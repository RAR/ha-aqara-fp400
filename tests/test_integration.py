"""Tests for the aqara_fp400_matter custom integration (not part of core; lives in ~/fp400/hacs)."""

from __future__ import annotations

import base64
from datetime import timedelta
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from matter_server.common.models import APICommand, EventType, MatterNodeEvent
import pytest
from tests.common import MockConfigEntry, async_fire_time_changed
import voluptuous as vol

from .common import setup_integration_with_node_fixture, trigger_subscription_callback

CLUSTER_CONFIG = 0x115FFC0A
CLUSTER_LOCATION = 0x115FFC0C
CLUSTER_RADAR = 0x115FFC0B
TARGETS_EXPIRE_S = 30  # const.TARGETS_EXPIRE_S

# zone 1 = cells (4,8) and (4,9): bits 4*16+8 = 72, 73 -> byte 9 = 0b11000000
ZONE_MASK = bytes(9) + bytes([0xC0]) + bytes(30)


@pytest.fixture
async def fp400(hass: HomeAssistant, matter_client: MagicMock, enable_custom_integrations: None):
    """Matter integration with the FP400 fixture + the custom integration."""
    node = await setup_integration_with_node_fixture(
        hass,
        "aqara_presence_fp400",
        matter_client,
        {
            "1/291503114/16": [
                {"zoneId": 1, "zoneType": 0, "cells": base64.b64encode(ZONE_MASK).decode(), "enabled": True}
            ]
        },
    )
    matter_client.send_command.return_value = {"status": 0}
    matter_client.read_attribute.return_value = {}
    entry = MockConfigEntry(domain="aqara_fp400_matter", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return node


async def test_entities(hass: HomeAssistant, fp400) -> None:
    """Entities exist and the zone attribute is decoded."""
    zones = hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_zones")
    assert zones is not None, [s.entity_id for s in hass.states.async_all() if "fp400" in s.entity_id]
    assert zones.state == "1"
    assert zones.attributes["zones"] == [
        {"id": 1, "type": 0, "proximity_detection": True, "cells": [[4, 8], [4, 9]], "name": None}
    ]
    assert hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_tracked_people").state == "0"
    assert hass.states.get("switch.aqara_spatial_multi_sensor_fp400_radar_live_tracking").state == "off"
    assert hass.states.get("button.aqara_spatial_multi_sensor_fp400_radar_clear_zones") is not None

    # own device, linked to the Matter device
    registry = er.async_get(hass)
    entity = registry.async_get("sensor.aqara_spatial_multi_sensor_fp400_radar_zones")
    matter_entity = registry.async_get("sensor.aqara_spatial_multi_sensor_fp400_illuminance")
    device = dr.async_get(hass).async_get(entity.device_id)
    assert device.via_device_id == matter_entity.device_id


async def test_location_event(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """LocationInfo events become tracked targets."""
    event = MatterNodeEvent(
        node_id=fp400.node_id,
        endpoint_id=1,
        cluster_id=CLUSTER_LOCATION,
        event_id=0,
        event_number=1,
        priority=1,
        timestamp=0,
        timestamp_type=0,
        data={"TLVValue": {"0": [{"0": 0, "1": -5, "2": 228, "3": 1032, "4": 2, "5": 0, "6": 0, "7": 255, "8": 1}]}},
    )
    await trigger_subscription_callback(hass, matter_client, EventType.NODE_EVENT, event, node_id=fp400.node_id)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_tracked_people")
    assert state.state == "1"
    assert state.attributes["targets"] == [
        {
            "id": 0,
            "x": -5,
            "y": 228,
            "row": 4,
            "col": 8,
            "activity": "active",
            "type": "person",
            "posture": None,
            "dynamic_points": 1032,
            "micro_motion_points": 2,
            "tracking_state": 0,
            "zones": [1],
        }
    ]

    # labelled payloads: the person uses the names matterjs-server used before PR #1269, the fan (field 6 = 2)
    # Aqara's own from PR #1298; the fan is listed but not counted, and still because its dynamic point count is 0
    event.data = {
        "location": [
            {
                "targetId": 1,
                "x": 10,
                "y": 100,
                "cell": 0x0207,
                "activityState": 1,
                "fallState": 1010,
                "postureState": 0,
            },
            {
                "targetId": 2,
                "targetXCoordinate": 60,
                "targetYCoordinate": 300,
                "dynPnts": 0,
                "mmsPnts": 40,
                "trackingState": 4010,
                "targetType": 2,
                "targetPosture": 5,
                "targetZoneId": 1,
            },
        ]
    }
    await trigger_subscription_callback(hass, matter_client, EventType.NODE_EVENT, event, node_id=fp400.node_id)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_tracked_people")
    assert state.state == "1"
    assert state.attributes["targets"][0]["col"] == 7
    assert state.attributes["targets"][0]["activity"] == "active"
    assert state.attributes["targets"][0]["tracking_state"] == 1010
    assert state.attributes["targets"][1]["type"] == "fan"
    assert state.attributes["targets"][1]["activity"] == "still"
    assert state.attributes["targets"][1]["micro_motion_points"] == 40
    assert state.attributes["targets"][1]["posture"] == "suspected_fall"
    assert state.attributes["targets"][1]["zones"] == [1]
    assert state.attributes["targets"][1]["col"] == 6

    # forgetting a target sends the command and drops it locally
    device_id = er.async_get(hass).async_get("sensor.aqara_spatial_multi_sensor_fp400_radar_zones").device_id
    matter_client.send_command.return_value = {"status": 0}
    await hass.services.async_call(
        "aqara_fp400_matter", "remove_target", {"device_id": device_id, "target_id": 1}, blocking=True
    )
    call = matter_client.send_command.call_args
    assert call.args[0] == APICommand.DEVICE_COMMAND
    assert call.kwargs["cluster_id"] == CLUSTER_LOCATION
    assert call.kwargs["command_name"] == "RemoveDetectionTarget"
    assert call.kwargs["payload"] == {"targetId": 1}
    assert hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_tracked_people").state == "0"

    motion = MatterNodeEvent(
        node_id=fp400.node_id,
        endpoint_id=1,
        cluster_id=CLUSTER_RADAR,
        event_id=0,
        event_number=2,
        priority=1,
        timestamp=0,
        timestamp_type=0,
        data={"TLVValue": {"0": 6}},
    )
    await trigger_subscription_callback(hass, matter_client, EventType.NODE_EVENT, motion, node_id=fp400.node_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_last_motion").state == "access"


async def test_set_zones_service(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """set_zones sends SetZones with the right bitmask and updates the sensor."""
    matter_entity = er.async_get(hass).async_get("sensor.aqara_spatial_multi_sensor_fp400_illuminance")
    device = dr.async_get(hass).async_get(matter_entity.device_id)  # the Matter device also resolves
    response = await hass.services.async_call(
        "aqara_fp400_matter",
        "set_zones",
        {
            "device_id": device.id,
            "zones": [{"id": 2, "cells": "3-4,6-10"}, {"id": 5, "cells": [[0, 0]], "proximity_detection": False}],
        },
        blocking=True,
        return_response=True,
    )
    await hass.async_block_till_done()
    call = matter_client.send_command.call_args
    assert call.args[0] == APICommand.DEVICE_COMMAND
    assert call.kwargs["cluster_id"] == CLUSTER_CONFIG
    assert call.kwargs["command_name"] == "SetZones"
    zones = call.kwargs["payload"]["zones"]
    assert [z["zoneId"] for z in zones] == [2, 5]
    mask = zones[0]["cells"]
    assert len(mask) == 40
    # rows 3-4, cols 6-10 -> bits row*16+col
    bits = {i for i in range(320) if mask[i // 8] & (0x80 >> (i % 8))}
    assert bits == {3 * 16 + c for c in range(6, 11)} | {4 * 16 + c for c in range(6, 11)}
    assert zones[1]["cells"][0] == 0x80 and zones[1]["enabled"] is False
    # Both label sets go out so the write works before and after matterjs-server#1269 (1.5.6).
    assert zones[1]["zoneBitmask"] == zones[1]["cells"]
    assert zones[1]["zoneProximityDetectionEnable"] is False and zones[0]["zoneProximityDetectionEnable"] is True
    assert response["zones"][0]["id"] == 2
    state = hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_zones")
    assert state.state == "2"
    assert state.attributes["pending"] is True


async def test_live_tracking_switch(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """The switch subscribes to location data."""
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.aqara_spatial_multi_sensor_fp400_radar_live_tracking"}, blocking=True
    )
    await hass.async_block_till_done()
    call = matter_client.send_command.call_args
    assert call.kwargs["cluster_id"] == CLUSTER_LOCATION
    assert call.kwargs["command_name"] == "SubscribeLocationData"
    assert call.kwargs["payload"] == {"timeout": 3600}
    assert hass.states.get("switch.aqara_spatial_multi_sensor_fp400_radar_live_tracking").state == "on"
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.aqara_spatial_multi_sensor_fp400_radar_live_tracking"},
        blocking=True,
    )
    assert hass.states.get("switch.aqara_spatial_multi_sensor_fp400_radar_live_tracking").state == "off"


async def test_config_entities(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """Install settings are exposed and written through the server."""
    assert hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_install_status").state == "tilted_facing_down"
    assert hass.states.get("select.aqara_spatial_multi_sensor_fp400_radar_install_mode").state == "side_mount"
    height = hass.states.get("number.aqara_spatial_multi_sensor_fp400_radar_install_height")
    assert height.state == "2000.0"
    assert height.attributes["min"] == 1500 and height.attributes["max"] == 4000

    matter_client.send_command.return_value = [
        {"Path": {"EndpointId": 1, "ClusterId": CLUSTER_CONFIG, "AttributeId": 4}, "Status": 0}
    ]
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.aqara_spatial_multi_sensor_fp400_radar_install_height", "value": 2200},
        blocking=True,
    )
    call = matter_client.send_command.call_args
    assert call.args[0] == APICommand.WRITE_ATTRIBUTE
    assert call.kwargs["attribute_path"] == f"1/{CLUSTER_CONFIG}/4" and call.kwargs["value"] == 2200
    assert hass.states.get("number.aqara_spatial_multi_sensor_fp400_radar_install_height").state == "2200.0"


async def test_reloads_with_matter(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """A Matter integration reload (new client) reloads this integration too."""
    from homeassistant.config_entries import ConfigEntryState

    matter_entry = hass.config_entries.async_loaded_entries("matter")[0]
    own_entry = hass.config_entries.async_loaded_entries("aqara_fp400_matter")[0]
    setups_before = matter_client.subscribe_events.call_count

    await hass.config_entries.async_reload(matter_entry.entry_id)
    await hass.async_block_till_done()

    assert matter_entry.state is ConfigEntryState.LOADED
    assert own_entry.state is ConfigEntryState.LOADED
    assert matter_client.subscribe_events.call_count > setups_before
    assert hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_zones").state == "1"


async def _push_attribute(hass: HomeAssistant, matter_client: MagicMock, node, path: str, value) -> None:
    """Fire ATTRIBUTE_UPDATED the way the client does: subscribers get only the value.

    The path reaches us solely through the subscription's attr_path_filter, and the client
    does not store the value in node_data for vendor clusters — the integration must.
    """
    await trigger_subscription_callback(
        hass,
        matter_client,
        EventType.ATTRIBUTE_UPDATED,
        value,
        node_id=node.node_id,
        attribute_path=path,
    )


async def test_live_tracking_survives_matter_reload(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """A Matter reconnect reloads us; live tracking must come back on, not restore as off."""
    switch = "switch.aqara_spatial_multi_sensor_fp400_radar_live_tracking"
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()

    matter_entry = hass.config_entries.async_loaded_entries("matter")[0]
    await hass.config_entries.async_reload(matter_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(switch).state == "on"
    own_entry = hass.config_entries.async_loaded_entries("aqara_fp400_matter")[0]
    assert all(fp.live_tracking for fp in own_entry.runtime_data.nodes.values())


async def test_targets_cleared_when_stale(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """Positions are dropped when the stream stops or the device reports an empty room."""
    people = "sensor.aqara_spatial_multi_sensor_fp400_radar_tracked_people"
    switch = "switch.aqara_spatial_multi_sensor_fp400_radar_live_tracking"
    event = MatterNodeEvent(
        node_id=fp400.node_id,
        endpoint_id=1,
        cluster_id=CLUSTER_LOCATION,
        event_id=0,
        event_number=1,
        priority=1,
        timestamp=0,
        timestamp_type=0,
        data={"targets": [{"targetId": 1, "x": 10, "y": 100, "cell": 0x0207, "activityState": 1}]},
    )

    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await trigger_subscription_callback(hass, matter_client, EventType.NODE_EVENT, event, node_id=fp400.node_id)
    await hass.async_block_till_done()
    assert hass.states.get(people).state == "1"

    await _push_attribute(hass, matter_client, fp400, f"1/{CLUSTER_RADAR}/2", 0)
    await hass.async_block_till_done()
    assert hass.states.get(people).state == "0"

    await trigger_subscription_callback(hass, matter_client, EventType.NODE_EVENT, event, node_id=fp400.node_id)
    await hass.async_block_till_done()
    assert hass.states.get(people).state == "1"
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert hass.states.get(people).state == "0"


async def test_late_frame_after_room_empties_expires(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """A LocationInfo frame that lands after the occupancy clear does not stay forever."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from tests.common import async_fire_time_changed

    people = "sensor.aqara_spatial_multi_sensor_fp400_radar_tracked_people"
    event = MatterNodeEvent(
        node_id=fp400.node_id,
        endpoint_id=1,
        cluster_id=CLUSTER_LOCATION,
        event_id=0,
        event_number=1,
        priority=1,
        timestamp=0,
        timestamp_type=0,
        data={"targets": [{"targetId": 1, "x": 10, "y": 100, "cell": 0x0207, "activityState": 1}]},
    )

    # Occupied room: positions are kept however long the stream stays quiet (someone sitting still).
    await _push_attribute(hass, matter_client, fp400, "1/1030/0", 1)
    await trigger_subscription_callback(hass, matter_client, EventType.NODE_EVENT, event, node_id=fp400.node_id)
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=TARGETS_EXPIRE_S * 4))
    await hass.async_block_till_done()
    assert hass.states.get(people).state == "1"

    # The room empties, then one last frame arrives after the clear.
    await _push_attribute(hass, matter_client, fp400, "1/1030/0", 0)
    await hass.async_block_till_done()
    assert hass.states.get(people).state == "0"
    await trigger_subscription_callback(hass, matter_client, EventType.NODE_EVENT, event, node_id=fp400.node_id)
    await hass.async_block_till_done()
    assert hass.states.get(people).state == "1"

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=TARGETS_EXPIRE_S + 1))
    await hass.async_block_till_done()
    assert hass.states.get(people).state == "0"

    # Occupancy coming back before the timeout keeps the frame.
    await trigger_subscription_callback(hass, matter_client, EventType.NODE_EVENT, event, node_id=fp400.node_id)
    await _push_attribute(hass, matter_client, fp400, "1/1030/0", 1)
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=TARGETS_EXPIRE_S * 4))
    await hass.async_block_till_done()
    assert hass.states.get(people).state == "1"


async def test_region_push_updates_sensor(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """A region change pushed by the subscription shows up without waiting for the poll."""
    mask = bytearray(40)
    mask[0] = 0x80  # cell (0, 0)
    await _push_attribute(hass, matter_client, fp400, f"1/{CLUSTER_CONFIG}/18", base64.b64encode(bytes(mask)).decode())
    await hass.async_block_till_done()
    state = hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_regions")
    assert state.attributes["regions"]["entry_exit"] == [[0, 0]]


async def test_region_reverts_when_device_keeps_its_own(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """A region the device does not accept is shown as the device has it, with an error, not as drawn."""
    from datetime import timedelta

    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.util import dt as dt_util
    from tests.common import async_fire_time_changed

    regions = "sensor.aqara_spatial_multi_sensor_fp400_radar_regions"
    full = base64.b64encode(bytes(40)).decode()  # monitoring stores exclusions: none excluded = whole grid
    path = f"1/{CLUSTER_CONFIG}/20"
    await _push_attribute(hass, matter_client, fp400, path, full)
    assert len(hass.states.get(regions).attributes["regions"]["monitoring"]) == 320

    # The device answers every read-back with the full grid: the drawing must not stick.
    matter_client.read_attribute.return_value = {path: full}
    await hass.services.async_call(
        "aqara_fp400_matter",
        "set_region",
        {"device_id": _radar_device(hass), "region": "monitoring", "cells": "0-3,0-3"},
        blocking=True,
    )
    state = hass.states.get(regions)
    assert state.attributes["pending"]["monitoring"] is True
    assert len(state.attributes["regions"]["monitoring"]) == 16
    for _ in range(6):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
        await hass.async_block_till_done()
    state = hass.states.get(regions)
    assert state.attributes["pending"]["monitoring"] is False
    assert state.attributes["error"]["monitoring"] == "device kept a different region"
    assert len(state.attributes["regions"]["monitoring"]) == 320

    # No answer at all: fall back to what the device had before the write.
    matter_client.read_attribute.side_effect = TimeoutError
    await hass.services.async_call(
        "aqara_fp400_matter",
        "set_region",
        {"device_id": _radar_device(hass), "region": "monitoring", "cells": "0-3,0-3"},
        blocking=True,
    )
    for _ in range(6):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
        await hass.async_block_till_done()
    state = hass.states.get(regions)
    assert state.attributes["error"]["monitoring"] == "could not read the region back from the device"
    assert len(state.attributes["regions"]["monitoring"]) == 320

    # A rejected write reverts straight away.
    matter_client.read_attribute.side_effect = None
    matter_client.send_command.return_value = [{"Path": path, "Status": 134}]
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "aqara_fp400_matter",
            "set_region",
            {"device_id": _radar_device(hass), "region": "monitoring", "cells": "0-3,0-3"},
            blocking=True,
        )
    state = hass.states.get(regions)
    assert "status 134" in state.attributes["error"]["monitoring"]
    assert len(state.attributes["regions"]["monitoring"]) == 320


async def test_vendor_attribute_push_updates_sensors(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """Pushed vendor-cluster values reach the entities (the client does not cache them for us)."""
    await _push_attribute(hass, matter_client, fp400, f"1/{CLUSTER_RADAR}/2", 3)
    await _push_attribute(hass, matter_client, fp400, f"1/{CLUSTER_LOCATION}/7", 1)
    assert hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_human_count").state == "3"
    assert hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_activity_state").state == "active"
    await _push_attribute(hass, matter_client, fp400, f"1/{CLUSTER_RADAR}/2", 2)
    assert hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_human_count").state == "2"


async def test_availability_follows_node(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """Radar entities go unavailable with the Matter node and come back with it."""
    people = "sensor.aqara_spatial_multi_sensor_fp400_radar_tracked_people"
    fp400.node_data.available = False
    await trigger_subscription_callback(hass, matter_client, EventType.NODE_UPDATED, fp400, node_id=fp400.node_id)
    assert hass.states.get(people).state == "unavailable"

    fp400.node_data.available = True
    await trigger_subscription_callback(hass, matter_client, EventType.NODE_UPDATED, fp400, node_id=fp400.node_id)
    assert hass.states.get(people).state == "0"


async def test_command_errors_are_readable(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """Client failures reach the user as HomeAssistantError, not an unknown error."""
    from homeassistant.exceptions import HomeAssistantError
    from matter_server.common.errors import InvalidCommand

    matter_client.send_command.side_effect = InvalidCommand("cluster 0x115ffc0a unknown")
    with pytest.raises(HomeAssistantError, match="cluster 0x115ffc0a unknown"):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.aqara_spatial_multi_sensor_fp400_radar_start_background_learning"},
            blocking=True,
        )
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": "select.aqara_spatial_multi_sensor_fp400_radar_install_mode", "option": "top_mount"},
            blocking=True,
        )


async def test_location_failure_warns_once(
    hass: HomeAssistant, matter_client: MagicMock, fp400, caplog: pytest.LogCaptureFixture
) -> None:
    """A server that cannot stream locations logs one warning, not one a minute."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from tests.common import async_fire_time_changed

    matter_client.send_command.side_effect = RuntimeError("no such cluster")
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.aqara_spatial_multi_sensor_fp400_radar_live_tracking"}, blocking=True
    )
    await hass.async_block_till_done()
    for minutes in (1, 2, 3):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=minutes, seconds=5))
        await hass.async_block_till_done()
    assert matter_client.send_command.call_count >= 2
    warnings = [r for r in caplog.records if "location subscription failed" in r.message and r.levelname == "WARNING"]
    assert len(warnings) == 1


def _radar_device(hass: HomeAssistant) -> str:
    return er.async_get(hass).async_get("sensor.aqara_spatial_multi_sensor_fp400_radar_zones").device_id


def _motion_event(node_id: int, endpoint: int, code: int, number: int = 7) -> MatterNodeEvent:
    return MatterNodeEvent(
        node_id=node_id,
        endpoint_id=endpoint,
        cluster_id=CLUSTER_RADAR,
        event_id=0,
        event_number=number,
        priority=1,
        timestamp=0,
        timestamp_type=0,
        data={"TLVValue": {"0": code}},
    )


async def test_zone_names(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """Names are stored by the integration (keyed by serial) and shown on zones and motion sensors."""
    device_id = _radar_device(hass)
    await hass.services.async_call(
        "aqara_fp400_matter", "set_zone_name", {"device_id": device_id, "zone_id": 1, "name": " Bed "}, blocking=True
    )
    await hass.async_block_till_done()
    zones = hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_zones")
    assert zones.attributes["zones"][0]["name"] == "Bed"
    entry = hass.config_entries.async_entries("aqara_fp400_matter")[0]
    serial = str(fp400.node_data.attributes["0/40/15"])
    assert entry.data["zone_names"] == {serial: {"1": "Bed"}}

    fp400.node_data.attributes["3/291503115/1"] = 1  # zone 1's child endpoint
    await trigger_subscription_callback(
        hass, matter_client, EventType.NODE_EVENT, _motion_event(fp400.node_id, 3, 0), node_id=fp400.node_id
    )
    motion = hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_last_motion")
    assert motion.state == "enter"
    assert motion.attributes["zone"] == 1 and motion.attributes["zone_name"] == "Bed"

    await hass.services.async_call(
        "aqara_fp400_matter", "set_zone_name", {"device_id": device_id, "zone_id": 1}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_zones").attributes["zones"][0]["name"] is None
    assert hass.config_entries.async_entries("aqara_fp400_matter")[0].data["zone_names"] == {serial: {}}


async def test_zone_motion_sensors_follow_zones(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """One last-motion sensor per zone: it reports that zone's events and goes away with the zone."""
    zone1 = "sensor.aqara_spatial_multi_sensor_fp400_radar_zone_1_last_motion"
    zone2 = "sensor.aqara_spatial_multi_sensor_fp400_radar_zone_2_last_motion"
    assert hass.states.get(zone1).state == "unknown"
    assert hass.states.get(zone2) is None

    fp400.node_data.attributes["3/291503115/1"] = 1
    await trigger_subscription_callback(
        hass, matter_client, EventType.NODE_EVENT, _motion_event(fp400.node_id, 3, 1), node_id=fp400.node_id
    )
    assert hass.states.get(zone1).state == "left"
    assert hass.states.get(zone1).attributes["zone"] == 1
    # the room's own event does not touch the zone sensor
    await trigger_subscription_callback(
        hass, matter_client, EventType.NODE_EVENT, _motion_event(fp400.node_id, 1, 6, 8), node_id=fp400.node_id
    )
    assert hass.states.get(zone1).state == "left"
    assert hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_last_motion").state == "access"

    await hass.services.async_call(
        "aqara_fp400_matter",
        "set_zones",
        {"device_id": _radar_device(hass), "zones": [{"id": 2, "cells": "3-4,6-10"}]},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(zone1) is None
    assert er.async_get(hass).async_get(zone1) is None
    assert hass.states.get(zone2).state == "unknown"


async def test_unrecognized_motion_code(
    hass: HomeAssistant, matter_client: MagicMock, fp400, caplog: pytest.LogCaptureFixture
) -> None:
    """A code missing from MOTION_EVENTS is a valid enum state, logged once per code."""
    last = "sensor.aqara_spatial_multi_sensor_fp400_radar_last_motion"
    zone1 = "sensor.aqara_spatial_multi_sensor_fp400_radar_zone_1_last_motion"
    fp400.node_data.attributes["3/291503115/1"] = 1
    for number in (20, 21):
        event = _motion_event(fp400.node_id, 3, 42, number)
        await trigger_subscription_callback(hass, matter_client, EventType.NODE_EVENT, event, node_id=fp400.node_id)
        await hass.async_block_till_done()
    assert hass.states.get(last).state == "unrecognized"
    assert hass.states.get(zone1).state == "unrecognized"
    warnings = [r for r in caplog.records if "unrecognized MotionDetected code 42" in r.message]
    assert len(warnings) == 1
    # Known codes keep working afterwards.
    await trigger_subscription_callback(
        hass, matter_client, EventType.NODE_EVENT, _motion_event(fp400.node_id, 3, 0, 22), node_id=fp400.node_id
    )
    await hass.async_block_till_done()
    assert hass.states.get(last).state == "enter"


async def test_set_zones_error_code_label(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """matterjs-server 1.6.0 labels the zone response field errorCode; a rejection must still raise."""
    from homeassistant.exceptions import HomeAssistantError

    call = {"device_id": _radar_device(hass), "zones": [{"id": 2, "cells": "3-4,6-10"}]}
    matter_client.send_command.return_value = {"errorCode": 5}
    with pytest.raises(HomeAssistantError, match="duplicate zone id"):
        await hass.services.async_call("aqara_fp400_matter", "set_zones", call, blocking=True)
    matter_client.send_command.return_value = {"errorCode": 0}
    await hass.services.async_call("aqara_fp400_matter", "set_zones", call, blocking=True)


@pytest.mark.parametrize("cells", ["10-5,0-15", "0-20,0-15", "0-19,0-16", "3-4", "a,1", "3-4,", "1-,2"])
async def test_cell_spec_rejects_reversed_or_out_of_grid_ranges(
    hass: HomeAssistant, matter_client: MagicMock, fp400, cells: str
) -> None:
    """A bad range is a validation error, never an empty list (which means the full grid for monitoring)."""
    matter_client.send_command.reset_mock()
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            "aqara_fp400_matter",
            "set_region",
            {"device_id": _radar_device(hass), "region": "monitoring", "cells": cells},
            blocking=True,
        )
    matter_client.send_command.assert_not_called()


async def test_learning_countdown(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """Start learning exposes when the device will be done and reads the zones back afterwards."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from tests.common import async_fire_time_changed

    zones = "sensor.aqara_spatial_multi_sensor_fp400_radar_zones"
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.aqara_spatial_multi_sensor_fp400_radar_start_background_learning"},
        blocking=True,
    )
    await hass.async_block_till_done()
    call = matter_client.send_command.call_args
    assert call.kwargs["cluster_id"] == CLUSTER_CONFIG
    assert call.kwargs["command_name"] == "EnableAiSpaceBackgroundLearning"
    state = hass.states.get(zones)
    assert state.attributes["learning_until"] is not None
    assert state.attributes["learning_zones"] is None

    matter_client.read_attribute.return_value = {
        f"1/{CLUSTER_CONFIG}/16": [
            {"zoneId": 1, "zoneType": 0, "cells": base64.b64encode(ZONE_MASK).decode(), "enabled": True},
            {"zoneId": 3, "zoneType": 0, "cells": base64.b64encode(ZONE_MASK).decode(), "enabled": True},
        ]
    }
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()
    state = hass.states.get(zones)
    assert state.attributes["learning_until"] is None
    assert state.attributes["learning_zones"] == 2
    assert state.state == "2"
    assert hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_zone_3_last_motion") is not None


async def test_node_removed(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """Removing the FP400 from Matter removes the radar device and its entities."""
    zones = "sensor.aqara_spatial_multi_sensor_fp400_radar_zones"
    device_id = er.async_get(hass).async_get(zones).device_id

    await trigger_subscription_callback(
        hass, matter_client, EventType.NODE_REMOVED, fp400.node_id, node_id=fp400.node_id
    )
    await hass.async_block_till_done()

    assert dr.async_get(hass).async_get(device_id) is None
    assert er.async_get(hass).async_get(zones) is None


def _old_domain_folder(tmp_path, documentation: str):
    """Write an aqara_fp400 manifest under tmp_path, which the test then uses as the config dir."""
    import json

    folder = tmp_path / "custom_components" / "aqara_fp400"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"domain": "aqara_fp400", "documentation": documentation}))
    return folder


async def test_migrates_legacy_entry(
    hass: HomeAssistant, matter_client: MagicMock, enable_custom_integrations: None, tmp_path
) -> None:
    """A setup under the pre-1.4.0 domain is taken over: device, entities (with their ids) and zone names."""
    from unittest.mock import patch

    _old_domain_folder(tmp_path, "https://github.com/RAR/ha-aqara-fp400")
    node = await setup_integration_with_node_fixture(hass, "aqara_presence_fp400", matter_client, {})
    matter_client.send_command.return_value = {"status": 0}
    matter_client.read_attribute.return_value = {}
    serial = str(node.node_data.attributes["0/40/15"])
    old = MockConfigEntry(domain="aqara_fp400", data={"zone_names": {serial: {"1": "Bed"}}})
    old.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=old.entry_id, identifiers={("aqara_fp400", str(node.node_id))}, name="Old radar"
    )
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        "sensor",
        "aqara_fp400",
        f"aqara_fp400_{node.node_id}_zones",
        suggested_object_id="my_zones",
        config_entry=old,
        device_id=device.id,
    )
    matter_client.read_attribute.return_value = {
        "1/291503114/16": [{"zoneId": 1, "zoneType": 0, "zoneBitmask": base64.b64encode(ZONE_MASK).decode()}]
    }

    entry = MockConfigEntry(domain="aqara_fp400_matter", data={})
    entry.add_to_hass(hass)
    with patch.object(hass.config, "path", side_effect=lambda *parts: str(tmp_path.joinpath(*parts))):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.config_entries.async_entries("aqara_fp400") == []
    assert entry.data["zone_names"] == {serial: {"1": "Bed"}}
    moved = entity_registry.async_get("sensor.my_zones")
    assert moved is not None
    assert moved.platform == "aqara_fp400_matter"
    assert moved.unique_id == f"aqara_fp400_matter_{node.node_id}_zones"
    assert moved.config_entry_id == entry.entry_id
    assert device_registry.async_get(device.id).identifiers == {("aqara_fp400_matter", str(node.node_id))}
    assert device_registry.async_get(device.id).config_entry_id == entry.entry_id
    assert hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_zones") is None
    assert hass.states.get("sensor.my_zones").attributes["zones"][0]["name"] == "Bed"


async def test_leaves_other_integration_with_old_domain(
    hass: HomeAssistant, matter_client: MagicMock, enable_custom_integrations: None, tmp_path
) -> None:
    """A different integration installed under aqara_fp400 is neither migrated nor touched."""
    from unittest.mock import patch

    other = _old_domain_folder(tmp_path, "https://example.org")
    node = await setup_integration_with_node_fixture(hass, "aqara_presence_fp400", matter_client, {})
    matter_client.send_command.return_value = {"status": 0}
    matter_client.read_attribute.return_value = {}
    old = MockConfigEntry(domain="aqara_fp400", data={"zone_names": {"x": {"1": "Theirs"}}})
    old.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create("sensor", "aqara_fp400", "theirs", config_entry=old)

    entry = MockConfigEntry(domain="aqara_fp400_matter", data={})
    entry.add_to_hass(hass)
    with patch.object(hass.config, "path", side_effect=lambda *parts: str(tmp_path.joinpath(*parts))):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.config_entries.async_entries("aqara_fp400") == [old]
    assert entity_registry.async_get_entity_id("sensor", "aqara_fp400", "theirs") is not None
    assert "zone_names" not in entry.data
    assert (other / "manifest.json").is_file()
    assert hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_zones").state == "0"
    assert node.node_id in entry.runtime_data.nodes


async def test_leaves_old_entry_without_proof(
    hass: HomeAssistant, matter_client: MagicMock, enable_custom_integrations: None, tmp_path, caplog
) -> None:
    """Without the old folder nothing proves an aqara_fp400 entry is ours, so it is left alone."""
    from unittest.mock import patch

    node = await setup_integration_with_node_fixture(hass, "aqara_presence_fp400", matter_client, {})
    matter_client.send_command.return_value = {"status": 0}
    matter_client.read_attribute.return_value = {}
    old = MockConfigEntry(domain="aqara_fp400", data={"zone_names": {"x": {"1": "Unknown"}}})
    old.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create("sensor", "aqara_fp400", "unknown", config_entry=old)

    entry = MockConfigEntry(domain="aqara_fp400_matter", data={})
    entry.add_to_hass(hass)
    with patch.object(hass.config, "path", side_effect=lambda *parts: str(tmp_path.joinpath(*parts))):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.config_entries.async_entries("aqara_fp400") == [old]
    assert entity_registry.async_get_entity_id("sensor", "aqara_fp400", "unknown") is not None
    assert "zone_names" not in entry.data
    assert "cannot be confirmed" in caplog.text
    assert node.node_id in entry.runtime_data.nodes


ZONE1_OCCUPANCY = "binary_sensor.aqara_spatial_multi_sensor_fp400_radar_zone_1_occupancy"
OFF_DELAY = "number.aqara_spatial_multi_sensor_fp400_radar_zone_occupancy_off_delay"


def _location_event(node_id: int, targets: list[dict], number: int = 1) -> MatterNodeEvent:
    return MatterNodeEvent(
        node_id=node_id,
        endpoint_id=1,
        cluster_id=CLUSTER_LOCATION,
        event_id=0,
        event_number=number,
        priority=1,
        timestamp=0,
        timestamp_type=0,
        data={"targets": targets},
    )


async def test_zone_occupancy_from_tracking(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """With live tracking on, a zone is occupied while a tracked person is inside it (even sitting still)."""
    switch = "switch.aqara_spatial_multi_sensor_fp400_radar_live_tracking"
    state = hass.states.get(ZONE1_OCCUPANCY)
    assert state.state == "off"
    assert state.attributes["source"] == "events"
    assert hass.states.get(OFF_DELAY).state == "30.0"

    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert hass.states.get(ZONE1_OCCUPANCY).attributes["source"] == "tracking"
    # zone 1 = cells (4,8),(4,9); y=228 cm -> row 4, x=-5 -> col 8; the device does not tag the zone (older firmware)
    still_in_zone = {"targetId": 1, "x": -5, "y": 228, "dynPnts": 0, "mmsPnts": 10, "trackingState": 0}
    await trigger_subscription_callback(
        hass,
        matter_client,
        EventType.NODE_EVENT,
        _location_event(fp400.node_id, [still_in_zone]),
        node_id=fp400.node_id,
    )
    await hass.async_block_till_done()
    assert hass.states.get(ZONE1_OCCUPANCY).state == "on"

    # the person moves out: off only after the delay
    outside = {"targetId": 1, "x": -5, "y": 50, "dynPnts": 5}
    await trigger_subscription_callback(
        hass, matter_client, EventType.NODE_EVENT, _location_event(fp400.node_id, [outside], 2), node_id=fp400.node_id
    )
    await hass.async_block_till_done()
    assert hass.states.get(ZONE1_OCCUPANCY).state == "on"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert hass.states.get(ZONE1_OCCUPANCY).state == "off"

    # a fan in the zone does not count; the device's zone id does, whatever the position says
    fan = {"targetId": 2, "x": -5, "y": 228, "dynPnts": 0, "targetType": 2}
    tagged = {"targetId": 3, "x": 300, "y": 900, "dynPnts": 3, "targetZoneId": 1}
    await trigger_subscription_callback(
        hass, matter_client, EventType.NODE_EVENT, _location_event(fp400.node_id, [fan], 3), node_id=fp400.node_id
    )
    await hass.async_block_till_done()
    assert hass.states.get(ZONE1_OCCUPANCY).state == "off"
    await trigger_subscription_callback(
        hass, matter_client, EventType.NODE_EVENT, _location_event(fp400.node_id, [tagged], 4), node_id=fp400.node_id
    )
    await hass.async_block_till_done()
    assert hass.states.get(ZONE1_OCCUPANCY).state == "on"

    # the room going empty clears the zone without the delay once the delay is 0
    await hass.services.async_call("number", "set_value", {"entity_id": OFF_DELAY, "value": 0}, blocking=True)
    await _push_attribute(hass, matter_client, fp400, "1/1030/0", 0)
    await hass.async_block_till_done()
    assert hass.states.get(ZONE1_OCCUPANCY).state == "off"


async def test_zone_occupancy_from_events(hass: HomeAssistant, matter_client: MagicMock, fp400) -> None:
    """Without live tracking the zone's own enter/access and leave events drive the sensor."""
    await hass.services.async_call("number", "set_value", {"entity_id": OFF_DELAY, "value": 0}, blocking=True)
    fp400.node_data.attributes["3/291503115/1"] = 1  # endpoint 3 is zone 1
    await _push_attribute(hass, matter_client, fp400, "1/1030/0", 1)  # room occupied

    async def motion(code: int, number: int) -> None:
        await trigger_subscription_callback(
            hass,
            matter_client,
            EventType.NODE_EVENT,
            _motion_event(fp400.node_id, 3, code, number),
            node_id=fp400.node_id,
        )
        await hass.async_block_till_done()

    await motion(0, 1)  # enter
    assert hass.states.get(ZONE1_OCCUPANCY).state == "on"
    await motion(1, 2)  # left
    assert hass.states.get(ZONE1_OCCUPANCY).state == "off"
    await motion(6, 3)  # access
    assert hass.states.get(ZONE1_OCCUPANCY).state == "on"
    await _push_attribute(hass, matter_client, fp400, "1/1030/0", 0)  # room empty
    await hass.async_block_till_done()
    assert hass.states.get(ZONE1_OCCUPANCY).state == "off"

    # the sensor goes away with its zone
    await hass.services.async_call(
        "aqara_fp400_matter",
        "set_zones",
        {"device_id": _radar_device(hass), "zones": [{"id": 2, "cells": "3-4,6-10"}]},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(ZONE1_OCCUPANCY) is None
    assert hass.states.get("binary_sensor.aqara_spatial_multi_sensor_fp400_radar_zone_2_occupancy").state == "off"
