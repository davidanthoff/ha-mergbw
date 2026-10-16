"""Connection handling for a single MeRGBW sunset lamp."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Sequence
from contextlib import suppress
from datetime import datetime, timedelta
import logging
from typing import Any

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from . import protocol
from .const import (
    DOMAIN,
    IDLE_DISCONNECT_DELAY,
    NOTIFY_CHAR_UUID,
    RECONNECT_DELAYS,
    STATUS_POLL_INTERVAL,
    WRITE_CHAR_UUID,
)

_LOGGER = logging.getLogger(__name__)

type MeRGBWConfigEntry = ConfigEntry[Lamp]
type StatusListener = Callable[[protocol.Status], None]

# Failed reconnects in a row before the lamp is reported as unreachable in the log.
FAILURES_BEFORE_WARNING = 3


class LampError(Exception):
    """The lamp could not be reached, or a write to it failed."""


class Lamp:
    """A sunset lamp.

    Every write takes a lock and connects if needed. Notifications are
    subscribed on every connection, and status replies go to the listeners.

    By default the connection is made on demand: each write restarts an idle
    timer, and when it runs out the connection is dropped again. With
    ``keep_connected`` the connection is opened at startup and kept open. It is
    re-established in the background when it drops, and the lamp is asked for
    its status after every write and every ``STATUS_POLL_INTERVAL`` seconds.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
        keep_connected: bool = False,
    ) -> None:
        """Initialize the lamp."""
        self._hass = hass
        self.address = address
        self.name = name
        self.keep_connected = keep_connected
        self._client: BleakClientWithServiceCache | None = None
        self._lock = asyncio.Lock()
        self._cancel_idle_timer: CALLBACK_TYPE | None = None
        self._status_listeners: list[StatusListener] = []
        self._collectors: list[list[bytes]] = []
        self._stopped = False
        self._failures = 0
        self._cancel_reconnect_timer: CALLBACK_TYPE | None = None
        self._cancel_poll_timer: CALLBACK_TYPE | None = None
        # Set while a reconnect or poll runs. A flag rather than the task alone,
        # because the task can finish before create_background_task returns.
        self._background_active = False
        self._background_task: asyncio.Task[None] | None = None

    @property
    def is_connected(self) -> bool:
        """Return whether a connection is currently open."""
        return self._client is not None and self._client.is_connected

    @callback
    def async_add_status_listener(self, listener: StatusListener) -> CALLBACK_TYPE:
        """Call ``listener`` with every status the lamp reports."""
        self._status_listeners.append(listener)

        @callback
        def _remove() -> None:
            self._status_listeners.remove(listener)

        return _remove

    @callback
    def async_start(self) -> None:
        """With keep_connected, connect in the background and start polling."""
        if not self.keep_connected:
            return
        self._cancel_poll_timer = async_track_time_interval(
            self._hass,
            self._on_poll_due,
            timedelta(seconds=STATUS_POLL_INTERVAL),
            name=f"{DOMAIN} status poll {self.address}",
        )
        self._schedule_reconnect()

    async def async_write(self, frames: Sequence[bytes]) -> None:
        """Send frames to the lamp, in order."""
        if self.keep_connected:
            # Ask for the result, so the state shown is what the lamp reports.
            frames = [*frames, protocol.sync_request()]
        await self._async_send(frames, None)

    async def async_request(
        self, frames: Sequence[bytes], listen: float
    ) -> list[bytes]:
        """Send frames and return what the lamp notifies within ``listen`` seconds."""
        return await self._async_send(frames, listen)

    async def async_stop(self) -> None:
        """Cancel timers and background work, and disconnect."""
        self._stopped = True
        self._stop_idle_timer()
        if self._cancel_poll_timer is not None:
            self._cancel_poll_timer()
            self._cancel_poll_timer = None
        if self._cancel_reconnect_timer is not None:
            self._cancel_reconnect_timer()
            self._cancel_reconnect_timer = None
        if (task := self._background_task) is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        async with self._lock:
            await self._async_disconnect()

    async def _async_send(
        self, frames: Sequence[bytes], listen: float | None
    ) -> list[bytes]:
        async with self._lock:
            self._stop_idle_timer()
            try:
                received = await self._async_transfer_retrying(frames, listen)
            except (BleakError, TimeoutError) as err:
                await self._async_disconnect()
                self._schedule_reconnect()
                raise LampError(str(err) or type(err).__name__) from err
            except LampError:
                self._schedule_reconnect()
                raise
            finally:
                if self._client is not None and not self.keep_connected:
                    self._start_idle_timer()
            self._on_success()
            return received

    async def _async_transfer_retrying(
        self, frames: Sequence[bytes], listen: float | None
    ) -> list[bytes]:
        reused = self.is_connected
        try:
            return await self._async_transfer(frames, listen)
        except (BleakError, TimeoutError) as err:
            if not reused:
                raise
            # The connection may have gone stale.
            _LOGGER.debug(
                "%s: write on open connection failed (%s), reconnecting",
                self.address,
                err,
            )
            await self._async_disconnect()
            return await self._async_transfer(frames, listen)

    async def _async_transfer(
        self, frames: Sequence[bytes], listen: float | None
    ) -> list[bytes]:
        client = await self._async_connect()
        received: list[bytes] = []
        if listen is not None:
            self._collectors.append(received)
        try:
            for frame in frames:
                _LOGGER.debug("%s: writing %s", self.address, frame.hex())
                await client.write_gatt_char(WRITE_CHAR_UUID, frame)
            if listen is not None:
                await asyncio.sleep(listen)
        finally:
            if listen is not None:
                self._collectors.remove(received)
        return received

    @callback
    def _on_success(self) -> None:
        """A write went through, so any pending reconnect is not needed."""
        if self._failures >= FAILURES_BEFORE_WARNING:
            _LOGGER.warning("%s: reachable again", self.name)
        self._failures = 0
        if self._cancel_reconnect_timer is not None:
            self._cancel_reconnect_timer()
            self._cancel_reconnect_timer = None

    def _ble_device(self) -> BLEDevice | None:
        return bluetooth.async_ble_device_from_address(
            self._hass, self.address, connectable=True
        )

    async def _async_connect(self) -> BleakClientWithServiceCache:
        if self._client is not None and self._client.is_connected:
            return self._client
        device = self._ble_device()
        if device is None:
            raise LampError(
                f"{self.address} is not in range of any connectable Bluetooth"
                " adapter or proxy"
            )
        _LOGGER.debug("%s: connecting", self.address)
        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            self.name,
            disconnected_callback=self._on_disconnected,
            # Re-resolve on every attempt, so a retry can go through another proxy.
            ble_device_callback=lambda: self._ble_device() or device,
        )
        self._client = client
        try:
            await client.start_notify(NOTIFY_CHAR_UUID, self._on_notification)
        except BleakError, TimeoutError:
            await self._async_disconnect()
            raise
        _LOGGER.debug("%s: connected", self.address)
        return client

    @callback
    def _on_notification(
        self, _char: BleakGATTCharacteristic | None, data: bytearray
    ) -> None:
        frame = bytes(data)
        _LOGGER.debug("%s: notified %s", self.address, frame.hex())
        for collector in self._collectors:
            collector.append(frame)
        if (status := protocol.parse_status(frame)) is None:
            return
        for listener in list(self._status_listeners):
            listener(status)

    @callback
    def _on_disconnected(self, client: BleakClientWithServiceCache) -> None:
        _LOGGER.debug("%s: disconnected", self.address)
        if client is self._client:
            self._client = None
            self._schedule_reconnect()

    async def _async_disconnect(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        _LOGGER.debug("%s: disconnecting", self.address)
        try:
            await client.disconnect()
        except (BleakError, TimeoutError) as err:
            _LOGGER.debug("%s: error while disconnecting: %s", self.address, err)

    @callback
    def _schedule_reconnect(self) -> None:
        """With keep_connected, reconnect after a delay that grows with failures."""
        if (
            not self.keep_connected
            or self._stopped
            or self._cancel_reconnect_timer is not None
            or self._background_active
        ):
            return
        delay = RECONNECT_DELAYS[min(self._failures, len(RECONNECT_DELAYS) - 1)]
        if delay == 0:
            self._run_in_background(self._async_reconnect(), "reconnect")
            return
        _LOGGER.debug("%s: reconnecting in %s s", self.address, delay)
        self._cancel_reconnect_timer = async_call_later(
            self._hass, delay, self._on_reconnect_due
        )

    @callback
    def _on_reconnect_due(self, _now: datetime) -> None:
        self._cancel_reconnect_timer = None
        if not self._background_active and not self._stopped:
            self._run_in_background(self._async_reconnect(), "reconnect")

    @callback
    def _on_poll_due(self, _now: datetime) -> None:
        if self._background_active or self._cancel_reconnect_timer is not None:
            return
        if self.is_connected:
            self._run_in_background(self._async_poll(), "status poll")
        else:
            # A reconnect is normally scheduled already; this is a safety net.
            self._schedule_reconnect()

    @callback
    def _run_in_background(self, coro: Coroutine[Any, Any, None], what: str) -> None:
        self._background_active = True
        task = self._hass.async_create_background_task(
            coro, f"{DOMAIN} {what} {self.address}"
        )
        self._background_task = None if task.done() else task

    async def _async_reconnect(self) -> None:
        try:
            await self._async_send([protocol.sync_request()], None)
        except LampError as err:
            self._failures += 1
            log = (
                _LOGGER.warning
                if self._failures == FAILURES_BEFORE_WARNING
                else _LOGGER.debug
            )
            log("%s: could not connect: %s", self.name, err)
        finally:
            self._background_active = False
            self._background_task = None
        if not self.is_connected:
            self._schedule_reconnect()

    async def _async_poll(self) -> None:
        try:
            await self._async_send([protocol.sync_request()], None)
        except LampError as err:
            _LOGGER.debug("%s: status poll failed: %s", self.address, err)
        finally:
            self._background_active = False
            self._background_task = None
        if not self.is_connected:
            self._schedule_reconnect()

    @callback
    def _start_idle_timer(self) -> None:
        self._stop_idle_timer()
        self._cancel_idle_timer = async_call_later(
            self._hass, IDLE_DISCONNECT_DELAY, self._on_idle_timeout
        )

    @callback
    def _stop_idle_timer(self) -> None:
        if self._cancel_idle_timer is not None:
            self._cancel_idle_timer()
            self._cancel_idle_timer = None

    @callback
    def _on_idle_timeout(self, _now: datetime) -> None:
        self._cancel_idle_timer = None
        self._hass.async_create_background_task(
            self._async_idle_disconnect(), f"{DOMAIN} idle disconnect {self.address}"
        )

    async def _async_idle_disconnect(self) -> None:
        async with self._lock:
            if self._cancel_idle_timer is not None:
                # A write came in while this task waited and restarted the timer.
                return
            await self._async_disconnect()
