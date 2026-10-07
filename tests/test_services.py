"""Tests for the send_raw action."""

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mergbw.const import DOMAIN

from .conftest import written


async def send_raw(hass: HomeAssistant, entry_id: str, response: bool, **data):
    """Call mergbw.send_raw."""
    return await hass.services.async_call(
        DOMAIN,
        "send_raw",
        {"config_entry_id": entry_id, **data},
        blocking=True,
        return_response=response,
    )


async def test_send_raw(
    hass: HomeAssistant, setup_entry: MockConfigEntry, client: MagicMock
) -> None:
    """A frame is sent verbatim."""
    await send_raw(hass, setup_entry.entry_id, False, frame="55 01 ff 06 01 a3")
    assert written(client) == ["5501ff0601a3"]
    client.start_notify.assert_not_called()


async def test_send_raw_with_checksum(
    hass: HomeAssistant, setup_entry: MockConfigEntry, client: MagicMock
) -> None:
    """The checksum can be appended."""
    await send_raw(
        hass, setup_entry.entry_id, False, frame="5501ff0601", add_checksum=True
    )
    assert written(client) == ["5501ff0601a2"]


async def test_send_raw_returns_notifications(
    hass: HomeAssistant, setup_entry: MockConfigEntry, client: MagicMock
) -> None:
    """With a response requested, notifications are collected and returned."""

    async def _start_notify(_uuid, handler) -> None:
        handler(None, bytearray.fromhex("5600ff0732"))

    client.start_notify.side_effect = _start_notify
    response = await send_raw(
        hass, setup_entry.entry_id, True, frame="5500ff05a5", listen=0
    )
    assert response == {"sent": "5500ff05a5", "notifications": ["5600ff0732"]}
    client.stop_notify.assert_called_once()


async def test_send_raw_invalid_frame(
    hass: HomeAssistant, setup_entry: MockConfigEntry, client: MagicMock
) -> None:
    """A frame that is not hex is rejected."""
    with pytest.raises(ServiceValidationError):
        await send_raw(hass, setup_entry.entry_id, False, frame="xyz")
    assert written(client) == []


async def test_send_raw_unknown_entry(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """An unknown config entry is rejected."""
    with pytest.raises(ServiceValidationError):
        await send_raw(hass, "nope", False, frame="00")


async def test_send_raw_unloaded_entry(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """An unloaded config entry is rejected."""
    await hass.config_entries.async_unload(setup_entry.entry_id)
    with pytest.raises(ServiceValidationError):
        await send_raw(hass, setup_entry.entry_id, False, frame="00")


async def test_send_raw_lamp_unreachable(
    hass: HomeAssistant, setup_entry: MockConfigEntry, ble_device: MagicMock
) -> None:
    """A lamp that cannot be reached raises HomeAssistantError."""
    ble_device.return_value = None
    with pytest.raises(HomeAssistantError):
        await send_raw(hass, setup_entry.entry_id, False, frame="00")
