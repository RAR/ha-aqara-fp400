"""Live tracking switch: keeps the LocationInfo stream subscribed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import SIGNAL_NEW_NODE, FP400ConfigEntry
from .entity import FP400Entity
from .node import FP400Node


async def async_setup_entry(
    hass: HomeAssistant, entry: FP400ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the switch for every node."""

    @callback
    def _add(fp: FP400Node) -> None:
        async_add_entities([LiveTrackingSwitch(fp)])

    for fp in entry.runtime_data.nodes.values():
        _add(fp)
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_NODE, _add))


class LiveTrackingSwitch(FP400Entity, SwitchEntity, RestoreEntity):
    """While on, the device streams target positions (~7 events/s while someone moves)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:radar"

    def __init__(self, fp: FP400Node) -> None:
        super().__init__(fp, "live_tracking")

    async def async_added_to_hass(self) -> None:
        """Resume the stream if it was on before a restart or reload."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and last.state == "on" and not self.fp.live_tracking:
            await self.fp.async_set_live_tracking(True)

    @property
    def is_on(self) -> bool:
        return self.fp.live_tracking

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.fp.async_set_live_tracking(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.fp.async_set_live_tracking(False)
