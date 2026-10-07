"""Tests for the light entity."""

from unittest.mock import AsyncMock, MagicMock

from bleak.exc import BleakError
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_EFFECT_LIST,
    ATTR_RGB_COLOR,
    ATTR_SUPPORTED_COLOR_MODES,
    DOMAIN as LIGHT_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    ColorMode,
)
from homeassistant.const import (
    ATTR_ASSUMED_STATE,
    ATTR_ENTITY_ID,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.script import Script
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.mergbw import protocol
from custom_components.mergbw.const import DOMAIN

from . import LAMP_ADDRESS
from .conftest import written

ENTITY_ID = "light.sunset_lamp_ee01"

POWER_ON = "5501ff0601a2"
POWER_OFF = "5501ff0600a3"
COLOR_SUNSET = "5503ff08ff8c28ea"  # (255, 140, 40)
BRIGHTNESS_100 = "5505ff06643b"
BRIGHTNESS_1 = "5505ff06019e"


def color_frame(r: int, g: int, b: int) -> str:
    """Hex frame for a color."""
    return protocol.color(r, g, b).hex()


def brightness_frame(percent: int) -> str:
    """Hex frame for a brightness percent."""
    return protocol.brightness(percent).hex()


async def turn_on(hass: HomeAssistant, **data) -> None:
    """Call light.turn_on on the lamp."""
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID, **data},
        blocking=True,
    )


async def turn_off(hass: HomeAssistant) -> None:
    """Call light.turn_off on the lamp."""
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )


async def test_device_and_entity(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """The lamp gets a device, and the entity takes the device's name."""
    entity = entity_registry.async_get(ENTITY_ID)
    assert entity is not None
    assert entity.unique_id == LAMP_ADDRESS
    device = device_registry.async_get(entity.device_id)
    assert device is not None
    assert device.connections == {(dr.CONNECTION_BLUETOOTH, LAMP_ADDRESS)}
    assert device.manufacturer == "MeRGBW"
    assert device.model == "Sunset lamp"
    assert device.name == "Sunset lamp EE01"

    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_UNKNOWN
    assert state.attributes[ATTR_ASSUMED_STATE] is True
    assert state.attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.RGB]
    assert ATTR_EFFECT_LIST not in state.attributes


async def test_setup_does_not_connect(
    hass: HomeAssistant, setup_entry: MockConfigEntry, establish_connection: AsyncMock
) -> None:
    """Setting up does not contact the lamp."""
    establish_connection.assert_not_called()


async def test_turn_on_from_off(
    hass: HomeAssistant, setup_entry: MockConfigEntry, client: MagicMock
) -> None:
    """Off to on sends power first, then color, then brightness."""
    await turn_on(hass, rgb_color=(255, 140, 40), brightness=255)
    assert written(client) == [POWER_ON, COLOR_SUNSET, BRIGHTNESS_100]
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_RGB_COLOR] == (255, 140, 40)
    assert state.attributes[ATTR_BRIGHTNESS] == 255
    assert state.attributes[ATTR_COLOR_MODE] == ColorMode.RGB


async def test_turn_on_without_known_state(
    hass: HomeAssistant, setup_entry: MockConfigEntry, client: MagicMock
) -> None:
    """With nothing known, turning on restores full brightness, not the dim off level."""
    await turn_on(hass)
    assert written(client) == [POWER_ON, BRIGHTNESS_100]
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 255


async def test_turn_on_when_on_sends_only_changes(
    hass: HomeAssistant, setup_entry: MockConfigEntry, client: MagicMock
) -> None:
    """On to on sends only what changed, or power when nothing changed."""
    await turn_on(hass, rgb_color=(255, 140, 40), brightness=255)
    written(client)

    await turn_on(hass, brightness=128)
    assert written(client) == [brightness_frame(51)]

    await turn_on(hass, rgb_color=(255, 30, 0), brightness=128)
    assert written(client) == [color_frame(255, 30, 0)]

    await turn_on(hass, rgb_color=(255, 100, 10), brightness=200)
    assert written(client) == [color_frame(255, 100, 10), brightness_frame(79)]

    # 201 is 79 % as well, so nothing needs to be sent but power.
    await turn_on(hass, brightness=201)
    assert written(client) == [POWER_ON]
    assert hass.states.get(ENTITY_ID).attributes[ATTR_BRIGHTNESS] == 201


