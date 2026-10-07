"""Light platform for MeRGBW sunset lamps."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    EFFECT_OFF,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoredExtraData, RestoreEntity

from . import protocol
from .const import DOMAIN, MANUFACTURER, MODEL, RESEND_AFTER_POWER_ON
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

    The lamp does not report its state, so the state shown is the last one
    sent, restored across restarts.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_assumed_state = True
    _attr_should_poll = False
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = [EFFECT_OFF, *protocol.SCENES]

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
        self._attr_effect = EFFECT_OFF

    @property
    def color_mode(self) -> ColorMode:
        """Return the color mode.

        While a scene runs, the stored RGB color is not what the lamp shows.
        """
        if self._attr_effect != EFFECT_OFF:
            return ColorMode.BRIGHTNESS
        return ColorMode.RGB

    @property
    def extra_restore_state_data(self) -> RestoredExtraData:
        """Keep color, brightness and effect, even while the light is off."""
        return RestoredExtraData(
            {
                "brightness": self._attr_brightness,
                "rgb_color": list(self._attr_rgb_color)
                if self._attr_rgb_color is not None
                else None,
                "effect": self._attr_effect,
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
        effect = data.get("effect")
        if effect in protocol.SCENES:
            self._attr_effect = effect

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally changing color, brightness or effect."""
        rgb: tuple[int, int, int] | None = self._attr_rgb_color
        brightness: int | None = self._attr_brightness
        effect: str = self._attr_effect or EFFECT_OFF

        if ATTR_RGB_COLOR in kwargs:
            rgb = tuple(kwargs[ATTR_RGB_COLOR])
            effect = EFFECT_OFF
        if ATTR_EFFECT in kwargs:
            effect = kwargs[ATTR_EFFECT]
            if effect != EFFECT_OFF and effect not in protocol.SCENES:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_effect",
                    translation_placeholders={"effect": effect},
                )
        if ATTR_BRIGHTNESS in kwargs:
            brightness = kwargs[ATTR_BRIGHTNESS]

        content = self._content_frame(rgb, effect)
        level = (
            protocol.brightness(protocol.brightness_to_percent(brightness))
            if brightness is not None
            else None
        )

        frames: list[bytes] = []
        if not self._attr_is_on:
            # Stage the new look before powering on, so the lamp does not flash
            # its previous color first.
            staged = [f for f in (content, level) if f is not None]
            frames = [*staged, protocol.power(True)]
            if RESEND_AFTER_POWER_ON:
                frames += staged
        else:
            # Only send what changed.
            if content is not None and content != self._content_frame(
                self._attr_rgb_color, self._attr_effect or EFFECT_OFF
            ):
                frames.append(content)
            if level is not None and (
                self._attr_brightness is None
                or protocol.brightness_to_percent(self._attr_brightness)
                != protocol.brightness_to_percent(brightness)
            ):
                frames.append(level)
            if not frames:
                # The state is assumed, the remote may have turned the lamp off.
                frames.append(protocol.power(True))

        await self._async_write(frames)
        self._attr_is_on = True
        self._attr_rgb_color = rgb
        self._attr_brightness = brightness
        self._attr_effect = effect
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._async_write([protocol.power(False)])
        self._attr_is_on = False
        self.async_write_ha_state()

    @staticmethod
    def _content_frame(rgb: tuple[int, int, int] | None, effect: str) -> bytes | None:
        """Return the frame that sets the scene or the static color."""
        if effect != EFFECT_OFF:
            return protocol.scene(protocol.SCENES[effect])
        if rgb is not None:
            return protocol.color(*rgb)
        return None

    async def _async_write(self, frames: list[bytes]) -> None:
        try:
            await self._lamp.async_write(frames)
        except LampError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="communication_error",
                translation_placeholders={"name": self._lamp.name, "error": str(err)},
            ) from err
