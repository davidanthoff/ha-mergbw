"""The MeRGBW Sunset Lamp integration."""

from __future__ import annotations

from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .lamp import Lamp, MeRGBWConfigEntry
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.LIGHT]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration's actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MeRGBWConfigEntry) -> bool:
    """Set up a lamp from a config entry.

    The lamp is not contacted here. It may be unplugged or out of range, and
    connections are made on demand anyway.
    """
    lamp = Lamp(hass, entry.data[CONF_ADDRESS], entry.title)
    entry.runtime_data = lamp

    async def _async_stop(_event: Event) -> None:
        await lamp.async_stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MeRGBWConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
    return unload_ok
