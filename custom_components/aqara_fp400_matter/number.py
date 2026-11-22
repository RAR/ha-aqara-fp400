"""Install height and the per-zone occupancy off delay."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SIGNAL_NEW_NODE, FP400ConfigEntry
from .const import (
    ATTR_INSTALL_HEIGHT,
    ATTR_INSTALL_HEIGHT_MAX,
    ATTR_INSTALL_HEIGHT_MIN,
    ZONE_OFF_DELAY_MAX_S,
)
from .entity import FP400Entity
from .node import FP400Node


async def async_setup_entry(
    hass: HomeAssistant, entry: FP400ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the numbers for every node."""

    @callback
    def _add(fp: FP400Node) -> None:
        async_add_entities([InstallHeightNumber(fp), ZoneOffDelayNumber(fp)])

    for fp in entry.runtime_data.nodes.values():
        _add(fp)
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_NODE, _add))


class InstallHeightNumber(FP400Entity, NumberEntity):
    """Mounting height in mm; limits come from the device."""

    _attr_device_class = NumberDeviceClass.DISTANCE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfLength.MILLIMETERS
    _attr_native_step = 10
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:human-male-height"

    def __init__(self, fp: FP400Node) -> None:
        super().__init__(fp, "install_height")

    @property
    def native_min_value(self) -> float:
        return float(self.fp.config_attr(ATTR_INSTALL_HEIGHT_MIN) or 1500)

    @property
    def native_max_value(self) -> float:
        return float(self.fp.config_attr(ATTR_INSTALL_HEIGHT_MAX) or 4000)

    @property
    def native_value(self) -> float | None:
        value = self.fp.config_attr(ATTR_INSTALL_HEIGHT)
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        await self.fp.async_write_config(ATTR_INSTALL_HEIGHT, int(value))


class ZoneOffDelayNumber(FP400Entity, RestoreNumber):
    """Seconds a zone occupancy sensor stays on after the last sign of someone in the zone (kept across restarts)."""

    _attr_device_class = NumberDeviceClass.DURATION
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = 0
    _attr_native_max_value = ZONE_OFF_DELAY_MAX_S
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-off-outline"

    def __init__(self, fp: FP400Node) -> None:
        super().__init__(fp, "zone_off_delay")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self.fp.set_zone_off_delay(float(last.native_value))

    @property
    def native_value(self) -> float:
        return float(self.fp.zone_off_delay_s)

    async def async_set_native_value(self, value: float) -> None:
        self.fp.set_zone_off_delay(value)
