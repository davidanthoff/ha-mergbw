"""Frame builders for the MeRGBW sunset lamp protocol.

This module does no I/O. A frame looks like this:

    0x55  command  0xFF  length  payload...  checksum

where ``length`` is the length of the whole frame (payload + 5) and the
checksum is the one's complement of the byte sum with carries folded back in.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

HEADER = 0x55
REPLY_HEADER = 0x56
SEPARATOR = 0xFF

CMD_SYNC = 0x00
CMD_POWER = 0x01
CMD_COLOR = 0x03
CMD_BRIGHTNESS = 0x05


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


def percent_to_brightness(percent: int) -> int:
    """Map lamp percent 1-100 to Home Assistant brightness 1-255.

    Rounds down, so that brightness_to_percent maps the result back to the
    same percent.
    """
    if not 1 <= percent <= 100:
        raise ValueError(f"brightness percent out of range: {percent}")
    return max(1, percent * 255 // 100)


@dataclass(frozen=True)
class Status:
    """What the lamp reports in reply to a status request."""

    on: bool
    brightness_percent: int


def parse_status(data: bytes) -> Status | None:
    """Return the status in a notification, or None if it is something else.

    A status frame looks like ``56 00 ff 0f <power> <brightness %> 00 ... xx``.
    The color is not reported.
    """
    if (
        len(data) < 6
        or data[0] != REPLY_HEADER
        or data[1] != CMD_SYNC
        or data[2] != SEPARATOR
        or data[3] != len(data)
    ):
        return None
    return Status(on=data[4] != 0, brightness_percent=min(data[5], 100))
