"""Constants and grid helpers for the Aqara FP400 integration."""

from __future__ import annotations

import base64
from collections.abc import Iterable
import logging

DOMAIN = "aqara_fp400"
LOGGER = logging.getLogger(__package__)

VENDOR_ID = 0x115F
PRODUCT_ID_FP400 = 0x2009

CLUSTER_CONFIG = 0x115FFC0A  # AmbientSensingConfiguration
CLUSTER_RADAR = 0x115FFC0B  # RadarSensingUnion
CLUSTER_LOCATION = 0x115FFC0C  # OccupantLocation

ATTR_INSTALL_MODE = 0
ATTR_SIDE_INSTALL = 2
ATTR_INSTALL_HEIGHT = 4
ATTR_INSTALL_HEIGHT_MIN = 5
ATTR_INSTALL_HEIGHT_MAX = 6
ATTR_INSTALL_STATUS = 7
ATTR_INSTALL_ANGLE = 8
ATTR_ZONES = 16
ATTR_MAX_ZONES = 17
ATTR_ACTIVITY_STATE = 7
ATTR_HUMAN_COUNT = 2
ATTR_ZONE_ID = 1
ATTR_COORDINATE_REVERSE = 45
ATTR_DETECTION_DIRECTION = 46
ATTR_PROXIMITY_LEVEL = 47

INSTALL_MODES = {0: "unknown", 1: "side_mount", 2: "top_mount"}
SIDE_INSTALLS = {0: "unknown", 1: "wall", 2: "left_corner", 3: "right_corner"}
COORDINATE_REVERSE = {0: "disabled", 1: "enabled", 2: "auto"}
DETECTION_DIRECTIONS = {0: "omnidirectional", 1: "left_right"}
PROXIMITY_LEVELS = {0: "far", 1: "medium", 2: "near"}
INSTALL_STATUSES = {
    0: "level_facing_up",
    1: "level_tilted_facing_up",
    2: "level_reverse_tilted_facing_up",
    3: "side_facing_forward",
    4: "side_reverse_facing_forward",
    5: "top_facing_down",
    6: "tilted_facing_down",
    7: "reverse_tilted_facing_down",
    8: "invalid",
}

EVENT_LOCATION_INFO = 0
EVENT_MOTION_DETECTED = 0

SENSOR_ENDPOINT = 1

# Detection grid: 16 columns x 20 rows of ~50 cm cells, row 0 nearest the sensor,
# column 8 straight ahead (columns grow as x decreases). bit = row * COLS + col, MSB first.
# Verified 2026-09-19 with single-column zones against the device's own zone assignment.
GRID_COLS = 16
GRID_ROWS = 20
MASK_BYTES = GRID_COLS * GRID_ROWS // 8
MAX_ZONES = 8
MAX_TARGETS = 3  # number of per-target x/y sensors created

LOCATION_SUBSCRIPTION_S = 3600  # device maximum
LOCATION_RENEW_S = 3300

ACTIVITY_STATES = {0: "unknown", 1: "active", 2: "still"}
MOTION_EVENTS = {
    0: "enter",
    1: "left",
    2: "left_in",
    3: "right_out",
    4: "right_in",
    5: "left_out",
    6: "access",
    7: "away",
}

CONF_NODE_ID = "node_id"

SERVICE_SET_ZONES = "set_zones"
SERVICE_CLEAR_ZONES = "clear_zones"
SERVICE_SUBSCRIBE_LOCATION = "subscribe_location"
SERVICE_START_LEARNING = "start_learning"


def cells_to_mask(cells: Iterable[tuple[int, int]]) -> bytes:
    """Encode (row, col) cells into the device's 40 byte bitmask."""
    mask = bytearray(MASK_BYTES)
    for row, col in cells:
        if not (0 <= row < GRID_ROWS and 0 <= col < GRID_COLS):
            raise ValueError(f"cell ({row}, {col}) outside the {GRID_ROWS}x{GRID_COLS} grid")
        bit = row * GRID_COLS + col
        mask[bit // 8] |= 0x80 >> (bit % 8)
    return bytes(mask)


def mask_to_cells(mask: bytes) -> list[list[int]]:
    """Decode the device bitmask into a sorted list of [row, col]."""
    cells = []
    for bit in range(GRID_ROWS * GRID_COLS):
        if mask[bit // 8] & (0x80 >> (bit % 8)):
            cells.append([bit // GRID_COLS, bit % GRID_COLS])
    return cells


def to_bytes(value: object) -> bytes:
    """Coerce a bitmask as delivered by the Matter server (bytes, base64 or hex) to bytes."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        if len(value) == MASK_BYTES * 2 and all(c in "0123456789abcdefABCDEF" for c in value):
            return bytes.fromhex(value)
        return base64.b64decode(value)
    if isinstance(value, list):
        return bytes(value)
    raise ValueError(f"unsupported bitmask value {value!r}")


def cell_from_index(cell: int) -> tuple[int, int]:
    """Split the LocationInfo cell field (row << 8 | col)."""
    return cell >> 8, cell & 0xFF
