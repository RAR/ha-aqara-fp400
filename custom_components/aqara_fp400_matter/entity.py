"""Base entity and the helper that keeps one entity per configured zone."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .node import FP400Node


class FP400Entity(Entity):
    """Entity bound to one FP400 node."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _follows_targets = False  # also update on every position event

    def __init__(self, fp: FP400Node, key: str) -> None:
        """Set up identity and device link."""
        self.fp = fp
        self._attr_unique_id = f"{DOMAIN}_{fp.node_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = fp.device_info

    @property
    def available(self) -> bool:
        """Unavailable while the Matter node is offline, like the Matter integration's own entities."""
        return self.fp.node.available

    async def async_added_to_hass(self) -> None:
        """Follow node updates."""
        self.async_on_remove(self.fp.add_listener(self.async_write_ha_state, targets=self._follows_targets))


class FP400ZoneEntity(FP400Entity):
    """Entity for one configured zone (created and removed with the zones)."""

    def __init__(self, fp: FP400Node, zone_id: int, key: str) -> None:
        super().__init__(fp, f"zone_{zone_id}_{key}")
        self._attr_translation_key = f"zone_{key}"
        self._attr_translation_placeholders = {"id": str(zone_id)}
        self.zone_id = zone_id


@callback
def follow_zones(
    hass: HomeAssistant,
    entry: ConfigEntry,
    fp: FP400Node,
    async_add_entities: AddEntitiesCallback,
    platform: str,
    factory: Callable[[FP400Node, int], FP400ZoneEntity],
) -> None:
    """Keep one entity per configured zone: added and removed with the zones."""
    present: dict[int, FP400ZoneEntity] = {}

    @callback
    def sync() -> None:
        wanted = {zone.zone_id for zone in fp.zones}
        if new := sorted(wanted - present.keys()):
            entities = [factory(fp, zone_id) for zone_id in new]
            present.update({e.zone_id: e for e in entities})
            async_add_entities(entities)
        for zone_id in sorted(present.keys() - wanted):
            entity = present.pop(zone_id)
            hass.async_create_task(_remove(entity))

    async def _remove(entity: FP400ZoneEntity) -> None:
        await entity.async_remove(force_remove=True)
        registry = er.async_get(hass)
        if entity_id := registry.async_get_entity_id(platform, DOMAIN, entity.unique_id):
            registry.async_remove(entity_id)

    entry.async_on_unload(fp.add_listener(sync))
    sync()
