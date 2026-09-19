"""Tests for the aqara_fp400 custom integration (not part of core; lives in ~/fp400/hacs)."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from matter_server.common.models import APICommand, EventType, MatterNodeEvent
import pytest
from tests.common import MockConfigEntry

from .common import setup_integration_with_node_fixture, trigger_subscription_callback

CLUSTER_CONFIG = 0x115FFC0A
CLUSTER_LOCATION = 0x115FFC0C
CLUSTER_RADAR = 0x115FFC0B

# zone 1 = cells (4,8) and (4,9): bits 88, 89 -> byte 11 = 0b11000000
ZONE_MASK = bytes(11) + bytes([0xC0]) + bytes(28)


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
    entry = MockConfigEntry(domain="aqara_fp400", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return node


async def test_entities(hass: HomeAssistant, fp400) -> None:
    """Entities exist and the zone attribute is decoded."""
    zones = hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_zones")
    assert zones is not None, [s.entity_id for s in hass.states.async_all() if "fp400" in s.entity_id]
    assert zones.state == "1"
    assert zones.attributes["zones"] == [{"id": 1, "type": 0, "enabled": True, "cells": [[4, 8], [4, 9]]}]
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
        {"id": 0, "x": -5, "y": 228, "row": 4, "col": 8, "activity": "still", "zones": [1]}
    ]

    # matter.js style payload with labels
    event.data = {"targets": [{"targetId": 1, "x": 10, "y": 100, "cell": 0x0207, "activityState": 1}]}
    await trigger_subscription_callback(hass, matter_client, EventType.NODE_EVENT, event, node_id=fp400.node_id)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_tracked_people")
    assert state.attributes["targets"][0]["col"] == 7
    assert state.attributes["targets"][0]["activity"] == "active"

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
        "aqara_fp400",
        "set_zones",
        {
            "device_id": device.id,
            "zones": [{"id": 2, "cells": "3-4,6-10"}, {"id": 5, "cells": [[0, 0]], "enabled": False}],
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
    # rows 3-4, cols 6-10 -> bits 66..70 and 86..90
    bits = {i for i in range(320) if mask[i // 8] & (0x80 >> (i % 8))}
    assert bits == {3 * 20 + c for c in range(6, 11)} | {4 * 20 + c for c in range(6, 11)}
    assert zones[1]["cells"][0] == 0x80 and zones[1]["enabled"] is False
    assert response["zones"][0]["id"] == 2
    assert hass.states.get("sensor.aqara_spatial_multi_sensor_fp400_radar_zones").state == "2"


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
