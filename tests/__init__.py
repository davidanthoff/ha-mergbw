"""Tests for the MeRGBW Sunset Lamp integration."""

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

LAMP_ADDRESS = "AA:BB:CC:DD:EE:01"
LAMP_TITLE = "Sunset lamp EE01"
LAMP_SERVICE_UUID = "00003519-0000-1000-8000-00805f9b34fb"


def service_info(
    address: str = LAMP_ADDRESS,
    name: str = "Sunset lights",
    service_uuids: list[str] | None = None,
) -> BluetoothServiceInfoBleak:
    """Build an advertisement as the lamp sends it."""
    if service_uuids is None:
        service_uuids = [LAMP_SERVICE_UUID]
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=service_uuids,
        source="local",
        device=BLEDevice(address, name, {}),
        advertisement=AdvertisementData(
            local_name=name,
            manufacturer_data={},
            service_data={},
            service_uuids=service_uuids,
            tx_power=-127,
            rssi=-60,
            platform_data=(),
        ),
        connectable=True,
        time=0,
        tx_power=None,
    )
