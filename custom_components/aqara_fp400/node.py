"""Per-node state for an Aqara FP400: zones, live targets and vendor commands."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.components.matter.helpers import get_node_device_identifier
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util
from matter_server.client.models.node import MatterNode
from matter_server.common.models import APICommand, EventType, MatterNodeEvent

from .const import (
    ACTIVITY_STATES,
    ATTR_ACTIVITY_STATE,
    ATTR_HUMAN_COUNT,
    ATTR_MAX_ZONES,
    ATTR_ZONE_ID,
    ATTR_ZONES,
    CLUSTER_CONFIG,
    CLUSTER_LOCATION,
    CLUSTER_RADAR,
    DOMAIN,
    EVENT_LOCATION_INFO,
    EVENT_MOTION_DETECTED,
    LOCATION_RENEW_S,
    LOCATION_SUBSCRIPTION_S,
    LOGGER,
    MAX_ZONES,
    MOTION_EVENTS,
    SENSOR_ENDPOINT,
    ZONE_POLL_S,
    cell_from_index,
    cells_to_mask,
    mask_to_cells,
    to_bytes,
)


@dataclass
class Zone:
    """A detection zone."""

    zone_id: int
    cells: list[list[int]]
    enabled: bool = True
    zone_type: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Dict for entity attributes / service responses."""
        return {
            "id": self.zone_id,
            "type": self.zone_type,
            "enabled": self.enabled,
            "cells": self.cells,
        }

    def as_payload(self) -> dict[str, Any]:
        """Struct as the Matter server expects it (field labels of the vendor cluster)."""
        return {
            "zoneId": self.zone_id,
            "zoneType": self.zone_type,
            "cells": cells_to_mask(tuple(c) for c in self.cells),
            "enabled": self.enabled,
        }


@dataclass
class Target:
    """One tracked person from a LocationInfo event."""

    target_id: int
    x: int
    y: int
    row: int
    col: int
    activity: str
    zone_id: int | None

    @property
    def zones(self) -> list[int]:
        """Zone ids the target is inside (the device reports one)."""
        return [] if self.zone_id is None else [self.zone_id]

    def as_dict(self) -> dict[str, Any]:
        """Dict for entity attributes."""
        return {
            "id": self.target_id,
            "x": self.x,
            "y": self.y,
            "row": self.row,
            "col": self.col,
            "activity": self.activity,
            "zones": self.zones,
        }


def _get(data: dict, *keys: str | int, default: Any = None) -> Any:
    """Read a struct field that may be keyed by label, by tag or by tag string."""
    for key in keys:
        if key in data:
            return data[key]
        if str(key) in data:
            return data[str(key)]
    return default


def _event_payload(data: Any) -> Any:
    """Unwrap the event data variants delivered by the different servers."""
    if isinstance(data, dict) and "TLVValue" in data:
        return data["TLVValue"]
    return data


def _zone_signature(zones: list[Zone]) -> list[tuple[int, int, bool, tuple[tuple[int, int], ...]]]:
    return sorted(
        (z.zone_id, z.zone_type, z.enabled, tuple(tuple(c) for c in sorted(z.cells))) for z in zones
    )


def parse_zone(raw: Any) -> Zone | None:
    """Parse one entry of the Zones attribute."""
    if not isinstance(raw, dict):
        return None
    zone_id = _get(raw, "zoneId", "zoneID", 0)
    mask = _get(raw, "cells", 2)
    if zone_id is None or mask is None:
        return None
    return Zone(
        zone_id=int(zone_id),
        cells=mask_to_cells(to_bytes(mask)),
        enabled=bool(_get(raw, "enabled", 3, default=True)),
        zone_type=int(_get(raw, "zoneType", 1, default=0) or 0),
    )


def parse_target(raw: Any) -> Target | None:
    """Parse one entry of the LocationInfo targets list."""
    if not isinstance(raw, dict):
        return None
    cell = int(_get(raw, "cell", 3, default=0) or 0)
    row, col = cell_from_index(cell)
    return Target(
        target_id=int(_get(raw, "targetId", "targetID", 0, default=0) or 0),
        x=int(_get(raw, "x", 1, default=0) or 0),
        y=int(_get(raw, "y", 2, default=0) or 0),
        row=row,
        col=col,
        activity=ACTIVITY_STATES.get(int(_get(raw, "activityState", 4, default=0) or 0), "unknown"),
        zone_id=_zone_id(_get(raw, "inZoneID", "inZoneId", "zoneMask", 8, default=None)),
    )


def _zone_id(value: Any) -> int | None:
    """Field 8 of a target is the id of the zone it is in; absent or 0/255 means none."""
    if value is None:
        return None
    value = int(value)
    return value if 1 <= value <= MAX_ZONES else None


