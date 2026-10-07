"""Constants for the MeRGBW Sunset Lamp integration."""

from typing import Final

DOMAIN: Final = "mergbw"

MANUFACTURER: Final = "MeRGBW"
MODEL: Final = "Sunset lamp"

# Advertisement of the sunset lamps. MeRGBW LED strips advertise the same
# service UUID but speak a different protocol, so the name has to match too.
ADVERTISED_SERVICE_UUID: Final = "00003519-0000-1000-8000-00805f9b34fb"
LOCAL_NAME_PREFIX: Final = "Sunset"

WRITE_CHAR_UUID: Final = "0000fff3-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR_UUID: Final = "0000fff4-0000-1000-8000-00805f9b34fb"

# Drop the connection after this many seconds without a write. Bluetooth
# proxies have few connection slots, and the lamp accepts only one connection,
# which would otherwise lock out the phone app.
IDLE_DISCONNECT_DELAY: Final = 10

# The lamp ignores color and brightness while it is off, and powers up at the
# brightness it had when it was turned off. Turning off therefore dims it to
# this level first, so the next turn-on starts nearly dark instead of flashing.
OFF_BRIGHTNESS_PERCENT: Final = 1
