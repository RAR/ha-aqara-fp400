"""Install height."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SIGNAL_NEW_NODE, FP400ConfigEntry
from .const import ATTR_INSTALL_HEIGHT, ATTR_INSTALL_HEIGHT_MAX, ATTR_INSTALL_HEIGHT_MIN
from .entity import FP400Entity
from .node import FP400Node


async def async_setup_entry(
    hass: HomeAssistant, entry: FP400ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the number for every node."""

    @callback
    def _add(fp: FP400Node) -> None:
        async_add_entities([InstallHeightNumber(fp)])

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
