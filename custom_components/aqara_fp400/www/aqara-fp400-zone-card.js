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
const GUTTER = 1.6; // left gutter (grid units) for the distance labels
// Cool hues only, so a zone is never mistaken for the green/red/amber region outlines.
const ZONE_COLORS = ["#4f8ef7", "#a35bf7", "#19b5c9", "#e0409a", "#6e6cf0", "#7fb3ff", "#c084fc", "#ff8ad8"];
const REGION_KEYS = ["entry_exit", "interference", "monitoring"];
const REGION_META = {
  entry_exit: { label: "Entry/Exit", color: "#3bbf6a", dash: "", help: "Where people walk in and out. Paint the doorway cells." },
  interference: { label: "Interference", color: "#f75f4f", dash: "", help: "Things that fool the radar (fans, curtains). Paint them to ignore them." },
  monitoring: { label: "Monitoring", color: "#f2b600", dash: "0.35 0.25", help: "The area the sensor watches. Cells outside it are ignored." },
};
const SVG_NS = "http://www.w3.org/2000/svg";

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
        ha-card { display: block; padding: 16px; container-type: inline-size; }
        * { box-sizing: border-box; }

        .header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
        .title { font-size: 1.15em; font-weight: 500; line-height: 1.3; }
        .sub { color: var(--secondary-text-color); font-size: 0.85em; margin-top: 2px; display: flex; align-items: center; gap: 6px; }
        .live-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--secondary-text-color); opacity: 0.5; }
        .live-dot.on { background: #3bbf6a; opacity: 1; box-shadow: 0 0 0 3px rgba(59, 191, 106, 0.25); }
        .sync { flex: none; font-size: 0.8em; padding: 4px 10px; border-radius: 12px; background: var(--secondary-background-color); color: var(--secondary-text-color); white-space: nowrap; }
        .sync.dirty { color: #b98300; background: rgba(242, 182, 0, 0.16); }
        .sync.busy { color: var(--primary-color); }
        .sync.bad { color: var(--error-color, #db4437); background: rgba(219, 68, 55, 0.14); }

        .layers { display: flex; flex-wrap: wrap; gap: 4px; padding: 4px; border-radius: 12px; background: var(--secondary-background-color); margin-bottom: 12px; }
        .layer { flex: 1 1 0; min-width: 0; min-height: 40px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 6px 8px; border-radius: 9px; cursor: pointer; font-size: 0.88em; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transition: background 0.15s, color 0.15s; }
        .layer:hover { color: var(--primary-text-color); }
        .layer.active { background: var(--card-background-color); color: var(--primary-text-color); font-weight: 600; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.18); }
        .layer .dot { flex: none; width: 10px; height: 10px; border-radius: 50%; }
        .layer .dot.zones { background: linear-gradient(135deg, #4f8ef7 50%, #a35bf7 50%); }
        .layer .dot.ring { background: none; border: 2.5px solid currentColor; }
        @container (max-width: 380px) { .layer { flex-basis: calc(50% - 2px); } }

        .grid { position: relative; width: 100%; aspect-ratio: ${COLS + GUTTER} / ${ROWS}; touch-action: none; user-select: none; border-radius: 10px; background: var(--secondary-background-color); overflow: hidden; }
        svg { width: 100%; height: 100%; display: block; }
        .cell { stroke: var(--divider-color, rgba(127,127,127,0.3)); stroke-width: 0.025; fill: var(--primary-text-color); fill-opacity: 0; cursor: crosshair; transition: fill-opacity 0.1s; }
        .cell.outside { fill-opacity: 0.07; }
        .cell.painted { fill-opacity: 0.6; }
        .cell.painted.disabled { fill-opacity: 0.18; }
        .cell.region-tint { fill-opacity: 0.16; }
        .cell.region-fill { fill-opacity: 0.45; }
        .cell.hover-zone:hover { fill-opacity: 0.35; }
        .gridline { stroke: var(--primary-text-color); stroke-opacity: 0.12; stroke-width: 0.04; }
        .ahead { stroke: var(--primary-text-color); stroke-opacity: 0.12; stroke-width: 0.04; stroke-dasharray: 0.3 0.3; }
        .label { fill: var(--secondary-text-color); font-size: 0.55px; font-family: inherit; }
        .sensor { fill: var(--primary-text-color); }
        .sensor-halo { fill: var(--primary-text-color); fill-opacity: 0.08; }
        .region-outline { fill: none; stroke-width: 0.16; stroke-linecap: square; stroke-linejoin: round; }
        .region-outline.dim { stroke-opacity: 0.55; }
        .target { fill: var(--primary-color); stroke: var(--card-background-color, #fff); stroke-width: 0.1; }
        .target.still { fill: var(--card-background-color, #fff); stroke: var(--primary-color); stroke-width: 0.12; }
        .target-halo { fill: var(--primary-color); fill-opacity: 0.18; }

        .tools { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 12px; }
        .chip { display: inline-flex; align-items: center; gap: 8px; min-height: 40px; padding: 6px 14px 6px 10px; border-radius: 20px; cursor: pointer; font-size: 0.92em; border: 2px solid transparent; background: var(--secondary-background-color); color: var(--primary-text-color); transition: border-color 0.15s, background 0.15s; }
        .chip:hover { border-color: var(--divider-color); }
        .chip .dot { width: 14px; height: 14px; border-radius: 50%; }
        .chip .n { color: var(--secondary-text-color); font-size: 0.85em; }
        .chip.active { border-color: var(--primary-text-color); }
        .chip.active .n { color: inherit; }
        .chip.new { border-style: dashed; border-color: var(--divider-color); }
        .chip.new.active { border-color: var(--primary-text-color); }
        .chip.new .dot { opacity: 0.5; }
        .chip.erase .dot { background: none; border: 2px solid currentColor; opacity: 0.5; }

        .panel { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 10px; padding: 10px 12px; border-radius: 10px; border: 1px solid var(--divider-color); font-size: 0.9em; }
        .panel:empty { display: none; }
        .panel .name { font-weight: 600; display: inline-flex; align-items: center; gap: 8px; }
        .panel .name .dot { width: 12px; height: 12px; border-radius: 50%; }
        .panel .meta { color: var(--secondary-text-color); }
        .panel .spacer { flex: 1; min-width: 8px; }
        .panel .btns { display: flex; gap: 8px; flex: none; }

        button { font: inherit; background: var(--primary-color); color: var(--text-primary-color, #fff); border: 0; border-radius: 8px; padding: 8px 16px; min-height: 40px; cursor: pointer; font-size: 0.92em; }
        button.secondary { background: var(--secondary-background-color); color: var(--primary-text-color); }
        button.ghost { background: transparent; color: var(--primary-text-color); border: 1px solid var(--divider-color); }
        button.danger { color: var(--error-color, #db4437); }
        button:disabled { opacity: 0.4; cursor: default; }
        button.small { min-height: 34px; padding: 6px 12px; font-size: 0.86em; }

        .footer { display: flex; align-items: center; gap: 8px; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--divider-color); }
        .footer .hint { flex: 1; color: var(--secondary-text-color); font-size: 0.82em; line-height: 1.35; }
        .footer.dirty .hint { color: var(--primary-text-color); }
        .footer .actions { display: flex; gap: 8px; flex: none; }
        .error { color: var(--error-color); font-size: 0.85em; margin-top: 8px; }
        .error:empty { display: none; }
      </style>
      <ha-card>
        <div class="header">
          <div>
            <div class="title"></div>
            <div class="sub"><span class="live-dot"></span><span class="sub-text"></span></div>
          </div>
          <span class="sync"></span>
        </div>
        <div class="layers"></div>
        <div class="grid"><svg></svg></div>
        <div class="tools"></div>
        <div class="panel"></div>
        <div class="footer"><span class="hint"></span><span class="actions"></span></div>
        <div class="error"></div>
      </ha-card>`;
    this._svg = root.querySelector("svg");
    this._buildGrid();

    const grid = root.querySelector(".grid");
    grid.addEventListener("pointerdown", (ev) => this._onPointer(ev, true));
    grid.addEventListener("pointermove", (ev) => this._onPointer(ev, false));
    grid.addEventListener("pointerup", () => (this._pointerDown = false));
    grid.addEventListener("pointercancel", () => (this._pointerDown = false));
    grid.addEventListener("pointerleave", () => (this._pointerDown = false));
  }

  _el(tag, attrs = {}, cls = "") {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    if (cls) el.setAttribute("class", cls);
    return el;
  }

  _buildGrid() {
    this._gridDims = `${COLS}x${ROWS}`;
    this._svg.innerHTML = "";
    this._svg.setAttribute("viewBox", `${-GUTTER} 0 ${COLS + GUTTER} ${ROWS}`);
    this.shadowRoot.querySelector(".grid").style.aspectRatio = `${COLS + GUTTER} / ${ROWS}`;

    // distance labels + guide lines every metre (2 rows), and the straight-ahead line
    const guides = this._el("g");
    for (let r = 2; r < ROWS; r += 2) {
      const y = ROWS - r;
      guides.appendChild(this._el("line", { x1: 0, y1: y, x2: COLS, y2: y }, "gridline"));
      const t = this._el("text", { x: -0.25, y: y + 0.2, "text-anchor": "end" }, "label");
      t.textContent = `${r / 2} m`;
      guides.appendChild(t);
    }
    guides.appendChild(this._el("line", { x1: AHEAD_COL, y1: 0, x2: AHEAD_COL, y2: ROWS }, "ahead"));
    this._svg.appendChild(guides);

    this._cells = [];
    const cellLayer = this._el("g");
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        const rect = this._el("rect", { x: c, y: ROWS - 1 - r, width: 1, height: 1 }, "cell"); // row 0 nearest the sensor = bottom
        rect.dataset.index = r * COLS + c;
        cellLayer.appendChild(rect);
        this._cells.push(rect);
      }
    }
    this._svg.appendChild(cellLayer);

    this._regionLayer = this._el("g");
    this._svg.appendChild(this._regionLayer);

    const sensor = this._el("g");
    sensor.appendChild(this._el("circle", { cx: AHEAD_COL, cy: ROWS, r: 1.4 }, "sensor-halo"));
    sensor.appendChild(this._el("path", { d: `M ${AHEAD_COL - 0.55} ${ROWS} L ${AHEAD_COL} ${ROWS - 0.65} L ${AHEAD_COL + 0.55} ${ROWS} Z` }, "sensor"));
    this._svg.appendChild(sensor);

    this._targetLayer = this._el("g");
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

  _maxZones() {
    return this._hass?.states[this._config.entity]?.attributes?.max_zones || 8;
  }

  _zoneColor(id) {
    return ZONE_COLORS[(id - 1) % ZONE_COLORS.length];
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
    const live = !!targetsState?.attributes?.live_tracking;
    root.querySelector(".live-dot").classList.toggle("on", live);
    const activity = targetsState?.attributes?.activity_state;
    root.querySelector(".sub-text").textContent = state
      ? `${targets.length === 1 ? "1 person" : `${targets.length} people`}${activity && activity !== "unknown" ? ` · ${activity}` : ""}${live ? " · live" : " · live tracking off"}`
      : "entity not found";
    this._renderSync(state);

    const zones = this._zones();
    const regions = this._regions();
    const editingRegion = this._mode !== "zones" ? this._mode : null;
    const monitored = regions.monitoring;
    const shadeOutside = monitored.size > 0 && editingRegion !== "monitoring";

    const owner = new Array(ROWS * COLS).fill(null);
    for (const [id, zone] of zones) for (const idx of zone.cells) owner[idx] = id;

    this._cells.forEach((rect, idx) => {
      rect.setAttribute("class", "cell");
      rect.style.fill = "";
      const zoneId = owner[idx];
      if (editingRegion && regions[editingRegion].has(idx)) {
        rect.classList.add("region-fill");
        rect.style.fill = REGION_META[editingRegion].color;
      } else if (zoneId !== null) {
        rect.classList.add(editingRegion ? "region-tint" : "painted");
        if (!editingRegion) rect.classList.toggle("disabled", zones.get(zoneId).enabled === false);
        rect.style.fill = this._zoneColor(zoneId);
      } else if (shadeOutside && !monitored.has(idx)) {
        rect.classList.add("outside");
      }
      if (!editingRegion && this._active > 0 && zoneId === null && !rect.classList.contains("outside")) {
        rect.classList.add("hover-zone");
        rect.style.fill = this._zoneColor(this._active);
      }
    });

    // regions are drawn as a single outline around their area (the edited one is solid, above)
    this._regionLayer.innerHTML = "";
    for (const key of REGION_KEYS) {
      if (key === editingRegion || regions[key].size === 0) continue;
      const path = this._regionOutline(regions[key], REGION_META[key]);
      if (editingRegion) path.classList.add("dim");
      this._regionLayer.appendChild(path);
    }

    this._targetLayer.innerHTML = "";
    for (const t of targets) {
      const cx = Math.min(COLS - 0.3, Math.max(0.3, AHEAD_COL - t.x / CELL_CM));
      const cy = Math.min(ROWS - 0.3, Math.max(0.3, ROWS - t.y / CELL_CM));
      const still = t.activity === "still";
      if (!still) this._targetLayer.appendChild(this._el("circle", { cx, cy, r: 0.7 }, "target-halo"));
      const dot = this._el("circle", { cx, cy, r: 0.36 }, "target" + (still ? " still" : ""));
      const title = this._el("title");
      title.textContent = `Person ${t.id}: ${(t.y / 100).toFixed(1)} m ahead, ${Math.abs(t.x / 100).toFixed(1)} m ${t.x < 0 ? "right" : "left"}, ${t.activity}${t.zones?.length ? ", in zone " + t.zones.join(", ") : ""}`;
      dot.appendChild(title);
      this._targetLayer.appendChild(dot);
    }

    this._renderLayers(zones, regions);
    this._renderTools(zones, regions, state);
  }

  // an SVG path tracing the perimeter of a set of cells (boundary edges only)
  _regionOutline(cells, meta) {
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
    const attrs = { d: seg.join(" "), stroke: meta.color };
    if (meta.dash) attrs["stroke-dasharray"] = meta.dash;
    return this._el("path", attrs, "region-outline");
  }

  _renderSync(state) {
    const el = this.shadowRoot.querySelector(".sync");
    let text = "Synced";
    let cls = "";
    const dirty = this._mode === "zones" ? !!this._edit : !!(this._regionEdit && this._regionEdit.key === this._mode);
    if (this._saving) {
      text = "Saving…";
      cls = "busy";
    } else if (dirty) {
      text = "Unsaved changes";
      cls = "dirty";
    } else if (this._mode === "zones") {
      if (state?.attributes?.pending) [text, cls] = ["Verifying…", "busy"];
      else if (state?.attributes?.error) [text, cls] = ["Error: " + state.attributes.error, "bad"];
    } else {
      const rstate = this._hass.states[this._regionsEntity()]?.attributes;
      if (rstate?.pending?.[this._mode]) [text, cls] = ["Verifying…", "busy"];
      else if (rstate?.error?.[this._mode]) [text, cls] = ["Error: " + rstate.error[this._mode], "bad"];
    }
    el.textContent = text;
    el.className = "sync " + cls;
  }

  _renderLayers(zones, regions) {
    const bar = this.shadowRoot.querySelector(".layers");
    bar.innerHTML = "";
    const mk = (key, label, dotHtml, tip) => {
      const b = document.createElement("span");
      b.className = "layer" + (this._mode === key ? " active" : "");
      b.innerHTML = dotHtml + `<span>${label}</span>`;
      b.title = tip;
      b.addEventListener("click", () => {
        if (this._mode === key) return;
        this._mode = key;
        if (key === "zones" && !zones.has(this._active)) this._active = [...zones.keys()].sort((a, b) => a - b)[0] || 1;
        this._render();
      });
      bar.appendChild(b);
    };
    mk("zones", `Zones${zones.size ? ` (${zones.size})` : ""}`, `<span class="dot zones"></span>`, "Detection zones — each one becomes an occupancy sensor");
    for (const key of REGION_KEYS) {
      const m = REGION_META[key];
      mk(key, m.label, `<span class="dot ring" style="color:${m.color}${regions[key].size ? "" : ";opacity:0.4"}"></span>`, m.help);
    }
  }

  _renderTools(zones, regions, state) {
    const tools = this.shadowRoot.querySelector(".tools");
    const panel = this.shadowRoot.querySelector(".panel");
    tools.innerHTML = "";
    panel.innerHTML = "";
    panel.className = "panel";
    if (this._mode === "zones") this._zoneTools(tools, panel, zones, state);
    else this._regionTools(tools, panel, regions);
  }

  _zoneTools(tools, panel, zones, state) {
    const max = state?.attributes?.max_zones || 8;
    // show a chip only for zones that exist, plus the empty one currently being added
    const ids = [...zones.keys()].sort((a, b) => a - b);
    const adding = this._active > 0 && !zones.has(this._active);
    if (adding) ids.push(this._active);
    for (const id of ids) {
      const exists = zones.has(id);
      const chip = document.createElement("span");
      chip.className = "chip" + (this._active === id ? " active" : "") + (exists ? "" : " new");
      const disabled = exists && zones.get(id).enabled === false;
      chip.innerHTML = `<span class="dot" style="background:${this._zoneColor(id)}"></span><span>Zone ${id}</span>` + (exists ? `<span class="n">${disabled ? "off" : zones.get(id).cells.size}</span>` : "");
      chip.title = exists ? `Select zone ${id} to paint it` : "New zone — paint cells on the grid";
      chip.addEventListener("click", () => { this._active = id; this._render(); });
      tools.appendChild(chip);
    }
    if (zones.size) {
      const erase = document.createElement("span");
      erase.className = "chip erase" + (this._active === 0 ? " active" : "");
      erase.innerHTML = `<span class="dot"></span><span>Erase</span>`;
      erase.title = "Drag over cells to remove them from any zone";
      erase.addEventListener("click", () => { this._active = 0; this._render(); });
      tools.appendChild(erase);
    }
    const nextId = this._freeZoneId(zones, max);
    if (!adding) {
      const add = document.createElement("button");
      add.className = "ghost";
      add.textContent = "+ Add zone";
      add.disabled = !nextId || this._saving;
      add.title = nextId ? "" : `All ${max} zones are in use`;
      add.addEventListener("click", () => { this._active = nextId; this._render(); });
      tools.appendChild(add);
    }

    // detail panel for the selected zone
    if (zones.has(this._active)) {
      const zone = zones.get(this._active);
      panel.innerHTML = `<span class="name"><span class="dot" style="background:${this._zoneColor(this._active)}"></span>Zone ${this._active}</span>
        <span class="meta">${zone.cells.size} cells · ${zone.enabled ? "enabled" : "disabled"}</span><span class="spacer"></span>`;
      const group = document.createElement("span");
      group.className = "btns";
      const toggle = document.createElement("button");
      toggle.className = "secondary small";
      toggle.textContent = zone.enabled ? "Disable" : "Enable";
      toggle.disabled = this._saving;
      toggle.addEventListener("click", () => this._toggleEnabled(this._active));
      group.appendChild(toggle);
      const del = document.createElement("button");
      del.className = "secondary small danger";
      del.textContent = "Delete";
      del.disabled = this._saving;
      del.addEventListener("click", () => this._deleteZone(this._active));
      group.appendChild(del);
      panel.appendChild(group);
    } else if (adding) {
      panel.innerHTML = `<span class="name"><span class="dot" style="background:${this._zoneColor(this._active)}"></span>New zone ${this._active}</span>
        <span class="meta">paint cells on the grid to create it</span>`;
    } else if (this._active === 0) {
      panel.innerHTML = `<span class="name">Erase</span><span class="meta">drag over cells to remove them from any zone</span>`;
    }

    const hint = zones.size || adding
      ? "Click or drag on the grid to paint the selected zone. Painting over another zone moves those cells."
      : "No zones yet. Each zone becomes its own occupancy sensor in Home Assistant.";
    this._actionButtons(hint, !!this._edit, () => { this._edit = null; this._render(); }, () => this._save());
  }

  _freeZoneId(zones, max) {
    for (let id = 1; id <= max; id++) if (!zones.has(id)) return id;
    return 0;
  }

  _deleteZone(id) {
    const zones = this._startEdit();
    zones.delete(id);
    this._active = [...zones.keys()].sort((a, b) => a - b)[0] || this._freeZoneId(zones, this._maxZones());
    this._render();
  }

  _regionTools(tools, panel, regions) {
    const key = this._mode;
    const meta = REGION_META[key];
    const count = regions[key].size;
    panel.innerHTML = `<span class="name"><span class="dot" style="background:${meta.color}"></span>${meta.label}</span><span class="meta">${count ? `${count} cells` : "not set"} · ${meta.help}</span><span class="spacer"></span>`;
    const clear = document.createElement("button");
    clear.className = "secondary small";
    clear.textContent = "Clear";
    clear.disabled = this._saving || count === 0;
    clear.addEventListener("click", () => { this._startRegionEdit(key).clear(); this._render(); });
    panel.appendChild(clear);

    const dirty = !!(this._regionEdit && this._regionEdit.key === key);
    this._actionButtons("Click or drag on the grid to add cells; drag from a filled cell to remove.", dirty, () => { this._regionEdit = null; this._render(); }, () => this._saveRegion());
  }

  _actionButtons(hint, dirty, onRevert, onSave) {
    const footer = this.shadowRoot.querySelector(".footer");
    footer.classList.toggle("dirty", dirty);
    footer.querySelector(".hint").textContent = dirty ? "You have unsaved changes." : hint;
    const actions = footer.querySelector(".actions");
    actions.innerHTML = "";

    const revert = document.createElement("button");
    revert.className = "ghost";
    revert.textContent = "Revert";
    revert.disabled = !dirty || this._saving;
    revert.addEventListener("click", onRevert);
    actions.appendChild(revert);

    const save = document.createElement("button");
    save.textContent = this._saving ? "Saving…" : "Save";
    save.disabled = !dirty || this._saving;
    save.addEventListener("click", onSave);
    actions.appendChild(save);
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
