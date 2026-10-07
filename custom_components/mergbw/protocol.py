"""Frame builders for the MeRGBW sunset lamp protocol.

This module does no I/O. A frame looks like this:

    0x55  command  0xFF  length  payload...  checksum

where ``length`` is the length of the whole frame (payload + 5) and the
checksum is the one's complement of the byte sum with carries folded back in.
"""

from __future__ import annotations

import math

HEADER = 0x55
SEPARATOR = 0xFF

CMD_SYNC = 0x00
CMD_POWER = 0x01
CMD_COLOR = 0x03
CMD_BRIGHTNESS = 0x05
CMD_SCENE = 0x06

SCENE_FIRST_ID = 0x80
SCENE_LAST_ID = 0x93

# Scene IDs that have been checked against a real lamp. The phone app shows
# twenty scenes in 0x80-0x93; the others are named "Scene N" by position until
# someone maps them.
_VERIFIED_SCENES = {
    0x81: "Green Prairie",
    0x84: "Ghost",
    0x87: "Disco",
    0x88: "Alarm",
    0x8B: "Savanah",
}

SCENES: dict[str, int] = {
    _VERIFIED_SCENES.get(scene_id, f"Scene {scene_id - SCENE_FIRST_ID + 1}"): scene_id
    for scene_id in range(SCENE_FIRST_ID, SCENE_LAST_ID + 1)
}


def checksum(data: bytes) -> int:
    """Return the checksum byte for the given frame prefix."""
    total = sum(data)
    while total > 0xFF:
        total = (total >> 8) + (total & 0xFF)
    return ~total & 0xFF


def build_frame(command: int, payload: bytes = b"") -> bytes:
    """Build a complete frame for a command and its payload."""
    if not 0 <= command <= 0xFF:
        raise ValueError(f"command out of range: {command}")
    body = bytes((HEADER, command, SEPARATOR, len(payload) + 5)) + payload
    return body + bytes((checksum(body),))


def power(on: bool) -> bytes:
    """Frame that switches the lamp on or off."""
    return build_frame(CMD_POWER, bytes((1 if on else 0,)))


def color(red: int, green: int, blue: int) -> bytes:
    """Frame that sets a static RGB color."""
    return build_frame(CMD_COLOR, bytes((red, green, blue)))


def brightness(percent: int) -> bytes:
    """Frame that sets the brightness in percent (0-100)."""
    if not 0 <= percent <= 100:
        raise ValueError(f"brightness percent out of range: {percent}")
    return build_frame(CMD_BRIGHTNESS, bytes((percent,)))


def scene(scene_id: int) -> bytes:
    """Frame that starts a built-in scene."""
    return build_frame(CMD_SCENE, bytes((scene_id,)))


def sync_request() -> bytes:
    """Frame that asks the lamp to report its status on the notify characteristic."""
    return build_frame(CMD_SYNC)


def brightness_to_percent(value: int) -> int:
    """Map Home Assistant brightness 1-255 to lamp percent 1-100.

    Rounds up, so that every non-zero brightness stays above 0 %.
    """
    if not 1 <= value <= 255:
        raise ValueError(f"brightness out of range: {value}")
    return math.ceil(value * 100 / 255)
