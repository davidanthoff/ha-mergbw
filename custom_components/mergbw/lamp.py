"""Connection handling for a single MeRGBW sunset lamp."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime
import logging

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, IDLE_DISCONNECT_DELAY, NOTIFY_CHAR_UUID, WRITE_CHAR_UUID

_LOGGER = logging.getLogger(__name__)

type MeRGBWConfigEntry = ConfigEntry[Lamp]


class LampError(Exception):
    """The lamp could not be reached, or a write to it failed."""


class Lamp:
    """A sunset lamp, connected on demand.

    Every write takes a lock, connects if needed, and restarts an idle timer.
    When the timer runs out the connection is dropped again.
    """

    def __init__(self, hass: HomeAssistant, address: str, name: str) -> None:
        """Initialize the lamp."""
        self._hass = hass
        self.address = address
        self.name = name
        self._client: BleakClientWithServiceCache | None = None
        self._lock = asyncio.Lock()
        self._cancel_idle_timer: CALLBACK_TYPE | None = None

    @property
    def is_connected(self) -> bool:
        """Return whether a connection is currently open."""
        return self._client is not None and self._client.is_connected

    async def async_write(self, frames: Sequence[bytes]) -> None:
        """Send frames to the lamp, in order."""
        await self._async_send(frames, None)

    async def async_request(
        self, frames: Sequence[bytes], listen: float
    ) -> list[bytes]:
        """Send frames and return what the lamp notifies within ``listen`` seconds."""
        return await self._async_send(frames, listen)

    async def async_stop(self) -> None:
        """Cancel the idle timer and disconnect."""
        self._stop_idle_timer()
        async with self._lock:
            await self._async_disconnect()

    async def _async_send(
        self, frames: Sequence[bytes], listen: float | None
    ) -> list[bytes]:
        async with self._lock:
            self._stop_idle_timer()
            try:
                reused = self.is_connected
                try:
                    return await self._async_transfer(frames, listen)
                except (BleakError, TimeoutError) as err:
                    if not reused:
                        raise
                    # The connection may have gone stale while it sat idle.
                    _LOGGER.debug(
                        "%s: write on open connection failed (%s), reconnecting",
                        self.address,
                        err,
                    )
                    await self._async_disconnect()
                    return await self._async_transfer(frames, listen)
            except (BleakError, TimeoutError) as err:
                await self._async_disconnect()
                raise LampError(str(err) or type(err).__name__) from err
            finally:
                if self._client is not None:
                    self._start_idle_timer()

    async def _async_transfer(
        self, frames: Sequence[bytes], listen: float | None
    ) -> list[bytes]:
        client = await self._async_connect()
        received: list[bytes] = []
        if listen is not None:
            await client.start_notify(
                NOTIFY_CHAR_UUID, lambda _char, data: received.append(bytes(data))
            )
        try:
            for frame in frames:
                _LOGGER.debug("%s: writing %s", self.address, frame.hex())
                await client.write_gatt_char(WRITE_CHAR_UUID, frame)
            if listen is not None:
                await asyncio.sleep(listen)
        finally:
            if listen is not None and client.is_connected:
                with suppress(BleakError, TimeoutError):
                    await client.stop_notify(NOTIFY_CHAR_UUID)
        for data in received:
            _LOGGER.debug("%s: notified %s", self.address, data.hex())
        return received

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
        self._client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            self.name,
            disconnected_callback=self._on_disconnected,
            # Re-resolve on every attempt, so a retry can go through another proxy.
            ble_device_callback=lambda: self._ble_device() or device,
        )
        return self._client

    @callback
    def _on_disconnected(self, client: BleakClientWithServiceCache) -> None:
        _LOGGER.debug("%s: disconnected", self.address)
        if client is self._client:
            self._client = None

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
