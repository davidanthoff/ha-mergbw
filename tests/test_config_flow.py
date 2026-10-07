"""Tests for the config flow."""

from unittest.mock import patch

from homeassistant.components.bluetooth.match import (
    BluetoothMatcher,
    ble_device_matches,
)
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.loader import async_get_integration
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mergbw.const import DOMAIN

from . import LAMP_ADDRESS, LAMP_TITLE, service_info

OTHER_ADDRESS = "AA:BB:CC:DD:EE:02"

DISCOVERED = "custom_components.mergbw.config_flow.async_discovered_service_info"


@pytest.fixture(autouse=True)
def no_setup():
    """Do not set up the entry that a flow creates."""
    with patch("custom_components.mergbw.async_setup_entry", return_value=True):
        yield


async def test_manifest_matcher(hass: HomeAssistant) -> None:
    """The manifest matches lamps and not strips or other devices."""
    integration = await async_get_integration(hass, DOMAIN)
    matchers = [BluetoothMatcher(m) for m in integration.manifest["bluetooth"]]

    def matches(info) -> bool:
        return any(ble_device_matches(m, info) for m in matchers)

    assert matches(service_info())
    # MeRGBW strips share the service UUID but use another protocol.
    assert not matches(service_info(name="LED Lights"))
    assert not matches(service_info(service_uuids=[]))


async def test_bluetooth_discovery(hass: HomeAssistant) -> None:
    """A discovered lamp is confirmed and added."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"] == {
        "name": LAMP_TITLE,
        "address": LAMP_ADDRESS,
    }

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == LAMP_TITLE
    assert result["data"] == {CONF_ADDRESS: LAMP_ADDRESS}
    assert result["result"].unique_id == LAMP_ADDRESS


async def test_bluetooth_discovery_already_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A lamp that is already set up is not offered again."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_picks_discovered_lamp(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The user step lists discovered lamps that are not yet configured."""
    config_entry.add_to_hass(hass)
    with patch(
        DISCOVERED,
        return_value=[
            service_info(),
            service_info(address=OTHER_ADDRESS),
            service_info(address="AA:BB:CC:DD:EE:03", name="LED Lights"),
        ],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    selector = result["data_schema"].schema[CONF_ADDRESS]
    assert selector.config["options"] == [
        {"value": OTHER_ADDRESS, "label": f"Sunset lamp EE02 ({OTHER_ADDRESS})"}
    ]
    assert selector.config["custom_value"] is True

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: OTHER_ADDRESS}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Sunset lamp EE02"
    assert result["data"] == {CONF_ADDRESS: OTHER_ADDRESS}


@pytest.mark.parametrize(
    "typed", ["aa:bb:cc:dd:ee:01", " AA-BB-CC-DD-EE-01 ", "aabbccddee01"]
)
async def test_user_types_address(hass: HomeAssistant, typed: str) -> None:
    """A typed address is normalized, also without any discovered lamps."""
    with patch(DISCOVERED, return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: typed}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == LAMP_TITLE
    assert result["data"] == {CONF_ADDRESS: LAMP_ADDRESS}


async def test_user_invalid_address(hass: HomeAssistant) -> None:
    """An invalid address shows an error and lets the user try again."""
    with patch(DISCOVERED, return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: "not an address"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ADDRESS: "invalid_address"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: LAMP_ADDRESS}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_already_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Typing the address of a configured lamp aborts."""
    config_entry.add_to_hass(hass)
    with patch(DISCOVERED, return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: LAMP_ADDRESS.lower()}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
