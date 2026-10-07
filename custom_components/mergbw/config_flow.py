"""Config flow for MeRGBW sunset lamps."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
import voluptuous as vol

from .const import ADVERTISED_SERVICE_UUID, DOMAIN, LOCAL_NAME_PREFIX

_MAC_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")


def is_sunset_lamp(info: BluetoothServiceInfoBleak) -> bool:
    """Return whether an advertisement comes from a sunset lamp."""
    return (info.name or "").startswith(LOCAL_NAME_PREFIX) and (
        ADVERTISED_SERVICE_UUID in info.service_uuids
    )


def lamp_title(address: str) -> str:
    """Name a lamp after the end of its address, e.g. "Sunset lamp A1B2"."""
    return f"Sunset lamp {address.replace(':', '')[-4:].upper()}"


def normalize_address(address: str) -> str | None:
    """Return the address as upper-case AA:BB:CC:DD:EE:FF, or None if invalid."""
    address = address.strip().upper().replace("-", ":")
    if re.fullmatch(r"[0-9A-F]{12}", address):
        address = ":".join(address[i : i + 2] for i in range(0, 12, 2))
    return address if _MAC_RE.match(address) else None


class MeRGBWConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MeRGBW sunset lamps."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._address: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a lamp found by Bluetooth discovery."""
        address = discovery_info.address.upper()
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()
        self._address = address
        self.context["title_placeholders"] = {"name": lamp_title(address)}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setting up a discovered lamp."""
        assert self._address is not None
        title = lamp_title(self._address)
        if user_input is not None:
            return self.async_create_entry(
                title=title, data={CONF_ADDRESS: self._address}
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": title, "address": self._address},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a discovered lamp, or type the address of one."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = normalize_address(user_input[CONF_ADDRESS])
            if address is None:
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=lamp_title(address), data={CONF_ADDRESS: address}
                )

        configured = self._async_current_ids(include_ignore=False)
        options: list[SelectOptionDict] = []
        seen: set[str] = set()
        for info in async_discovered_service_info(self.hass, connectable=True):
            address = info.address.upper()
            if address in configured or address in seen or not is_sunset_lamp(info):
                continue
            seen.add(address)
            options.append(
                SelectOptionDict(
                    value=address, label=f"{lamp_title(address)} ({address})"
                )
            )

        if options:
            field: Any = SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    custom_value=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        else:
            field = TextSelector()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): field}),
            errors=errors,
        )
