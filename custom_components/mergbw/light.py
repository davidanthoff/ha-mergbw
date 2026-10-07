"""Light platform for MeRGBW sunset lamps."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoredExtraData, RestoreEntity

from . import protocol
from .const import DOMAIN, MANUFACTURER, MODEL, OFF_BRIGHTNESS_PERCENT
from .lamp import Lamp, LampError, MeRGBWConfigEntry

# Each lamp serializes its own writes; different lamps can work in parallel.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MeRGBWConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the light for a lamp."""
    async_add_entities([MeRGBWLight(entry.runtime_data)])


class MeRGBWLight(LightEntity, RestoreEntity):
    """A MeRGBW sunset lamp.

    The state shown is the last one sent, restored across restarts. The lamp
    can report power and brightness, but not color, so it is not read back.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_assumed_state = True
    _attr_should_poll = False
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    def __init__(self, lamp: Lamp) -> None:
        """Initialize the light."""
        self._lamp = lamp
        self._attr_unique_id = lamp.address
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, lamp.address)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=lamp.name,
        )
        self._attr_is_on = None
        self._attr_brightness = None
        self._attr_rgb_color = None

    @property
    def extra_restore_state_data(self) -> RestoredExtraData:
        """Keep color and brightness, even while the light is off."""
        return RestoredExtraData(
            {
                "brightness": self._attr_brightness,
                "rgb_color": list(self._attr_rgb_color)
                if self._attr_rgb_color is not None
                else None,
            }
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last known state."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state == STATE_ON:
                self._attr_is_on = True
            elif last_state.state == STATE_OFF:
                self._attr_is_on = False
        if (last_extra := await self.async_get_last_extra_data()) is None:
            return
        data = last_extra.as_dict()
        brightness = data.get("brightness")
        if isinstance(brightness, int) and 1 <= brightness <= 255:
            self._attr_brightness = brightness
        rgb = data.get("rgb_color")
        if (
            isinstance(rgb, list)
            and len(rgb) == 3
            and all(isinstance(c, int) and 0 <= c <= 255 for c in rgb)
        ):
            self._attr_rgb_color = (rgb[0], rgb[1], rgb[2])

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally changing color or brightness."""
        was_on = self._attr_is_on
        rgb: tuple[int, int, int] | None = self._attr_rgb_color
        brightness: int | None = self._attr_brightness
        if ATTR_RGB_COLOR in kwargs:
            rgb = tuple(kwargs[ATTR_RGB_COLOR])
        if ATTR_BRIGHTNESS in kwargs:
            brightness = kwargs[ATTR_BRIGHTNESS]
        elif brightness is None and not was_on:
            # Turning off leaves the lamp dimmed, so do not keep that level.
            brightness = 255

        # The lamp ignores color and brightness while it is off, so power comes
        # first. It then shows its last color at the dimmed level from
        # async_turn_off, until the color and brightness below arrive. Color goes
        # before brightness, so the lamp is never bright in the old color.
        frames: list[bytes] = []
        if not was_on:
            frames.append(protocol.power(True))
        if rgb is not None and (not was_on or rgb != self._attr_rgb_color):
            frames.append(protocol.color(*rgb))
        if brightness is not None and (
            not was_on
            or self._attr_brightness is None
            or protocol.brightness_to_percent(self._attr_brightness)
            != protocol.brightness_to_percent(brightness)
        ):
            frames.append(
                protocol.brightness(protocol.brightness_to_percent(brightness))
            )
        if not frames:
            # The state is assumed, the remote may have turned the lamp off.
            frames.append(protocol.power(True))

        await self._async_write(frames)
        self._attr_is_on = True
        self._attr_rgb_color = rgb
        self._attr_brightness = brightness
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off, dimming it first.

        The lamp powers up at the brightness it had when it was turned off, and
        ignores commands while off. Dimming it here keeps the next turn-on from
        flashing the old look at full brightness.
        """
        await self._async_write(
            [protocol.brightness(OFF_BRIGHTNESS_PERCENT), protocol.power(False)]
        )
        self._attr_is_on = False
        self.async_write_ha_state()

    async def _async_write(self, frames: list[bytes]) -> None:
        try:
            await self._lamp.async_write(frames)
        except LampError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="communication_error",
                translation_placeholders={"name": self._lamp.name, "error": str(err)},
            ) from err
