"""Config flow: one entry, discovers every FP400 on the Matter server."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class FP400ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-instance confirmation flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm and create the entry."""
        if not self.hass.config_entries.async_loaded_entries("matter"):
            return self.async_abort(reason="matter_not_loaded")
        if user_input is not None:
            return self.async_create_entry(title="Aqara FP400", data={})
        return self.async_show_form(step_id="user")
