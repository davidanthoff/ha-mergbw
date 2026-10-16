"""Tests for keeping the connection open."""

from collections.abc import Callable
from datetime import timedelta
import logging
from unittest.mock import AsyncMock, MagicMock

from bleak.exc import BleakError
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.const import ATTR_ASSUMED_STATE, CONF_ADDRESS, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.mergbw import protocol
from custom_components.mergbw.const import (
    CONF_KEEP_CONNECTED,
    DOMAIN,
    IDLE_DISCONNECT_DELAY,
    STATUS_POLL_INTERVAL,
)

from . import LAMP_ADDRESS, LAMP_TITLE
from .conftest import written
from .test_light import (
    BRIGHTNESS_1,
    BRIGHTNESS_100,
    ENTITY_ID,
    POWER_OFF,
    POWER_ON,
    turn_off,
    turn_on,
)

STATUS_REQUEST = protocol.sync_request().hex()


def status_frame(on: bool, percent: int) -> bytearray:
    """A status notification as the lamp sends it."""
    return bytearray(bytes((0x56, 0x00, 0xFF, 0x0F, int(on), percent)) + bytes(9))


class FakeLamp:
    """Answers status requests with a power and brightness that tests can change."""

    def __init__(self, client: MagicMock) -> None:
        self.client = client
        self.on = False
        self.percent = 1
        client.write_gatt_char.side_effect = self._write

    def notify(self, data: bytearray) -> None:
        handler: Callable = self.client.start_notify.call_args.args[1]
        handler(None, data)

    async def _write(self, _uuid: str, frame: bytes) -> None:
        if frame == protocol.power(True):
            self.on = True
        elif frame == protocol.power(False):
            self.on = False
        elif frame[1] == protocol.CMD_BRIGHTNESS and self.on:
            self.percent = frame[4]
        elif frame == protocol.sync_request():
            self.notify(status_frame(self.on, self.percent))


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float):
    """Let time pass and run what became due."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.fixture
def fake_lamp(client: MagicMock) -> FakeLamp:
    """A lamp that answers status requests."""
    return FakeLamp(client)


@pytest.fixture
async def keep_entry(
    hass: HomeAssistant, establish_connection: AsyncMock, fake_lamp: FakeLamp
) -> MockConfigEntry:
    """Set up one lamp with keep_connected."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=LAMP_TITLE,
        unique_id=LAMP_ADDRESS,
        data={CONF_ADDRESS: LAMP_ADDRESS},
        options={CONF_KEEP_CONNECTED: True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_connects_at_startup_and_reads_status(
    hass: HomeAssistant,
    keep_entry: MockConfigEntry,
    establish_connection: AsyncMock,
    client: MagicMock,
) -> None:
    """The connection is opened right away and the state is not assumed."""
    assert establish_connection.call_count == 1
    assert written(client) == [STATUS_REQUEST]
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_OFF
    assert ATTR_ASSUMED_STATE not in state.attributes


async def test_writes_ask_for_status_and_stay_connected(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    keep_entry: MockConfigEntry,
    establish_connection: AsyncMock,
    client: MagicMock,
) -> None:
    """Every write ends with a status request, and the idle timer is not used."""
    written(client)
    await turn_on(hass)
    assert written(client) == [POWER_ON, BRIGHTNESS_100, STATUS_REQUEST]
    assert hass.states.get(ENTITY_ID).state == STATE_ON

    await tick(hass, freezer, IDLE_DISCONNECT_DELAY + 1)
    client.disconnect.assert_not_called()
    await turn_off(hass)
    assert written(client) == [BRIGHTNESS_1, POWER_OFF, STATUS_REQUEST]
    assert establish_connection.call_count == 1
    assert hass.states.get(ENTITY_ID).state == STATE_OFF


async def test_poll_picks_up_changes_from_the_remote(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    keep_entry: MockConfigEntry,
    client: MagicMock,
    fake_lamp: FakeLamp,
) -> None:
    """Changes made outside Home Assistant show up at the next poll."""
    await turn_on(hass, brightness=255)
    written(client)

    # Someone turns the lamp off with the remote. It still reports its last
    # brightness; the dimmed level after a turn-off from here is covered too.
    fake_lamp.on = False
    fake_lamp.percent = 1
    await tick(hass, freezer, STATUS_POLL_INTERVAL)
    assert written(client) == [STATUS_REQUEST]
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_OFF
    # The reported 1 % while off does not replace the brightness to come back to.
    assert state.attributes.get(ATTR_BRIGHTNESS) is None
    await turn_on(hass)
    assert written(client) == [POWER_ON, BRIGHTNESS_100, STATUS_REQUEST]

    # And on again with the remote, at 40 %.
    fake_lamp.on = True
    fake_lamp.percent = 40
    await tick(hass, freezer, STATUS_POLL_INTERVAL)
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 102
    assert protocol.brightness_to_percent(102) == 40


async def test_unsolicited_status_is_applied(
    hass: HomeAssistant, keep_entry: MockConfigEntry, fake_lamp: FakeLamp
) -> None:
    """A status the lamp sends by itself also updates the entity."""
    fake_lamp.notify(status_frame(True, 100))
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 255

    # Acknowledgements and other frames are ignored.
    fake_lamp.notify(bytearray.fromhex("5601ff060000"))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_ON


async def test_reconnects_when_connection_drops(
    hass: HomeAssistant,
    keep_entry: MockConfigEntry,
    establish_connection: AsyncMock,
    client: MagicMock,
) -> None:
    """A dropped connection is re-established right away."""
    written(client)
    on_disconnected = establish_connection.call_args.kwargs["disconnected_callback"]
    client.is_connected = False
    on_disconnected(client)
    await hass.async_block_till_done()
    assert establish_connection.call_count == 2
    assert keep_entry.runtime_data.is_connected
    assert written(client) == [STATUS_REQUEST]


async def test_reconnect_backs_off_and_warns(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    keep_entry: MockConfigEntry,
    establish_connection: AsyncMock,
    client: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failed reconnects wait longer each time, and the third one is logged."""
    connect = establish_connection.side_effect
    establish_connection.side_effect = BleakError("out of range")
    on_disconnected = establish_connection.call_args.kwargs["disconnected_callback"]
    client.is_connected = False
    on_disconnected(client)
    await hass.async_block_till_done()
    assert establish_connection.call_count == 2  # immediate attempt, failed

    await tick(hass, freezer, 4)
    assert establish_connection.call_count == 2
    await tick(hass, freezer, 1)
    assert establish_connection.call_count == 3  # after 5 s

    with caplog.at_level(logging.WARNING):
        await tick(hass, freezer, 15)
    assert establish_connection.call_count == 4  # after 15 s more
    assert "could not connect: out of range" in caplog.text

    # The lamp is back. The next attempt, 30 s later, succeeds.
    establish_connection.side_effect = connect
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        await tick(hass, freezer, 30)
    assert establish_connection.call_count == 5
    assert keep_entry.runtime_data.is_connected
    assert "reachable again" in caplog.text


async def test_command_while_disconnected_connects_now(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    keep_entry: MockConfigEntry,
    establish_connection: AsyncMock,
    client: MagicMock,
) -> None:
    """A command does not wait for the next reconnect attempt."""
    connect = establish_connection.side_effect
    establish_connection.side_effect = BleakError("out of range")
    on_disconnected = establish_connection.call_args.kwargs["disconnected_callback"]
    client.is_connected = False
    on_disconnected(client)
    await hass.async_block_till_done()
    assert establish_connection.call_count == 2

    establish_connection.side_effect = connect
    await turn_on(hass)
    assert establish_connection.call_count == 3
    assert hass.states.get(ENTITY_ID).state == STATE_ON

    # The pending reconnect was cancelled, since the connection is back.
    await tick(hass, freezer, 60)
    assert establish_connection.call_count == 3


async def test_unload_stops_reconnecting_and_polling(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    keep_entry: MockConfigEntry,
    establish_connection: AsyncMock,
    client: MagicMock,
) -> None:
    """After unloading, the lamp is left alone."""
    assert await hass.config_entries.async_unload(keep_entry.entry_id)
    await hass.async_block_till_done()
    client.disconnect.assert_called_once()
    written(client)

    await tick(hass, freezer, STATUS_POLL_INTERVAL * 3)
    assert written(client) == []
    assert establish_connection.call_count == 1


async def test_startup_without_lamp_in_range(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    establish_connection: AsyncMock,
    ble_device: MagicMock,
) -> None:
    """Setup succeeds when the lamp is out of reach, and it connects later."""
    device = ble_device.return_value
    ble_device.return_value = None
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=LAMP_TITLE,
        unique_id=LAMP_ADDRESS,
        data={CONF_ADDRESS: LAMP_ADDRESS},
        options={CONF_KEEP_CONNECTED: True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    establish_connection.assert_not_called()

    ble_device.return_value = device
    await tick(hass, freezer, 5)
    assert establish_connection.call_count == 1
    assert entry.runtime_data.is_connected
