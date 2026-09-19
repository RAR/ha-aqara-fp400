"""Base entity."""

from __future__ import annotations

from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .node import FP400Node


class FP400Entity(Entity):
    """Entity bound to one FP400 node."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, fp: FP400Node, key: str) -> None:
        """Set up identity and device link."""
        self.fp = fp
        self._attr_unique_id = f"{DOMAIN}_{fp.node_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = fp.device_info

    async def async_added_to_hass(self) -> None:
        """Follow node updates."""
        self.async_on_remove(self.fp.add_listener(self.async_write_ha_state))
