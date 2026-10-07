"""Fixtures for the MeRGBW Sunset Lamp tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

from bleak.backends.device import BLEDevice
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mergbw.const import DOMAIN

from . import LAMP_ADDRESS, LAMP_TITLE


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom integrations in every test."""


@pytest.fixture(autouse=True)
def auto_mock_bluetooth(mock_bluetooth: None) -> None:
    """Keep the real Bluetooth stack from starting."""


@pytest.fixture
def ble_device() -> Generator[MagicMock]:
    """Make the lamp resolvable through a connectable adapter."""
    with patch(
        "homeassistant.components.bluetooth.async_ble_device_from_address",
        return_value=BLEDevice(LAMP_ADDRESS, "Sunset lights", {}),
    ) as mock:
        yield mock


@pytest.fixture
def client() -> MagicMock:
    """A connected Bleak client."""
    client = MagicMock()
    client.is_connected = True
    client.write_gatt_char = AsyncMock()
    client.start_notify = AsyncMock()
    client.stop_notify = AsyncMock()

    async def _disconnect() -> None:
        client.is_connected = False

    client.disconnect = AsyncMock(side_effect=_disconnect)
    return client


@pytest.fixture
def establish_connection(
    ble_device: MagicMock, client: MagicMock
) -> Generator[AsyncMock]:
    """Patch connecting to return the mock client."""

    async def _connect(*args, **kwargs) -> MagicMock:
        client.is_connected = True
        return client

    with patch(
        "custom_components.mergbw.lamp.establish_connection",
        AsyncMock(side_effect=_connect),
    ) as mock:
        yield mock


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A config entry for one lamp."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=LAMP_TITLE,
        unique_id=LAMP_ADDRESS,
        data={CONF_ADDRESS: LAMP_ADDRESS},
    )


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry, establish_connection: AsyncMock
) -> MockConfigEntry:
    """Set up the integration with one lamp."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


def written(client: MagicMock) -> list[str]:
    """Return the frames written so far, hex encoded, and reset the mock."""
    frames = [call.args[1].hex() for call in client.write_gatt_char.call_args_list]
    client.write_gatt_char.reset_mock()
    return frames
