"""Tests for the frame builders."""

import pytest

from custom_components.mergbw import protocol


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        (protocol.power(True), "5501ff0601a2"),
        (protocol.power(False), "5501ff0600a3"),
        (protocol.color(255, 140, 40), "5503ff08ff8c28ea"),
        (protocol.brightness(100), "5505ff06643b"),
        (protocol.scene(0x81), "5506ff06811d"),
        (protocol.sync_request(), "5500ff05a5"),
    ],
)
def test_frames(frame: bytes, expected: str) -> None:
    """Frames match the vectors worked out by hand."""
    assert frame.hex() == expected


def test_checksum_folds_carries() -> None:
    """The checksum folds carries back in instead of masking them off."""
    data = bytes.fromhex("5501ff0601")
    assert sum(data) & 0xFF == 0x5C
    assert protocol.checksum(data) == 0xA2


def test_length_counts_whole_frame() -> None:
    """The length byte counts header, command, separator, length and checksum."""
    frame = protocol.build_frame(0x42, b"\x01\x02\x03\x04")
    assert frame[3] == len(frame) == 9


@pytest.mark.parametrize(
    ("value", "percent"),
    [(1, 1), (2, 1), (3, 2), (128, 51), (254, 100), (255, 100)],
)
def test_brightness_to_percent(value: int, percent: int) -> None:
    """Non-zero brightness never maps to 0 %."""
    assert protocol.brightness_to_percent(value) == percent


@pytest.mark.parametrize("value", [0, 256])
def test_brightness_to_percent_out_of_range(value: int) -> None:
    """Brightness outside 1-255 is rejected."""
    with pytest.raises(ValueError, match="out of range"):
        protocol.brightness_to_percent(value)


def test_brightness_frame_rejects_out_of_range() -> None:
    """Percent outside 0-100 is rejected."""
    with pytest.raises(ValueError, match="out of range"):
        protocol.brightness(101)


def test_scenes() -> None:
    """All twenty scene IDs have a unique name, verified ones by their app name."""
    assert len(protocol.SCENES) == 20
    assert sorted(protocol.SCENES.values()) == list(range(0x80, 0x94))
    assert protocol.SCENES["Green Prairie"] == 0x81
    assert protocol.SCENES["Ghost"] == 0x84
    assert protocol.SCENES["Disco"] == 0x87
    assert protocol.SCENES["Alarm"] == 0x88
    assert protocol.SCENES["Savanah"] == 0x8B
    assert protocol.SCENES["Scene 1"] == 0x80
    assert protocol.SCENES["Scene 20"] == 0x93
