"""Actions for the MeRGBW Sunset Lamp integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from . import protocol
from .const import DOMAIN
from .lamp import LampError, MeRGBWConfigEntry

SERVICE_SEND_RAW = "send_raw"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_FRAME = "frame"
ATTR_ADD_CHECKSUM = "add_checksum"
ATTR_LISTEN = "listen"

SEND_RAW_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_FRAME): cv.string,
        vol.Optional(ATTR_ADD_CHECKSUM, default=False): cv.boolean,
        vol.Optional(ATTR_LISTEN, default=2): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=30)
        ),
    }
)


def _parse_frame(text: str) -> bytes:
    cleaned = "".join(text.split()).replace(":", "").replace("-", "")
    cleaned = cleaned.removeprefix("0x")
    try:
        frame = bytes.fromhex(cleaned)
    except ValueError:
        frame = b""
    if not frame:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_frame",
            translation_placeholders={"frame": text},
        )
    return frame


def _get_entry(hass: HomeAssistant, entry_id: str) -> MeRGBWConfigEntry:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"name": entry.title},
        )
    return entry


async def _async_send_raw(call: ServiceCall) -> ServiceResponse:
    """Send a raw frame to a lamp, for working out the protocol."""
    entry = _get_entry(call.hass, call.data[ATTR_CONFIG_ENTRY_ID])
    frame = _parse_frame(call.data[ATTR_FRAME])
    if call.data[ATTR_ADD_CHECKSUM]:
        frame += bytes((protocol.checksum(frame),))
    lamp = entry.runtime_data
    try:
        if call.return_response:
            received = await lamp.async_request([frame], call.data[ATTR_LISTEN])
        else:
            await lamp.async_write([frame])
            received = []
    except LampError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="communication_error",
            translation_placeholders={"name": lamp.name, "error": str(err)},
        ) from err
    if not call.return_response:
        return None
    return {"sent": frame.hex(), "notifications": [data.hex() for data in received]}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_RAW,
        _async_send_raw,
        schema=SEND_RAW_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
