"""Sensors: zones, tracked targets (with per-target x/y), last motion event."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import DEGREE, EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SIGNAL_NEW_NODE, FP400ConfigEntry
from .const import (
    ACTIVITY_STATES,
    ATTR_INSTALL_ANGLE,
    ATTR_INSTALL_STATUS,
    DOMAIN,
    GRID_COLS,
    GRID_ROWS,
    INSTALL_STATUSES,
    MAX_TARGETS,
    MOTION_OPTIONS,
)
from .entity import FP400Entity
from .node import FP400Node


async def async_setup_entry(
    hass: HomeAssistant, entry: FP400ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create sensors for every node, now and when nodes appear later."""

    @callback
    def _add(fp: FP400Node) -> None:
        entities: list[FP400Entity] = [
            ZonesSensor(fp),
            RegionsSensor(fp),
            TargetsSensor(fp),
            LastMotionSensor(fp),
            ActivityStateSensor(fp),
            HumanCountSensor(fp),
            InstallStatusSensor(fp),
            InstallAngleSensor(fp),
        ]
        for index in range(MAX_TARGETS):
            entities.append(TargetAxisSensor(fp, index, "x"))
            entities.append(TargetAxisSensor(fp, index, "y"))
        async_add_entities(entities)
        _follow_zones(hass, entry, fp, async_add_entities)

    for fp in entry.runtime_data.nodes.values():
        _add(fp)
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_NODE, _add))


@callback
def _follow_zones(
    hass: HomeAssistant, entry: FP400ConfigEntry, fp: FP400Node, async_add_entities: AddEntitiesCallback
) -> None:
    """Keep one last-motion sensor per configured zone: added and removed with the zones."""
    present: dict[int, ZoneMotionSensor] = {}

    @callback
    def sync() -> None:
        wanted = {zone.zone_id for zone in fp.zones}
        if new := sorted(wanted - present.keys()):
            entities = [ZoneMotionSensor(fp, zone_id) for zone_id in new]
            present.update({e.zone_id: e for e in entities})
            async_add_entities(entities)
        for zone_id in sorted(present.keys() - wanted):
            entity = present.pop(zone_id)
            hass.async_create_task(_remove(entity))

    async def _remove(entity: ZoneMotionSensor) -> None:
        await entity.async_remove(force_remove=True)
        registry = er.async_get(hass)
        if entity_id := registry.async_get_entity_id("sensor", DOMAIN, entity.unique_id):
            registry.async_remove(entity_id)

    entry.async_on_unload(fp.add_listener(sync))
    sync()


class ZonesSensor(FP400Entity, SensorEntity):
    """Number of configured zones; the zone definitions ride along as attributes."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:vector-square"
    _unrecorded_attributes = frozenset({"zones", "zone_endpoints"})

    def __init__(self, fp: FP400Node) -> None:
        super().__init__(fp, "zones")

    @property
    def native_value(self) -> int:
        return len(self.fp.zones)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "zones": [{**zone.as_dict(), "name": self.fp.zone_name(zone.zone_id)} for zone in self.fp.zones],
            "pending": self.fp.zones_pending,
            "error": self.fp.zones_error,
            "max_zones": self.fp.max_zones,
            "learning_until": self.fp.learning_until,
            "learning_finished_at": self.fp.learning_finished_at,
            "learning_zones": self.fp.learning_zones,
            "zone_endpoints": self.fp.zone_endpoints(),
            "grid_rows": GRID_ROWS,
            "grid_cols": GRID_COLS,
        }


class RegionsSensor(FP400Entity, SensorEntity):
    """Entry/exit, interference and monitoring regions; their cells ride along as attributes."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:selection-drag"
    _unrecorded_attributes = frozenset({"regions"})

    def __init__(self, fp: FP400Node) -> None:
        super().__init__(fp, "regions")

    @property
    def native_value(self) -> int:
        return sum(1 for cells in self.fp.regions.values() if cells)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "regions": {key: list(cells) for key, cells in self.fp.regions.items()},
            "pending": dict(self.fp.regions_pending),
            "error": dict(self.fp.regions_error),
            "grid_rows": GRID_ROWS,
            "grid_cols": GRID_COLS,
        }