@dataclass
class FP400Node:
    """State and commands for one FP400 node."""

    hass: HomeAssistant
    node: MatterNode
    matter_client: Any
    zones: list[Zone] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    targets_updated: datetime | None = None
    last_motion: str | None = None
    last_motion_zone: int | None = None
    last_motion_at: datetime | None = None
    live_tracking: bool = False
    zones_pending: bool = False
    zones_error: str | None = None
    _listeners: list[Callable[[], None]] = field(default_factory=list)
    _unsubscribe: list[Callable[[], None]] = field(default_factory=list)
    _renew_task: asyncio.Task | None = None
    _zone_task: asyncio.Task | None = None

    @property
    def node_id(self) -> int:
        """Matter node id."""
        return self.node.node_id

    @property
    def name(self) -> str:
        """Device name from Basic Information."""
        attrs = self.node.node_data.attributes
        return attrs.get("0/40/5") or attrs.get("0/40/3") or f"FP400 {self.node_id}"

    @property
    def device_identifier(self) -> tuple[str, str]:
        """Identifier of the HA device the Matter integration created for this node."""
        return get_node_device_identifier(self.matter_client.server_info, self.node_id)

    @property
    def own_identifier(self) -> tuple[str, str]:
        """Identifier of this integration's device for the node."""
        return (DOMAIN, str(self.node_id))

    @property
    def device_info(self) -> DeviceInfo:
        """Own device, linked to the Matter device (a device belongs to one config entry)."""
        attrs = self.node.node_data.attributes
        matter_devices = dr.async_get(self.hass).async_get_devices(identifiers={self.device_identifier})
        matter_device = next(iter(matter_devices), None)
        return DeviceInfo(
            identifiers={self.own_identifier},
            name=f"{self.name} radar",
            manufacturer=attrs.get("0/40/1"),
            model=attrs.get("0/40/3"),
            via_device_id=matter_device.id if matter_device else None,
        )

    @property
    def max_zones(self) -> int:
        """Zone limit reported by the device."""
        return int(self._attr(SENSOR_ENDPOINT, CLUSTER_CONFIG, ATTR_MAX_ZONES) or MAX_ZONES)

    @property
    def activity_state(self) -> str:
        """Overall activity state."""
        value = self._attr(SENSOR_ENDPOINT, CLUSTER_LOCATION, ATTR_ACTIVITY_STATE)
        return ACTIVITY_STATES.get(int(value or 0), "unknown")

    @property
    def human_count(self) -> int | None:
        """CurrentHumanCount; 255 means unknown."""
        value = self._attr(SENSOR_ENDPOINT, CLUSTER_RADAR, ATTR_HUMAN_COUNT)
        if value is None or int(value) == 255:
            return None
        return int(value)

    def zone_endpoints(self) -> dict[int, int]:
        """Map zone id -> child endpoint id."""
        result = {}
        for path, value in self.node.node_data.attributes.items():
            endpoint, cluster, attribute = (int(x) for x in path.split("/"))
            if cluster == CLUSTER_RADAR and attribute == ATTR_ZONE_ID and endpoint != SENSOR_ENDPOINT:
                result[int(value)] = endpoint
        return result

    def _attr(self, endpoint: int, cluster: int, attribute: int) -> Any:
        return self.node.node_data.attributes.get(f"{endpoint}/{cluster}/{attribute}")

    def config_attr(self, attribute: int) -> Any:
        """Read a cached AmbientSensingConfiguration attribute."""
        return self._attr(SENSOR_ENDPOINT, CLUSTER_CONFIG, attribute)

    async def async_write_config(self, attribute: int, value: Any) -> None:
        """Write an AmbientSensingConfiguration attribute and mirror it into the cache."""
        result = await self.matter_client.send_command(
            APICommand.WRITE_ATTRIBUTE,
            node_id=self.node_id,
            attribute_path=f"{SENSOR_ENDPOINT}/{CLUSTER_CONFIG}/{attribute}",
            value=value,
        )
        for entry in result or []:
            status = entry.get("Status") if isinstance(entry, dict) else None
            if status not in (None, 0):
                raise ValueError(f"device rejected the write (status {status})")
        self.node.node_data.attributes[f"{SENSOR_ENDPOINT}/{CLUSTER_CONFIG}/{attribute}"] = value
        self._notify()

    # ---- lifecycle -------------------------------------------------------

    async def async_start(self) -> None:
        """Subscribe to node events and attribute updates."""
        self._refresh_zones()
        self.hass.async_create_background_task(self._async_initial_zones(), f"{self.name} read zones")
        self._zone_task = self.hass.async_create_background_task(
            self._zone_poll_loop(), f"{self.name} poll zones"
        )
        self._unsubscribe.append(
            self.matter_client.subscribe_events(
                callback=self._on_node_event,
                event_filter=EventType.NODE_EVENT,
                node_filter=self.node_id,
            )
        )
        self._unsubscribe.append(
            self.matter_client.subscribe_events(
                callback=self._on_attribute_updated,
                event_filter=EventType.ATTRIBUTE_UPDATED,
                node_filter=self.node_id,
            )
        )

    async def async_stop(self) -> None:
        """Tear down subscriptions and the renew task."""
        for unsub in self._unsubscribe:
            unsub()
        self._unsubscribe.clear()
        if self._zone_task:
            self._zone_task.cancel()
            self._zone_task = None
        await self.async_set_live_tracking(False)

    @callback
    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register an entity update callback."""
        self._listeners.append(listener)

        def _remove() -> None:
            self._listeners.remove(listener)

        return _remove

    @callback
    def _notify(self) -> None:
        for listener in self._listeners:
            listener()

    # ---- incoming data ---------------------------------------------------

    async def async_read_zones(self) -> list[Zone] | None:
        """Read the zone list from the device; the server's subscription cache misses its changes."""
        path = f"{SENSOR_ENDPOINT}/{CLUSTER_CONFIG}/{ATTR_ZONES}"
        try:
            result = await self.matter_client.read_attribute(self.node_id, path)
        except Exception as err:
            LOGGER.debug("%s: reading zones failed: %s", self.name, err)
            return None
        if not isinstance(result, dict) or path not in result:
            return None
        self.node.node_data.attributes[path] = result[path]
        return sorted(
            (z for z in (parse_zone(item) for item in result[path] or []) if z is not None), key=lambda z: z.zone_id
        )

    async def _async_initial_zones(self) -> None:
        if (zones := await self.async_read_zones()) is not None:
            self.zones = zones
            self._notify()

    async def _zone_poll_loop(self) -> None:
        """Re-read zones periodically so changes made in the Aqara app show up.

        The device does not report zone (attribute 16) changes to the server's
        subscription, so nothing else notices when zones are edited elsewhere.
        """
        while True:
            await asyncio.sleep(ZONE_POLL_S)
            try:
                if self.zones_pending:
                    continue  # a local write is being verified; don't fight it
                zones = await self.async_read_zones()
                # re-check after the await: a local write may have started meanwhile
                if self.zones_pending or zones is None:
                    continue
                if _zone_signature(zones) != _zone_signature(self.zones):
                    self.zones = zones
                    self._notify()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("%s: zone poll failed", self.name)

    async def _async_verify_zones(self, expected: list[Zone]) -> None:
        """Poll the device until its zone list matches what was written (it applies changes lazily)."""
        want = _zone_signature(expected)
        got: list[Zone] | None = None
        for delay in (0.5, 1, 1.5, 2, 2, 3, 3, 4):
            await asyncio.sleep(delay)
            got = await self.async_read_zones()
            if got is not None and _zone_signature(got) == want:
                self.zones, self.zones_pending, self.zones_error = got, False, None
                self._notify()
                return
        self.zones_pending = False
        if got is None:
            self.zones_error = "could not read the zones back from the device"
        else:
            self.zones = got
            self.zones_error = "device kept a different zone list"
        LOGGER.warning("%s: %s", self.name, self.zones_error)
        self._notify()

    async def _async_apply_zones(self, zones: list[Zone]) -> None:
        """Show the written zones immediately and confirm them in the background."""
        self.zones = sorted(zones, key=lambda zone: zone.zone_id)
        self.zones_pending, self.zones_error = True, None
        self._notify()
        self.hass.async_create_background_task(self._async_verify_zones(self.zones), f"{self.name} verify zones")

    @callback
    def _refresh_zones(self) -> None:
        raw = self._attr(SENSOR_ENDPOINT, CLUSTER_CONFIG, ATTR_ZONES) or []
        zones = [zone for zone in (parse_zone(item) for item in raw) if zone is not None]
        self.zones = sorted(zones, key=lambda zone: zone.zone_id)

    @callback
    def _on_attribute_updated(self, event: EventType, data: Any) -> None:
        # The client runs subscribers inside its listen loop: an exception here drops the
        # whole server connection, so never let one escape.
        try:
            self._handle_attribute_updated(data)
        except Exception:
            LOGGER.exception("%s: failed to handle attribute update %s", self.name, data)

    @callback
    def _on_node_event(self, event: EventType, data: MatterNodeEvent) -> None:
        try:
            self._handle_node_event(data)
        except Exception:
            LOGGER.exception("%s: failed to handle node event %s", self.name, data)

    def _handle_attribute_updated(self, data: Any) -> None:
        # data = (node_id, attribute_path, value)
        try:
            _, path, _ = data
        except (TypeError, ValueError):
            return
        _, cluster, attribute = (int(x) for x in path.split("/"))
        if cluster == CLUSTER_CONFIG and attribute == ATTR_ZONES:
            self._refresh_zones()
        elif cluster not in (CLUSTER_CONFIG, CLUSTER_RADAR, CLUSTER_LOCATION):
            return
        self._notify()

    def _handle_node_event(self, data: MatterNodeEvent) -> None:
        payload = _event_payload(data.data)
        if data.cluster_id == CLUSTER_LOCATION and data.event_id == EVENT_LOCATION_INFO:
            if data.endpoint_id != SENSOR_ENDPOINT:
                return
            raw_targets = _get(payload, "targets", 0, default=[]) if isinstance(payload, dict) else []
            self.targets = [t for t in (parse_target(item) for item in raw_targets or []) if t is not None]
            self.targets_updated = dt_util.utcnow()
            self._notify()
        elif data.cluster_id == CLUSTER_RADAR and data.event_id == EVENT_MOTION_DETECTED:
            code = _get(payload, "motion", 0, default=None) if isinstance(payload, dict) else payload
            if not isinstance(code, int | str) or (isinstance(code, str) and not code.isdigit()):
                LOGGER.debug("%s: unexpected MotionDetected payload %r", self.name, data.data)
                return
            self.last_motion = MOTION_EVENTS.get(int(code), str(code))
            zone_ids = {ep: zid for zid, ep in self.zone_endpoints().items()}
            self.last_motion_zone = zone_ids.get(data.endpoint_id)
            self.last_motion_at = dt_util.utcnow()
            self._notify()

    # ---- commands --------------------------------------------------------

    async def _command(self, cluster_id: int, command_name: str, payload: dict[str, Any] | None = None) -> Any:
        return await self.matter_client.send_command(
            APICommand.DEVICE_COMMAND,
            node_id=self.node_id,
            endpoint_id=SENSOR_ENDPOINT,
            cluster_id=cluster_id,
            command_name=command_name,
            payload=payload or {},
        )

    async def async_set_zones(self, zones: list[Zone]) -> None:
        """Replace all zones on the device."""
        if len(zones) > self.max_zones:
            raise ValueError(f"the device supports at most {self.max_zones} zones")
        ids = [zone.zone_id for zone in zones]
        if len(set(ids)) != len(ids) or any(not 1 <= i <= self.max_zones for i in ids):
            raise ValueError(f"zone ids must be unique and between 1 and {self.max_zones}")
        result = await self._command(CLUSTER_CONFIG, "SetZones", {"zones": [zone.as_payload() for zone in zones]})
        self._check_status(result)
        await self._async_apply_zones(zones)

    async def async_append_zone(self, zone: Zone) -> None:
        """Add or replace one zone."""
        result = await self._command(CLUSTER_CONFIG, "AppendZone", {"zone": zone.as_payload()})
        self._check_status(result)
        await self._async_apply_zones([z for z in self.zones if z.zone_id != zone.zone_id] + [zone])

    async def async_remove_zone(self, zone_id: int) -> None:
        """Remove one zone."""
        result = await self._command(CLUSTER_CONFIG, "RemoveZone", {"zoneId": zone_id})
        self._check_status(result)
        await self._async_apply_zones([z for z in self.zones if z.zone_id != zone_id])

    async def async_start_learning(self) -> None:
        """Kick off the AI space background learning."""
        await self._command(CLUSTER_CONFIG, "EnableAiSpaceBackgroundLearning")

    async def async_subscribe_location(self, timeout: int = LOCATION_SUBSCRIPTION_S) -> None:
        """Ask the device to stream LocationInfo events for `timeout` seconds."""
        await self._command(CLUSTER_LOCATION, "SubscribeLocationData", {"timeout": timeout})

    async def async_set_live_tracking(self, enabled: bool) -> None:
        """Keep the location stream alive while enabled."""
        self.live_tracking = enabled
        if self._renew_task:
            self._renew_task.cancel()
            self._renew_task = None
        if enabled:
            self._renew_task = self.hass.async_create_background_task(
                self._renew_loop(), f"{self.name} location stream"
            )
        self._notify()

    async def _renew_loop(self) -> None:
        while True:
            try:
                await self.async_subscribe_location()
            except Exception as err:
                LOGGER.warning("%s: location subscription failed: %s", self.name, err)
                await asyncio.sleep(60)
                continue
            await asyncio.sleep(LOCATION_RENEW_S)

    @staticmethod
    def _check_status(result: Any) -> None:
        """Raise on a non-zero zone command status."""
        status = None
        if isinstance(result, dict):
            status = _get(result, "status", 0, default=None)
            if status is None and "Any" in result and isinstance(result["Any"], dict):
                status = _get(result["Any"], "status", 0, default=None)
        if status not in (None, 0):
            names = {
                1: "invalid argument",
                2: "invalid state",
                3: "resource exhausted",
                4: "busy",
                5: "duplicate zone id",
            }
            raise ValueError(f"device rejected the zone command: {names.get(int(status), status)}")
