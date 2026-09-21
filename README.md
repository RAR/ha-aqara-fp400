# Aqara FP400 (Matter) for Home Assistant

Zones, live people positions and setup commands for the Aqara Spatial Multi-Sensor FP400 in Matter/Thread mode —
the parts the Aqara app does (when it works) that plain Matter does not expose.

It rides on Home Assistant's Matter integration: same server connection, same fabric, no extra pairing. Entities land
on a "<device> radar" device linked to the sensor's Matter device.

## Requirements

- Home Assistant 2026.9 or newer with the Matter integration and the FP400 commissioned.
- A Matter server that knows the FP400's vendor clusters. That is the OHF Matter Server with
  [this change](https://github.com/matter-js/matterjs-server) (branch `aqara-fp400`, image built from `docker/matterjs-server/Dockerfile.dev`) (custom cluster `aqara.ts`) — until it is released you
  need a server built from that branch. Reading works without it; zone commands and the position stream need it.

## What you get

| Entity | Meaning |
|---|---|
| `sensor.<name>_radar_zones` | number of zones; attribute `zones` holds `[{id, type, enabled, cells: [[row, col], …]}]` |
| `sensor.<name>_radar_regions` | number of non-empty regions; attribute `regions` holds `{entry_exit, interference, monitoring: [[row, col], …]}` |
| `sensor.<name>_radar_tracked_people` | people currently tracked; attribute `targets` = `[{id, x, y, row, col, activity, zones}]` (cm) |
| `sensor.<name>_radar_target_N_x` / `_y` | position of target N (disabled by default; enable for radar map cards) |
| `sensor.<name>_radar_last_motion` | last motion event: enter / left / left_in / right_out / right_in / left_out / access / away |
| `sensor.<name>_radar_activity_state` / `_human_count` | activity (unknown/active/still) and the device's people count |
| `sensor.<name>_radar_install_status` / `_install_angle` | orientation from the tilt sensor (diagnostic) |
| `select.<name>_radar_install_mode` / `_side_install` / `_coordinate_reverse` / `_detection_direction` / `_proximity_distance_level` | install and detection settings |
| `number.<name>_radar_install_height` | mounting height in mm (limits from the device) |
| `switch.<name>_radar_live_tracking` | keeps the position stream alive (~7 updates/s while someone moves) |
| `button.<name>_radar_start_background_learning` | starts the AI space background learning |
| `button.<name>_radar_clear_zones` | removes all zones |

Zones themselves show up through the Matter integration as extra occupancy sensors (one child endpoint per zone) after
a re-interview of the node — they are motion-triggered with the zone's own hold time.

## Services

```yaml
action: aqara_fp400.set_zones
data:
  device_id: <the FP400 device>
  zones:
    - id: 1
      cells: "3-4,6-10"        # rows 3-4, columns 6-10
    - id: 2
      cells: [[2, 8], [2, 9]]  # explicit [row, column] cells
      enabled: true
```

`aqara_fp400.clear_zones`, `aqara_fp400.subscribe_location` (timeout in seconds, max 3600) and
`aqara_fp400.start_learning` take just `device_id`.

Regions (the entry/exit, interference and monitoring-range areas, drawn as cell masks on the same grid) are set the
same way:

```yaml
action: aqara_fp400.set_region
data:
  device_id: <the FP400 device>
  region: entry_exit        # or: interference, monitoring
  cells: "6-7,4-6"          # rows,cols range, or explicit [[row, col], …]
```

`aqara_fp400.clear_region` takes `device_id` and `region`. Clearing the `monitoring` region means the full grid (the
device would detect nobody if the range were empty). The zone card can paint zones and all three regions —
switch layers with the buttons above the grid.

## The grid

16 columns × 20 rows of roughly 50 cm cells. Row 0 is the row nearest the sensor, column 8 is straight ahead;
columns grow as `x` decreases (column ≈ 8 − round((x + 25) / 50), row ≈ y / 50). `x`/`y` in the target attributes are
centimetres (`y` = distance from the sensor). The layout was verified by letting the device assign a person to
single-column test zones.

## Zone card

The integration serves a Lovelace card; add it as a manual card:

```yaml
type: custom:aqara-fp400-zone-card
entity: sensor.aqara_spatial_multi_sensor_fp400_radar_zones
title: Master bedroom      # optional
```

Pick a layer at the top (Zones, or one of the three regions), select a zone chip or **Add zone**, then click or drag on
the grid to paint (painting over another zone's cell moves the cell; **Erase** removes cells from any zone). The panel
under the chips enables/disables or deletes the selected zone. Regions are drawn as outlines and painted as a fill while
you edit them. **Save** writes the change to the device. Tracked people are drawn as dots (hollow when still); cells
outside the monitoring range are shaded. Turn on the live tracking switch to see people move.

## Development

Tests run inside a Home Assistant core checkout so they can reuse the Matter test fixtures:

```
ln -s $PWD/custom_components/aqara_fp400 <core>/tests/testing_config/custom_components/aqara_fp400
cp tests/test_integration.py <core>/tests/components/matter/test_zz_aqara_fp400.py
cd <core> && pytest tests/components/matter/test_zz_aqara_fp400.py
```

The `aqara_presence_fp400` node fixture referenced by the tests is part of the Home Assistant change that adds the
FP400 entities to the Matter integration.
