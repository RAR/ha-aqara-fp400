"""Config selects for the install/detection settings."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SIGNAL_NEW_NODE, FP400ConfigEntry
from .const import (
    ATTR_COORDINATE_REVERSE,
    ATTR_DETECTION_DIRECTION,
    ATTR_INSTALL_MODE,
    ATTR_PROXIMITY_LEVEL,
    ATTR_SIDE_INSTALL,
    COORDINATE_REVERSE,
    DETECTION_DIRECTIONS,
    INSTALL_MODES,
    PROXIMITY_LEVELS,
    SIDE_INSTALLS,
)
from .entity import FP400Entity
from .node import FP400Node

SELECTS: list[tuple[str, int, dict[int, str], str]] = [
    ("install_mode", ATTR_INSTALL_MODE, INSTALL_MODES, "mdi:wall"),
    ("side_install", ATTR_SIDE_INSTALL, SIDE_INSTALLS, "mdi:wall"),
    ("coordinate_reverse", ATTR_COORDINATE_REVERSE, COORDINATE_REVERSE, "mdi:flip-horizontal"),
    ("detection_direction", ATTR_DETECTION_DIRECTION, DETECTION_DIRECTIONS, "mdi:arrow-left-right"),
    ("proximity_distance_level", ATTR_PROXIMITY_LEVEL, PROXIMITY_LEVELS, "mdi:map-marker-distance"),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: FP400ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the selects for every node."""

    @callback
    def _add(fp: FP400Node) -> None:
        async_add_entities(ConfigSelect(fp, key, attribute, options, icon) for key, attribute, options, icon in SELECTS)

    for fp in entry.runtime_data.nodes.values():
        _add(fp)
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_NODE, _add))


class ConfigSelect(FP400Entity, SelectEntity):
    """An enum attribute of the AmbientSensingConfiguration cluster."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, fp: FP400Node, key: str, attribute: int, options: dict[int, str], icon: str) -> None:
        super().__init__(fp, key)
        self._attribute = attribute
        self._map = options
        self._attr_options = list(options.values())
        self._attr_icon = icon

    @property
    def current_option(self) -> str | None:
        value = self.fp.config_attr(self._attribute)
        return None if value is None else self._map.get(int(value))

    async def async_select_option(self, option: str) -> None:
        value = next(k for k, v in self._map.items() if v == option)
        await self.fp.async_write_config(self._attribute, value)
