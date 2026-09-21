# Zone card — design/handoff notes

File: `custom_components/aqara_fp400/www/aqara-fp400-zone-card.js` — one self-contained Web Component
(`<aqara-fp400-zone-card>`), shadow DOM, no external deps. Registered as a Lovelace module resource by
`__init__.py::_async_register_card`.

## Deploy loop (to test in the live HA on beanbag)
1. Edit the JS. Bump `manifest.json` "version".
2. `cd custom_components && COPYFILE_DISABLE=1 tar czf /tmp/aqara_fp400.tgz aqara_fp400`
3. `scp /tmp/aqara_fp400.tgz rar@10.75.0.238:/data/build/aqara_fp400.tgz` (served on :8766)
4. On HA: `curl -s http://10.75.0.238:8766/aqara_fp400.tgz | tar xzf - -C /config/custom_components`, restart HA.
   (Andrew usually does step 4 + a browser hard-refresh.)
Commit + `git push origin main` when happy.

## Design constraints (do not break)
- Must use HA theme CSS vars: `--primary-color`, `--card-background-color`, `--secondary-background-color`,
  `--primary-text-color`, `--secondary-text-color`, `--divider-color`, `--error-color`, `--text-primary-color`.
  Support light and dark themes (no hard-coded backgrounds that fight the theme).
- Works at phone width (the grid is `aspect-ratio: COLS/ROWS`, currently 16/20 — portrait).
- Shadow DOM: all styles inline in the `<style>` block; query within `this.shadowRoot`.

## Functional structure (keep behaviour, restyle freely)
- Reads three entities (derived from the required `entity` = the `_zones` sensor): `_zones`, `_regions`, `_tracked_people`.
- **Layers** via top buttons: "Zones" + one per region (Entry/Exit, Interference, Monitoring). `this._mode`.
- Grid is an SVG of COLS×ROWS `<rect>` cells (row 0 = nearest the sensor = BOTTOM; `y = ROWS-1-r`).
  `AHEAD_COL` marks straight-ahead; the sensor triangle sits at the TOP by default (`sensor_at: bottom` flips it), matching the Aqara app. Row 0 is the row nearest the sensor either way.
- Zones = solid filled cells (ZONE_COLORS by id); bottom toolbar shows only EXISTING zones + the one being added,
  an "+ Add zone" button, "erase", and "Delete zone N" when a zone is selected. Save → `set_zones` service.
- Regions = **single perimeter outline** around their area (`_regionOutline`), except the region being edited,
  which is a solid fill you paint. Region toolbar: label + Clear + Save (→ `set_region`).
- Monitoring is already inverted in the BACKEND (the card just paints "the monitored area").
- Status line: "N tracked · <activity> · <sync>"; sync = saving…/unsaved/verifying…/synced/⚠ error.

## Design nits addressed in 0.4.0 (kept for history)
- Zone colors 2/3/4 reuse the region colors (red/green/amber) → a green zone can look like the Entry/Exit
  outline. Consider a zone palette that avoids the three region hues, or distinguish fill vs outline more.
- Toolbar can get wide with many controls; consider grouping/wrapping.
- Region layer buttons vs zone chips: the two-level model (top layer buttons, bottom per-zone) took a beat to
  discover — could be made more obvious.
- Grid cell borders + region outlines + target dots can get busy; a design pass could calm the visual hierarchy.

Current version: 1.0.0 (design pass 2026-09-21: segmented layer bar, cool-only zone palette, distance labels,
shaded area outside monitoring, selected-zone panel with Enable/Disable + Delete, sync pill, footer Save bar). Commits are on `main` (github.com/RAR/ha-aqara-fp400). Full protocol/verification notes:
`~/fp400/VERIFY_1196.md`, `~/fp400/NOTES.md`; upstream PR handoff `~/fp400/PR_NOTES.md`.
