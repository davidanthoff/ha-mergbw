"""Tests for connection handling."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from bleak.exc import BleakError
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.mergbw.const import IDLE_DISCONNECT_DELAY

from .conftest import written
from .test_light import POWER_OFF, POWER_ON, turn_off, turn_on


async def idle(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float):
    """Let time pass and run what became due."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_connection_reused_then_dropped_when_idle(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    establish_connection: AsyncMock,
    client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Writes share a connection, which closes after the idle delay."""
    await turn_on(hass)
    await idle(hass, freezer, IDLE_DISCONNECT_DELAY - 1)
    await turn_off(hass)
    assert establish_connection.call_count == 1
    client.disconnect.assert_not_called()

    # The second write restarted the timer.
    await idle(hass, freezer, IDLE_DISCONNECT_DELAY - 1)
    client.disconnect.assert_not_called()
    await idle(hass, freezer, 2)
    client.disconnect.assert_called_once()
    lamp = setup_entry.runtime_data
    assert not lamp.is_connected

    await turn_on(hass)
    assert establish_connection.call_count == 2


async def test_device_resolved_through_callback(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    establish_connection: AsyncMock,
    ble_device: MagicMock,
) -> None:
    """The connector gets a callback that re-resolves the proxy path."""
    await turn_on(hass)
    kwargs = establish_connection.call_args.kwargs
    ble_device.reset_mock()
    assert kwargs["ble_device_callback"]() is ble_device.return_value
    ble_device.assert_called_once()

    # If the lamp drops out of sight mid-retry, fall back to the last device.
    first = establish_connection.call_args.args[1]
    ble_device.return_value = None
    assert kwargs["ble_device_callback"]() is first


async def test_stale_connection_is_retried(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    establish_connection: AsyncMock,
    client: MagicMock,
) -> None:
    """A write that fails on a reused connection reconnects and tries once more."""
    await turn_on(hass)
    written(client)

    client.write_gatt_char.side_effect = [BleakError("stale"), None]
    await turn_off(hass)
    assert establish_connection.call_count == 2
    assert written(client) == [POWER_OFF, POWER_OFF]


async def test_fresh_connection_is_not_retried(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    establish_connection: AsyncMock,
    client: MagicMock,
) -> None:
    """A write that fails right after connecting is not retried."""
    client.write_gatt_char.side_effect = BleakError("nope")
    with pytest.raises(HomeAssistantError):
        await turn_on(hass)
    assert establish_connection.call_count == 1
    assert written(client) == [POWER_ON]
    client.disconnect.assert_called_once()


async def test_lamp_disconnects_by_itself(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    establish_connection: AsyncMock,
    client: MagicMock,
) -> None:
    """If the lamp drops the connection, the next write reconnects."""
    await turn_on(hass)
    on_disconnected = establish_connection.call_args.kwargs["disconnected_callback"]
    client.is_connected = False
    on_disconnected(client)
    assert not setup_entry.runtime_data.is_connected

    await turn_off(hass)
    assert establish_connection.call_count == 2


async def test_unload_disconnects(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Unloading the entry closes the connection and stops the timer."""
    await turn_on(hass)
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    client.disconnect.assert_called_once()

    await idle(hass, freezer, IDLE_DISCONNECT_DELAY + 1)
    client.disconnect.assert_called_once()
