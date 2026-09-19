/*
 * Aqara FP400 zone card — paints detection zones on the sensor's 20 x 16 grid and
 * shows tracked people live.
 *
 * Config:
 *   type: custom:aqara-fp400-zone-card
 *   entity: sensor.<device>_zones          (required; the "Zones" sensor of this integration)
 *   targets_entity: sensor.<device>_tracked_people   (optional; derived from `entity` when omitted)
 *   title: Living room                      (optional)
 */

const COLS = 20;
const ROWS = 16;
// column 8 is straight ahead of the sensor (x ≈ -22 cm); columns grow as x decreases
const AHEAD_COL = 8.5 - 22 / 50;
const ZONE_COLORS = ["#4f8ef7", "#f75f4f", "#3bbf6a", "#f2b600", "#a35bf7", "#19b5c9", "#f77b1c", "#c9198f"];

class AqaraFp400ZoneCard extends HTMLElement {
  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find((id) => id.startsWith("sensor.") && id.endsWith("_zones"));
    return { entity: entity || "sensor.aqara_spatial_multi_sensor_fp400_zones" };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("entity (the Zones sensor) is required");
    this._config = config;
    this._edit = null; // {zones: Map<id, Set<cellIndex>>, dirty}
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
        .grid { position: relative; width: 100%; aspect-ratio: ${COLS} / ${ROWS}; touch-action: none; user-select: none; }
        svg { width: 100%; height: 100%; display: block; }
        .cell { stroke: var(--divider-color, #444); stroke-width: 0.03; fill: var(--card-background-color, #1c1c1c); cursor: crosshair; }
        .cell.painted { fill-opacity: 0.55; }
        .cell.painted.disabled { fill-opacity: 0.2; }
        .sensor { fill: var(--primary-text-color); }
        .target { fill: #fff; stroke: #000; stroke-width: 0.06; }
        .target.still { fill: #bbb; }
        .toolbar { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 10px; }
        .chip { border: 2px solid transparent; border-radius: 16px; padding: 2px 10px; cursor: pointer; font-size: 0.85em; color: #fff; opacity: 0.6; }
        .chip.active { border-color: var(--primary-text-color); opacity: 1; }
        .chip.erase { background: #555; }
        .spacer { flex: 1; }
        mwc-button, button { font: inherit; }
        button { background: var(--primary-color); color: var(--text-primary-color, #fff); border: 0; border-radius: 4px; padding: 6px 12px; cursor: pointer; }
        button.secondary { background: var(--secondary-background-color); color: var(--primary-text-color); }
        button:disabled { opacity: 0.4; cursor: default; }
        .hint { color: var(--secondary-text-color); font-size: 0.8em; margin-top: 6px; }
        .error { color: var(--error-color); font-size: 0.85em; margin-top: 6px; }
      </style>
      <ha-card>
        <div class="header"><span class="title"></span><span class="status"></span></div>
        <div class="grid"><svg viewBox="0 0 ${COLS} ${ROWS}" preserveAspectRatio="none"></svg></div>
        <div class="toolbar"></div>
        <div class="hint">Cells are ~50 cm; the sensor is the triangle at the bottom. Click or drag to paint the selected zone, double-click a zone chip to disable it, then Save.</div>
        <div class="error"></div>
      </ha-card>`;
    this._svg = root.querySelector("svg");
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
    this._targetLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
    this._svg.appendChild(this._targetLayer);

    const grid = root.querySelector(".grid");
    grid.addEventListener("pointerdown", (ev) => this._onPointer(ev, true));
    grid.addEventListener("pointermove", (ev) => this._onPointer(ev, false));
    grid.addEventListener("pointerup", () => (this._pointerDown = false));
    grid.addEventListener("pointerleave", () => (this._pointerDown = false));
  }

  // ---------------------------------------------------------------- state

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
    root.querySelector(".title").textContent = this._config.title || state?.attributes?.friendly_name?.replace(/ Zones$/, "") || "FP400";
    const targetsState = this._hass.states[this._targetsEntity()];
    const targets = targetsState?.attributes?.targets || [];
    const live = targetsState?.attributes?.live_tracking;
    root.querySelector(".status").textContent = state
      ? `${targets.length} tracked · ${targetsState?.attributes?.activity_state || "?"}${live ? " · live" : ""}${this._edit ? " · unsaved" : ""}`
      : "entity not found";

    const zones = this._zones();
    const owner = new Array(ROWS * COLS).fill(null);
    for (const [id, zone] of zones) for (const idx of zone.cells) owner[idx] = id;
    this._cells.forEach((rect, idx) => {
      const id = owner[idx];
      rect.classList.toggle("painted", id !== null);
      rect.classList.toggle("disabled", id !== null && zones.get(id).enabled === false);
      rect.style.fill = id === null ? "" : ZONE_COLORS[(id - 1) % ZONE_COLORS.length];
    });

    this._targetLayer.innerHTML = "";
    for (const t of targets) {
      const u = AHEAD_COL - t.x / 50;
      const v = ROWS - t.y / 50;
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("cx", Math.min(COLS - 0.3, Math.max(0.3, u)));
      dot.setAttribute("cy", Math.min(ROWS - 0.3, Math.max(0.3, v)));
      dot.setAttribute("r", 0.35);
      dot.classList.add("target");
      if (t.activity === "still") dot.classList.add("still");
      dot.innerHTML = `<title>target ${t.id}: x ${t.x} cm, y ${t.y} cm, ${t.activity}${t.zones?.length ? ", zones " + t.zones.join(",") : ""}</title>`;
      this._targetLayer.appendChild(dot);
    }

    this._renderToolbar(zones, state);
  }

  _renderToolbar(zones, state) {
    const bar = this.shadowRoot.querySelector(".toolbar");
    bar.innerHTML = "";
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

    const spacer = document.createElement("span");
    spacer.className = "spacer";
    bar.appendChild(spacer);

    const revert = document.createElement("button");
    revert.className = "secondary";
    revert.textContent = "Revert";
    revert.disabled = !this._edit;
    revert.addEventListener("click", () => { this._edit = null; this._render(); });
    bar.appendChild(revert);

    const save = document.createElement("button");
    save.textContent = "Save";
    save.disabled = !this._edit;
    save.addEventListener("click", () => this._save());
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
    this._render();
  }

  _toggleEnabled(id) {
    const zones = this._startEdit();
    if (zones.has(id)) zones.get(id).enabled = !zones.get(id).enabled;
    this._render();
  }

  async _save() {
    const error = this.shadowRoot.querySelector(".error");
    error.textContent = "";
    const deviceId = this._deviceId();
    if (!deviceId) {
      error.textContent = "Cannot resolve the device of " + this._config.entity;
      return;
    }
    const zones = [...this._zones()].map(([id, zone]) => ({
      id,
      enabled: zone.enabled,
      type: zone.type || 0,
      cells: [...zone.cells].sort((a, b) => a - b).map((idx) => [Math.floor(idx / COLS), idx % COLS]),
    }));
    try {
      await this._hass.callService("aqara_fp400", "set_zones", { device_id: deviceId, zones });
      this._edit = null;
      this._render();
    } catch (err) {
      error.textContent = err?.message || String(err);
    }
  }
}

customElements.define("aqara-fp400-zone-card", AqaraFp400ZoneCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "aqara-fp400-zone-card",
  name: "Aqara FP400 zone card",
  description: "Paint detection zones and watch tracked people on the FP400 grid.",
  preview: false,
});
