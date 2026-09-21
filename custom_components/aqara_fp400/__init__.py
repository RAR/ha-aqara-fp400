"""Aqara FP400 (Matter) — zones, live target positions and setup commands.

Rides on Home Assistant's Matter integration: same server connection, same fabric,
entities attached to the existing Matter device.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from homeassistant.components.matter.helpers import get_matter
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.loader import async_get_integration
from matter_server.client.models.node import MatterNode
from matter_server.common.models import EventType
import voluptuous as vol

from .const import (
    DOMAIN,
    GRID_COLS,
    GRID_ROWS,
    LOGGER,
    PRODUCT_ID_FP400,
    REGIONS,
    SERVICE_CLEAR_REGION,
    SERVICE_CLEAR_ZONES,
    SERVICE_SET_REGION,
    SERVICE_SET_ZONES,
    SERVICE_START_LEARNING,
    SERVICE_SUBSCRIBE_LOCATION,
    VENDOR_ID,
)
from .node import FP400Node, Zone

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON, Platform.SELECT, Platform.NUMBER]
SIGNAL_NEW_NODE = f"{DOMAIN}_new_node"
CARD_URL = f"/{DOMAIN}/aqara-fp400-zone-card.js"


@dataclass
class FP400Data:
    """Runtime data of the config entry."""

    nodes: dict[int, FP400Node] = field(default_factory=dict)
    unsubscribe: list = field(default_factory=list)


type FP400ConfigEntry = ConfigEntry[FP400Data]


def is_fp400(node: MatterNode) -> bool:
    """True for an Aqara FP400 node."""
    attrs = node.node_data.attributes
    return attrs.get("0/40/2") == VENDOR_ID and attrs.get("0/40/4") == PRODUCT_ID_FP400


async def async_setup_entry(hass: HomeAssistant, entry: FP400ConfigEntry) -> bool:
    """Set up from a config entry."""
    matter_entries = hass.config_entries.async_loaded_entries("matter")
    if not matter_entries:
        raise ConfigEntryNotReady("The Matter integration is not loaded")
    matter_entry = matter_entries[0]
    client = get_matter(hass).matter_client
    data = FP400Data()
    entry.runtime_data = data

    # The Matter integration reloads itself (new client object) whenever the server connection
    # drops; follow it so we never keep talking to a dead client.
    @callback
    def _on_matter_state_change() -> None:
        if matter_entry.state is ConfigEntryState.LOADED and entry.state is ConfigEntryState.LOADED:
            LOGGER.debug("Matter integration reloaded; reloading %s", DOMAIN)
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(matter_entry.async_on_state_change(_on_matter_state_change))

    await _async_register_card(hass)

    async def _add_node(node: MatterNode) -> None:
        if node.node_id in data.nodes or not is_fp400(node):
            return
        fp = FP400Node(hass=hass, node=node, matter_client=client)
        await fp.async_start()
        data.nodes[node.node_id] = fp
        LOGGER.info("Found %s (node %s)", fp.name, node.node_id)
        async_dispatcher_send(hass, SIGNAL_NEW_NODE, fp)

    for node in client.get_nodes():
        await _add_node(node)

    @callback
    def _on_node_added(event: EventType, node: MatterNode) -> None:
        hass.async_create_task(_add_node(node))

    data.unsubscribe.append(client.subscribe_events(callback=_on_node_added, event_filter=EventType.NODE_ADDED))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FP400ConfigEntry) -> bool:
    """Unload a config entry."""
    for unsub in entry.runtime_data.unsubscribe:
        unsub()
    for fp in entry.runtime_data.nodes.values():
        await fp.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the bundled card and register it as a Lovelace resource (like HACS does).

    Skipped when http/frontend are not loaded (tests). Injecting the script through
    add_extra_js_url no longer works: the frontend's scoped custom-element registry
    doesn't see elements defined by scripts that ran before it.
    """
    if hass.data.get(f"{DOMAIN}_card_registered") or "frontend" not in hass.config.components:
        return
    import mimetypes

    from aiohttp.web_fileresponse import CONTENT_TYPES
    from homeassistant.components.http import StaticPathConfig

    hass.data[f"{DOMAIN}_card_registered"] = True
    # Some images map .js to text/plain; with nosniff the browser then refuses the module.
    mimetypes.add_type("text/javascript", ".js")
    with contextlib.suppress(AttributeError):
        CONTENT_TYPES.add_type("text/javascript", ".js")
    card = Path(__file__).parent / "www" / "aqara-fp400-zone-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(card), cache_headers=False)])
    version = (await async_get_integration(hass, DOMAIN)).version
    url = f"{CARD_URL}?v={version}"

    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if resources is None or not hasattr(resources, "async_create_item"):
        LOGGER.warning("Lovelace resources are in YAML mode; add %s as a module resource yourself", url)
        return
    if not getattr(resources, "loaded", True):
        await resources.async_load()
    for item in resources.async_items():
        if not str(item.get("url", "")).startswith(CARD_URL):
            continue
        if item["url"] != url:
            await resources.async_update_item(item["id"], {"url": url})
        return
    await resources.async_create_item({"res_type": "module", "url": url})