async def test_turn_off_dims_first(
    hass: HomeAssistant, setup_entry: MockConfigEntry, client: MagicMock
) -> None:
    """Turning off dims to 1 % before powering off; turning on restores the look."""
    await turn_on(hass, rgb_color=(255, 140, 40), brightness=255)
    written(client)

    await turn_off(hass)
    assert written(client) == [BRIGHTNESS_1, POWER_OFF]
    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_OFF

    # Turning on again without arguments brings back the remembered brightness.
    await turn_on(hass)
    assert written(client) == [POWER_ON, COLOR_SUNSET, BRIGHTNESS_100]
    assert hass.states.get(ENTITY_ID).attributes[ATTR_BRIGHTNESS] == 255

    await turn_off(hass)
    written(client)
    await turn_on(hass, rgb_color=(255, 30, 0), brightness=3)
    assert written(client) == [POWER_ON, color_frame(255, 30, 0), brightness_frame(2)]


async def test_lowest_brightness_is_not_zero(
    hass: HomeAssistant, setup_entry: MockConfigEntry, client: MagicMock
) -> None:
    """Brightness 1 is sent as 1 %, not 0 %."""
    await turn_on(hass, brightness=1)
    assert written(client) == [POWER_ON, BRIGHTNESS_1]


async def test_color_temperature_is_converted(
    hass: HomeAssistant, setup_entry: MockConfigEntry, client: MagicMock
) -> None:
    """Color temperature arrives as RGB."""
    await turn_on(hass, color_temp_kelvin=2200, brightness=10)
    state = hass.states.get(ENTITY_ID)
    rgb = state.attributes[ATTR_RGB_COLOR]
    assert rgb[0] == 255
    assert rgb[2] < rgb[1] < 255
    assert written(client)[1] == color_frame(*rgb)


async def test_write_error_raises_home_assistant_error(
    hass: HomeAssistant, setup_entry: MockConfigEntry, client: MagicMock
) -> None:
    """Bleak errors surface as HomeAssistantError, so continue_on_error works."""
    client.write_gatt_char.side_effect = BleakError("boom")
    with pytest.raises(HomeAssistantError) as exc_info:
        await turn_on(hass, brightness=255)
    assert exc_info.value.translation_key == "communication_error"
    assert hass.states.get(ENTITY_ID).state == STATE_UNKNOWN


async def test_connect_error_raises_home_assistant_error(
    hass: HomeAssistant, setup_entry: MockConfigEntry, establish_connection: AsyncMock
) -> None:
    """Failing to connect surfaces as HomeAssistantError."""
    establish_connection.side_effect = BleakError("no slot")
    with pytest.raises(HomeAssistantError):
        await turn_off(hass)


async def test_lamp_not_found_raises_home_assistant_error(
    hass: HomeAssistant, setup_entry: MockConfigEntry, ble_device: MagicMock
) -> None:
    """A lamp that no adapter can see surfaces as HomeAssistantError."""
    ble_device.return_value = None
    with pytest.raises(HomeAssistantError):
        await turn_on(hass)
    assert hass.states.get(ENTITY_ID).state == STATE_UNKNOWN


async def test_continue_on_error_in_script(
    hass: HomeAssistant, setup_entry: MockConfigEntry, ble_device: MagicMock
) -> None:
    """A script step with continue_on_error carries on when the lamp is gone."""
    ble_device.return_value = None
    script = Script(
        hass,
        [
            {
                "action": "light.turn_on",
                "target": {"entity_id": ENTITY_ID},
                "continue_on_error": True,
            },
            {"event": "after_lamp"},
        ],
        "test",
        DOMAIN,
    )
    events = []
    hass.bus.async_listen("after_lamp", events.append)
    await script.async_run(context=None)
    await hass.async_block_till_done()
    assert len(events) == 1


async def test_restore_state(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    establish_connection: AsyncMock,
    client: MagicMock,
) -> None:
    """On/off, color and brightness survive a restart."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(ENTITY_ID, STATE_OFF),
                # "effect" is left over from 0.1.0, which had scenes.
                {"brightness": 77, "rgb_color": [255, 30, 0], "effect": "Disco"},
            )
        ],
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == STATE_OFF
    await turn_on(hass)
    assert written(client) == [
        POWER_ON,
        color_frame(255, 30, 0),
        brightness_frame(31),
    ]
    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_BRIGHTNESS] == 77
    assert state.attributes[ATTR_RGB_COLOR] == (255, 30, 0)


async def test_restore_ignores_bad_data(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    establish_connection: AsyncMock,
) -> None:
    """Garbage in the restore cache is ignored."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(ENTITY_ID, "unavailable"),
                {"brightness": 0, "rgb_color": [1, 2]},
            )
        ],
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_UNKNOWN