class TargetsSensor(FP400Entity, SensorEntity):
    """Number of tracked people; positions in the attributes."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-multiple"
    _follows_targets = True
    _unrecorded_attributes = frozenset({"targets", "updated"})

    def __init__(self, fp: FP400Node) -> None:
        super().__init__(fp, "targets")

    @property
    def native_value(self) -> int:
        # targets the device has classified as a fan, pet or other non-person are listed but not counted
        return sum(1 for target in self.fp.targets if target.is_person)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "targets": [target.as_dict() for target in self.fp.targets],
            "updated": self.fp.targets_updated,
            "live_tracking": self.fp.live_tracking,
            "activity_state": self.fp.activity_state,
            "human_count": self.fp.human_count,
        }


class TargetAxisSensor(FP400Entity, SensorEntity):
    """x or y of one tracked target, for radar map cards that need plain numeric entities."""

    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.CENTIMETERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False
    _follows_targets = True

    def __init__(self, fp: FP400Node, index: int, axis: str) -> None:
        super().__init__(fp, f"target_{index + 1}_{axis}")
        self._attr_translation_key = f"target_{axis}"
        self._attr_translation_placeholders = {"index": str(index + 1)}
        self._index = index
        self._axis = axis

    @property
    def native_value(self) -> int | None:
        if self._index >= len(self.fp.targets):
            return None
        return getattr(self.fp.targets[self._index], self._axis)


class LastMotionSensor(FP400Entity, SensorEntity):
    """Last MotionDetected event (enter/left/access/away/...)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = MOTION_OPTIONS
    _attr_icon = "mdi:motion-sensor"

    def __init__(self, fp: FP400Node) -> None:
        super().__init__(fp, "last_motion")

    @property
    def native_value(self) -> str | None:
        return self.fp.last_motion

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        zone = self.fp.last_motion_zone
        return {
            "zone": zone,
            "zone_name": None if zone is None else self.fp.zone_name(zone),
            "at": self.fp.last_motion_at,
        }


class ZoneMotionSensor(FP400Entity, SensorEntity):
    """Last MotionDetected event of one zone (its child endpoint reports enter/left/... too)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = MOTION_OPTIONS
    _attr_icon = "mdi:motion-sensor"

    def __init__(self, fp: FP400Node, zone_id: int) -> None:
        super().__init__(fp, f"zone_{zone_id}_last_motion")
        self._attr_translation_key = "zone_motion"
        self._attr_translation_placeholders = {"id": str(zone_id)}
        self.zone_id = zone_id

    @property
    def native_value(self) -> str | None:
        motion = self.fp.zone_motion.get(self.zone_id)
        return None if motion is None else motion[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        motion = self.fp.zone_motion.get(self.zone_id)
        return {
            "zone": self.zone_id,
            "zone_name": self.fp.zone_name(self.zone_id),
            "at": None if motion is None else motion[1],
        }


class ActivityStateSensor(FP400Entity, SensorEntity):
    """OccupantLocation ActivityState."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(ACTIVITY_STATES.values())
    _attr_icon = "mdi:motion"

    def __init__(self, fp: FP400Node) -> None:
        super().__init__(fp, "activity_state")

    @property
    def native_value(self) -> str:
        return self.fp.activity_state


class HumanCountSensor(FP400Entity, SensorEntity):
    """RadarSensingUnion CurrentHumanCount (255 = unknown → unavailable)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:account-group"

    def __init__(self, fp: FP400Node) -> None:
        super().__init__(fp, "human_count")

    @property
    def native_value(self) -> int | None:
        return self.fp.human_count


class InstallStatusSensor(FP400Entity, SensorEntity):
    """Orientation as measured by the tilt sensor."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(INSTALL_STATUSES.values())
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:axis-z-rotate-clockwise"

    def __init__(self, fp: FP400Node) -> None:
        super().__init__(fp, "install_status")

    @property
    def native_value(self) -> str | None:
        value = self.fp.config_attr(ATTR_INSTALL_STATUS)
        return None if value is None else INSTALL_STATUSES.get(int(value))


class InstallAngleSensor(FP400Entity, SensorEntity):
    """Tilt from horizontal."""

    _attr_native_unit_of_measurement = DEGREE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:angle-acute"

    def __init__(self, fp: FP400Node) -> None:
        super().__init__(fp, "install_angle")

    @property
    def native_value(self) -> int | None:
        value = self.fp.config_attr(ATTR_INSTALL_ANGLE)
        return None if value is None else int(value)
