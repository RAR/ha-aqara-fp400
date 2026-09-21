/*
 * Aqara FP400 zone card — paints detection zones and the entry/exit, interference and
 * monitoring regions on the sensor's 20 x 16 grid, and shows tracked people live.
 *
 * Config:
 *   type: custom:aqara-fp400-zone-card
 *   entity: sensor.<device>_zones          (required; the "Zones" sensor of this integration)
 *   regions_entity: sensor.<device>_regions (optional; derived from `entity` when omitted)
 *   targets_entity: sensor.<device>_tracked_people   (optional; derived from `entity` when omitted)
 *   title: Living room                      (optional)
 */

// Defaults; the Zones sensor reports grid_cols/grid_rows and the card follows those.
let COLS = 16;
let ROWS = 20;
// column 8 is straight ahead of the sensor (x ≈ -25 cm); columns grow as x decreases
const AHEAD_COL = 8.5 - 25 / 50;
const CELL_CM = 50;
const ZONE_COLORS = ["#4f8ef7", "#f75f4f", "#3bbf6a", "#f2b600", "#a35bf7", "#19b5c9", "#f77b1c", "#c9198f"];
const REGION_KEYS = ["entry_exit", "interference", "monitoring"];
const REGION_META = {
  entry_exit: { label: "Entry/Exit", color: "#3bbf6a" },
  interference: { label: "Interference", color: "#f75f4f" },
  monitoring: { label: "Monitoring", color: "#f2b600" },
};