# ---------------------------------------------------------------------------
# services


def _cell_list(value: Any) -> list[list[int]]:
    """Accept [[row, col], ...] or a "rows,cols" range spec such as "3-4,6-10"."""
    if isinstance(value, str):
        rows_spec, _, cols_spec = value.partition(",")

        def rng(spec: str) -> range:
            start, _, end = spec.strip().partition("-")
            return range(int(start), int(end or start) + 1)

        return [[r, c] for r in rng(rows_spec) for c in rng(cols_spec)]
    cells = []
    for item in value:
        row, col = int(item[0]), int(item[1])
        if not (0 <= row < GRID_ROWS and 0 <= col < GRID_COLS):
            raise vol.Invalid(f"cell [{row}, {col}] outside the {GRID_ROWS}x{GRID_COLS} grid")
        cells.append([row, col])
    return cells


ZONE_SCHEMA = vol.Schema(
    {
        vol.Required("id"): vol.All(vol.Coerce(int), vol.Range(min=1, max=8)),
        vol.Required("cells"): _cell_list,
        vol.Optional("enabled", default=True): cv.boolean,
        vol.Optional("type", default=0): vol.Coerce(int),
    }
)

SET_ZONES_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("zones"): [ZONE_SCHEMA],
    }
)
DEVICE_SCHEMA = vol.Schema({vol.Required("device_id"): cv.string})
SET_REGION_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("region"): vol.In(list(REGIONS)),
        vol.Required("cells"): _cell_list,
    }
)
CLEAR_REGION_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("region"): vol.In(list(REGIONS)),
    }
)
SUBSCRIBE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Optional("timeout", default=3600): vol.All(vol.Coerce(int), vol.Range(min=1, max=3600)),
    }
)


def _node_for_device(hass: HomeAssistant, device_id: str) -> FP400Node:
    """Resolve a HA device id to our node object."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise HomeAssistantError(f"device {device_id} not found")
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        for fp in entry.runtime_data.nodes.values():
            if device.identifiers & {fp.device_identifier, fp.own_identifier}:
                return fp
    raise HomeAssistantError(f"device {device.name_by_user or device.name} is not an Aqara FP400")


@callback
def _async_register_services(hass: HomeAssistant, entry: FP400ConfigEntry) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_ZONES):
        return

    async def set_zones(call: ServiceCall) -> dict[str, Any]:
        fp = _node_for_device(hass, call.data["device_id"])
        zones = [
            Zone(zone_id=z["id"], cells=z["cells"], enabled=z["enabled"], zone_type=z["type"])
            for z in call.data["zones"]
        ]
        try:
            await fp.async_set_zones(zones)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        return {"zones": [z.as_dict() for z in fp.zones]}

    async def clear_zones(call: ServiceCall) -> None:
        fp = _node_for_device(hass, call.data["device_id"])
        await fp.async_set_zones([])

    async def set_region(call: ServiceCall) -> None:
        fp = _node_for_device(hass, call.data["device_id"])
        try:
            await fp.async_set_region(call.data["region"], call.data["cells"])
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err

    async def clear_region(call: ServiceCall) -> None:
        fp = _node_for_device(hass, call.data["device_id"])
        await fp.async_set_region(call.data["region"], [])

    async def subscribe_location(call: ServiceCall) -> None:
        fp = _node_for_device(hass, call.data["device_id"])
        await fp.async_subscribe_location(call.data["timeout"])

    async def start_learning(call: ServiceCall) -> None:
        fp = _node_for_device(hass, call.data["device_id"])
        await fp.async_start_learning()

    hass.services.async_register(
        DOMAIN, SERVICE_SET_ZONES, set_zones, schema=SET_ZONES_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_ZONES, clear_zones, schema=DEVICE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_REGION, set_region, schema=SET_REGION_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_REGION, clear_region, schema=CLEAR_REGION_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SUBSCRIBE_LOCATION, subscribe_location, schema=SUBSCRIBE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_START_LEARNING, start_learning, schema=DEVICE_SCHEMA)
