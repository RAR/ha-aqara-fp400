"""Per-zone occupancy, derived from the tracked targets or the zone's own events."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SIGNAL_NEW_NODE, FP400ConfigEntry
from .entity import FP400ZoneEntity, follow_zones
from .node import FP400Node


async def async_setup_entry(
    hass: HomeAssistant, entry: FP400ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create one occupancy sensor per zone for every node, now and when nodes appear later."""

    @callback
    def _add(fp: FP400Node) -> None:
        follow_zones(hass, entry, fp, async_add_entities, "binary_sensor", ZoneOccupancySensor)

    for fp in entry.runtime_data.nodes.values():
        _add(fp)
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_NODE, _add))


class ZoneOccupancySensor(FP400ZoneEntity, BinarySensorEntity):
    """Someone is in the zone.

    Unlike the device's own zone occupancy (a Matter child endpoint, motion-triggered with a short
    hold time), this stays on for a person sitting still: while live tracking is on it follows the
    tracked targets, otherwise the zone's enter/access and leave events. Off while the room is empty,
    after the configurable off delay.
    """

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, fp: FP400Node, zone_id: int) -> None:
        super().__init__(fp, zone_id, "occupancy")

    @property
    def is_on(self) -> bool:
        return self.fp.zone_occupied.get(self.zone_id, False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "zone": self.zone_id,
            "zone_name": self.fp.zone_name(self.zone_id),
            "source": self.fp.zone_occupancy_source,
        }