class AqaraFp400ZoneCard extends HTMLElement {
  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find((id) => id.startsWith("sensor.") && id.endsWith("_zones"));
    return { entity: entity || "sensor.aqara_spatial_multi_sensor_fp400_zones" };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("entity (the Zones sensor) is required");
    this._config = config;
    this._mode = "zones"; // "zones" or a REGION_KEYS entry
    this._edit = null; // zones edit: Map<id, {cells:Set, enabled, type}>
    this._regionEdit = null; // region edit: {key, cells:Set}
    this._active = 1;
    this._pointerDown = false;
    this._paintValue = null;
    if (!this.shadowRoot) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 8;
  }

  // ---------------------------------------------------------------- build

  _build() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 12px 16px 16px; }
        .header { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 8px; }
        .title { font-size: 1.1em; font-weight: 500; }
        .status { color: var(--secondary-text-color); font-size: 0.85em; }
        .modes { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
        .mode { display: inline-flex; align-items: center; min-height: 34px; box-sizing: border-box; border: 1px solid var(--divider-color, #444); border-radius: 6px; padding: 4px 12px; cursor: pointer; font-size: 0.9em; background: var(--card-background-color); color: var(--primary-text-color); }
        .mode.active { border-color: var(--primary-text-color); font-weight: 600; }
        .mode .swatch { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 5px; vertical-align: middle; }
        .grid { position: relative; width: 100%; aspect-ratio: ${COLS} / ${ROWS}; touch-action: none; user-select: none; }
        svg { width: 100%; height: 100%; display: block; }
        .cell { stroke: var(--divider-color, #444); stroke-width: 0.03; fill: var(--card-background-color, #1c1c1c); cursor: crosshair; }
        .cell.painted { fill-opacity: 0.55; }
        .cell.painted.disabled { fill-opacity: 0.2; }
        .cell.region-tint { fill-opacity: 0.22; }
        .sensor { fill: var(--primary-text-color); }
        .target { fill: #fff; stroke: #000; stroke-width: 0.06; }
        .target.still { fill: #bbb; }
        .toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 12px; }
        .chip { display: inline-flex; align-items: center; justify-content: center; box-sizing: border-box; min-width: 40px; min-height: 40px; border: 2px solid transparent; border-radius: 20px; padding: 6px 14px; cursor: pointer; font-size: 0.95em; color: #fff; opacity: 0.6; }
        .chip.active { border-color: var(--primary-text-color); opacity: 1; }
        .chip.erase { background: #555; }
        .spacer { flex: 1; }
        mwc-button, button { font: inherit; }
        button { background: var(--primary-color); color: var(--text-primary-color, #fff); border: 0; border-radius: 6px; padding: 9px 16px; min-height: 40px; cursor: pointer; }
        button.secondary { background: var(--secondary-background-color); color: var(--primary-text-color); }
        button:disabled { opacity: 0.4; cursor: default; }
        .hint { color: var(--secondary-text-color); font-size: 0.8em; margin-top: 6px; }
        .error { color: var(--error-color); font-size: 0.85em; margin-top: 6px; }
      </style>
      <ha-card>
        <div class="header"><span class="title"></span><span class="status"></span></div>
        <div class="modes"></div>
        <div class="grid"><svg viewBox="0 0 ${COLS} ${ROWS}" preserveAspectRatio="none"></svg></div>
        <div class="toolbar"></div>
        <div class="hint"></div>
        <div class="error"></div>
      </ha-card>`;
    this._svg = root.querySelector("svg");
    this._buildGrid();

    const grid = root.querySelector(".grid");
    grid.addEventListener("pointerdown", (ev) => this._onPointer(ev, true));
    grid.addEventListener("pointermove", (ev) => this._onPointer(ev, false));
    grid.addEventListener("pointerup", () => (this._pointerDown = false));
    grid.addEventListener("pointerleave", () => (this._pointerDown = false));
  }

  _buildGrid() {
    this._gridDims = `${COLS}x${ROWS}`;
    this._svg.innerHTML = "";
    this._svg.setAttribute("viewBox", `0 0 ${COLS} ${ROWS}`);
    this.shadowRoot.querySelector(".grid").style.aspectRatio = `${COLS} / ${ROWS}`;
    this._cells = [];
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("x", c);
        rect.setAttribute("y", ROWS - 1 - r); // row 0 nearest the sensor = bottom
        rect.setAttribute("width", 1);
        rect.setAttribute("height", 1);
        rect.classList.add("cell");
        rect.dataset.index = r * COLS + c;
        this._svg.appendChild(rect);
        this._cells.push(rect);
      }
    }
    const sensor = document.createElementNS("http://www.w3.org/2000/svg", "path");
    sensor.setAttribute("d", `M ${AHEAD_COL - 0.6} ${ROWS} L ${AHEAD_COL} ${ROWS - 0.7} L ${AHEAD_COL + 0.6} ${ROWS} Z`);
    sensor.classList.add("sensor");
    this._svg.appendChild(sensor);
    this._regionLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
    this._svg.appendChild(this._regionLayer);
    this._targetLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
    this._svg.appendChild(this._targetLayer);
  }

  // ---------------------------------------------------------------- state (zones)

  _entityZones() {
    const state = this._hass?.states[this._config.entity];
    const zones = new Map();
    for (const zone of state?.attributes?.zones || []) {
      zones.set(zone.id, { cells: new Set(zone.cells.map(([r, c]) => r * COLS + c)), enabled: zone.enabled !== false, type: zone.type || 0 });
    }
    return zones;
  }

  _zones() {
    return this._edit ? this._edit.zones : this._entityZones();
  }

  _startEdit() {
    if (!this._edit) {
      const zones = new Map();
      for (const [id, zone] of this._entityZones()) zones.set(id, { ...zone, cells: new Set(zone.cells) });
      this._edit = { zones };
    }
    return this._edit.zones;
  }

  // ---------------------------------------------------------------- state (regions)

  _regionsEntity() {
    return this._config.regions_entity || this._config.entity.replace(/_zones$/, "_regions");
  }

  _entityRegions() {
    const attrs = this._hass?.states[this._regionsEntity()]?.attributes?.regions || {};
    const out = {};
    for (const key of REGION_KEYS) {
      out[key] = new Set((attrs[key] || []).map(([r, c]) => r * COLS + c));
    }
    return out;
  }

  // cells of every region, with the one being edited overridden
  _regions() {
    const out = this._entityRegions();
    if (this._regionEdit) out[this._regionEdit.key] = this._regionEdit.cells;
    return out;
  }

  _startRegionEdit(key) {
    if (!this._regionEdit || this._regionEdit.key !== key) {
      this._regionEdit = { key, cells: new Set(this._entityRegions()[key]) };
    }
    return this._regionEdit.cells;
  }

  _targetsEntity() {
    return this._config.targets_entity || this._config.entity.replace(/_zones$/, "_tracked_people");
  }

  _deviceId() {
    return this._hass?.entities?.[this._config.entity]?.device_id;
  }

  // ---------------------------------------------------------------- render

  _render() {
    if (!this._hass || !this.shadowRoot) return;
    const root = this.shadowRoot;
    const state = this._hass.states[this._config.entity];
    const cols = Number(state?.attributes?.grid_cols) || COLS;
    const rows = Number(state?.attributes?.grid_rows) || ROWS;
    if (`${cols}x${rows}` !== this._gridDims) {
      COLS = cols;
      ROWS = rows;
      this._buildGrid();
    }
    root.querySelector(".title").textContent = this._config.title || state?.attributes?.friendly_name?.replace(/ Zones$/, "") || "FP400";
    const targetsState = this._hass.states[this._targetsEntity()];
    const targets = targetsState?.attributes?.targets || [];
    const live = targetsState?.attributes?.live_tracking;
    root.querySelector(".status").textContent = state
      ? `${targets.length} tracked · ${targetsState?.attributes?.activity_state || "?"}${live ? " · live" : ""} · ${this._syncLabel(state)}`
      : "entity not found";

    const zones = this._zones();
    const regions = this._regions();
    const editingRegion = this._mode !== "zones" ? this._mode : null;

    const owner = new Array(ROWS * COLS).fill(null);
    for (const [id, zone] of zones) for (const idx of zone.cells) owner[idx] = id;

    this._cells.forEach((rect, idx) => {
      rect.classList.remove("painted", "disabled", "region-tint");
      rect.style.fill = "";
      const zoneId = owner[idx];
      if (editingRegion && regions[editingRegion].has(idx)) {
        rect.classList.add("painted");
        rect.style.fill = REGION_META[editingRegion].color;
      } else if (zoneId !== null) {
        rect.classList.add(editingRegion ? "region-tint" : "painted");
        if (!editingRegion) rect.classList.toggle("disabled", zones.get(zoneId).enabled === false);
        rect.style.fill = ZONE_COLORS[(zoneId - 1) % ZONE_COLORS.length];
      }
    });

    // regions are drawn as a single outline around their area (the edited one is solid, above)
    this._regionLayer.innerHTML = "";
    for (const key of REGION_KEYS) {
      if (key === editingRegion) continue;
      this._regionLayer.appendChild(this._regionOutline(regions[key], REGION_META[key].color));
    }

    this._targetLayer.innerHTML = "";
    for (const t of targets) {
      const u = AHEAD_COL - t.x / CELL_CM;
      const v = ROWS - t.y / CELL_CM;
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("cx", Math.min(COLS - 0.3, Math.max(0.3, u)));
      dot.setAttribute("cy", Math.min(ROWS - 0.3, Math.max(0.3, v)));
      dot.setAttribute("r", 0.35);
      dot.classList.add("target");
      if (t.activity === "still") dot.classList.add("still");
      dot.innerHTML = `<title>target ${t.id}: x ${t.x} cm, y ${t.y} cm, ${t.activity}${t.zones?.length ? ", zones " + t.zones.join(",") : ""}</title>`;
      this._targetLayer.appendChild(dot);
    }

    this._renderModes();
    this._renderToolbar(zones, regions, state);
    root.querySelector(".hint").textContent = editingRegion
      ? `Editing the ${REGION_META[editingRegion].label} region. Click or drag to add cells, drag from a filled cell to erase, then Save.`
      : `Cells are ~50 cm; the sensor is the triangle at the bottom. Click or drag to paint the selected zone, double-click a zone chip to disable it, then Save.`;
  }

  // an SVG path tracing the perimeter of a set of cells (boundary edges only)
  _regionOutline(cells, color) {
    const has = (r, c) => r >= 0 && r < ROWS && c >= 0 && c < COLS && cells.has(r * COLS + c);
    const seg = [];
    for (const idx of cells) {
      const r = Math.floor(idx / COLS);
      const c = idx % COLS;
      const yTop = ROWS - 1 - r;
      const yBot = ROWS - r;
      if (!has(r + 1, c)) seg.push(`M ${c} ${yTop} L ${c + 1} ${yTop}`); // toward the sensor-far side
      if (!has(r - 1, c)) seg.push(`M ${c} ${yBot} L ${c + 1} ${yBot}`);
      if (!has(r, c - 1)) seg.push(`M ${c} ${yTop} L ${c} ${yBot}`);
      if (!has(r, c + 1)) seg.push(`M ${c + 1} ${yTop} L ${c + 1} ${yBot}`);
    }
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", seg.join(" "));
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", color);
    path.setAttribute("stroke-width", 0.16);
    path.setAttribute("stroke-linecap", "square");
    return path;
  }

  _syncLabel(state) {
    if (this._saving) return "saving…";
    const dirty = this._mode === "zones" ? this._edit : this._regionEdit && this._regionEdit.key === this._mode;
    if (dirty) return "unsaved";
    if (this._mode === "zones") {
      if (state?.attributes?.pending) return "verifying…";
      if (state?.attributes?.error) return "⚠ " + state.attributes.error;
      return "synced";
    }
    const rstate = this._hass.states[this._regionsEntity()]?.attributes;
    if (rstate?.pending?.[this._mode]) return "verifying…";
    if (rstate?.error?.[this._mode]) return "⚠ " + rstate.error[this._mode];
    return "synced";
  }

  _renderModes() {
    const bar = this.shadowRoot.querySelector(".modes");
    bar.innerHTML = "";
    const mk = (key, label, color) => {
      const b = document.createElement("span");
      b.className = "mode" + (this._mode === key ? " active" : "");
      b.innerHTML = (color ? `<span class="swatch" style="background:${color}"></span>` : "") + label;
      b.addEventListener("click", () => {
        if (this._mode === key) return;
        this._mode = key;
        if (key === "zones") this._active = 1;
        this._render();
      });
      bar.appendChild(b);
    };
    mk("zones", "Zones", null);
    for (const key of REGION_KEYS) mk(key, REGION_META[key].label, REGION_META[key].color);
  }

  _renderToolbar(zones, regions, state) {
    const bar = this.shadowRoot.querySelector(".toolbar");
    bar.innerHTML = "";
    if (this._mode === "zones") this._zoneToolbar(bar, zones, state);
    else this._regionToolbar(bar, regions);
  }

  _zoneToolbar(bar, zones, state) {
    const max = state?.attributes?.max_zones || 8;
    for (let id = 1; id <= max; id++) {
      const chip = document.createElement("span");
      chip.className = "chip" + (this._active === id ? " active" : "");
      chip.style.background = ZONE_COLORS[(id - 1) % ZONE_COLORS.length];
      chip.textContent = `${id}${zones.has(id) ? ` (${zones.get(id).cells.size})` : ""}`;
      chip.title = zones.has(id) ? "double-click to toggle enabled" : "";
      chip.addEventListener("click", () => { this._active = id; this._render(); });
      chip.addEventListener("dblclick", () => this._toggleEnabled(id));
      bar.appendChild(chip);
    }
    const erase = document.createElement("span");
    erase.className = "chip erase" + (this._active === 0 ? " active" : "");
    erase.textContent = "erase";
    erase.addEventListener("click", () => { this._active = 0; this._render(); });
    bar.appendChild(erase);

    this._actionButtons(bar, !!this._edit, () => { this._edit = null; this._render(); }, () => this._save());
  }

  _regionToolbar(bar, regions) {
    const key = this._mode;
    const count = regions[key].size;
    const label = document.createElement("span");
    label.className = "chip active";
    label.style.background = REGION_META[key].color;
    label.textContent = `${REGION_META[key].label} (${count})`;
    bar.appendChild(label);

    const clear = document.createElement("button");
    clear.className = "secondary";
    clear.textContent = "Clear";
    clear.disabled = this._saving;
    clear.addEventListener("click", () => { this._startRegionEdit(key).clear(); this._render(); });
    bar.appendChild(clear);

    this._actionButtons(bar, !!(this._regionEdit && this._regionEdit.key === key), () => { this._regionEdit = null; this._render(); }, () => this._saveRegion());
  }

  _actionButtons(bar, dirty, onRevert, onSave) {
    const spacer = document.createElement("span");
    spacer.className = "spacer";
    bar.appendChild(spacer);

    const revert = document.createElement("button");
    revert.className = "secondary";
    revert.textContent = "Revert";
    revert.disabled = !dirty || this._saving;
    revert.addEventListener("click", onRevert);
    bar.appendChild(revert);

    const save = document.createElement("button");
    save.textContent = this._saving ? "Saving…" : "Save";
    save.disabled = !dirty || this._saving;
    save.addEventListener("click", onSave);
    bar.appendChild(save);
  }

  // ---------------------------------------------------------------- editing

  _onPointer(ev, down) {
    if (down) {
      this._pointerDown = true;
      ev.target.setPointerCapture?.(ev.pointerId);
    } else if (!this._pointerDown) {
      return;
    }
    const cell = this.shadowRoot.elementFromPoint(ev.clientX, ev.clientY);
    const idx = cell?.dataset?.index;
    if (idx === undefined) return;
    const index = Number(idx);
    if (this._mode === "zones") this._paintZone(index, down);
    else this._paintRegion(index, down);
    this._render();
  }

  _paintZone(index, down) {
    const zones = this._startEdit();
    if (down) {
      // first cell decides whether this stroke paints or erases the active zone
      this._paintValue = this._active !== 0 && !zones.get(this._active)?.cells.has(index);
    }
    for (const zone of zones.values()) zone.cells.delete(index);
    if (this._active !== 0 && this._paintValue) {
      if (!zones.has(this._active)) zones.set(this._active, { cells: new Set(), enabled: true, type: 0 });
      zones.get(this._active).cells.add(index);
    }
    for (const [id, zone] of [...zones]) if (zone.cells.size === 0) zones.delete(id);
  }

  _paintRegion(index, down) {
    const cells = this._startRegionEdit(this._mode);
    if (down) this._paintValue = !cells.has(index);
    if (this._paintValue) cells.add(index);
    else cells.delete(index);
  }

  _toggleEnabled(id) {
    const zones = this._startEdit();
    if (zones.has(id)) zones.get(id).enabled = !zones.get(id).enabled;
    this._render();
  }

  _cellsToRowCol(set) {
    return [...set].sort((a, b) => a - b).map((idx) => [Math.floor(idx / COLS), idx % COLS]);
  }

  async _runService(service, data) {
    const error = this.shadowRoot.querySelector(".error");
    error.textContent = "";
    const deviceId = this._deviceId();
    if (!deviceId) {
      error.textContent = "Cannot resolve the device of " + this._config.entity;
      return false;
    }
    this._saving = true;
    this._render();
    try {
      await this._hass.callService("aqara_fp400", service, { device_id: deviceId, ...data });
      return true;
    } catch (err) {
      error.textContent = err?.message || String(err);
      return false;
    } finally {
      this._saving = false;
      this._render();
    }
  }

  async _save() {
    const zones = [...this._zones()].map(([id, zone]) => ({
      id,
      enabled: zone.enabled,
      type: zone.type || 0,
      cells: this._cellsToRowCol(zone.cells),
    }));
    if (await this._runService("set_zones", { zones })) this._edit = null;
  }

  async _saveRegion() {
    const key = this._mode;
    const cells = this._cellsToRowCol(this._regions()[key]);
    if (await this._runService("set_region", { region: key, cells })) this._regionEdit = null;
  }
}

customElements.define("aqara-fp400-zone-card", AqaraFp400ZoneCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "aqara-fp400-zone-card",
  name: "Aqara FP400 zone card",
  description: "Paint detection zones and regions and watch tracked people on the FP400 grid.",
  preview: false,
});
