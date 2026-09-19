"""Buttons: start AI background learning, clear zones."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SIGNAL_NEW_NODE, FP400ConfigEntry
from .entity import FP400Entity
from .node import FP400Node


async def async_setup_entry(
    hass: HomeAssistant, entry: FP400ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the buttons for every node."""

    @callback
    def _add(fp: FP400Node) -> None:
        async_add_entities([StartLearningButton(fp), ClearZonesButton(fp)])

    for fp in entry.runtime_data.nodes.values():
        _add(fp)
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_NODE, _add))


class StartLearningButton(FP400Entity, ButtonEntity):
    """EnableAiSpaceBackgroundLearning."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:brain"

    def __init__(self, fp: FP400Node) -> None:
        super().__init__(fp, "start_learning")

    async def async_press(self) -> None:
        await self.fp.async_start_learning()


class ClearZonesButton(FP400Entity, ButtonEntity):
    """SetZones([])."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:vector-square-remove"

    def __init__(self, fp: FP400Node) -> None:
        super().__init__(fp, "clear_zones")

    async def async_press(self) -> None:
        await self.fp.async_set_zones([])
