// Australian Energy Analyst — one page, two event sources.
// Replay: a recorded event stream from data/replays/<id>.json, played back on its clock.
// Live:   the same events streamed from the backend (?live=<url>), passcode-gated.
// Every renderer consumes the same event shapes (see src/agent/trace.py).

const TURNS_BUDGET = 10;
const TOKENS_BUDGET = 150000;
const MAX_GAP_MS = 700; // replays compress long model pauses

// Fuel colours: validated all-pairs on the light surface (dataviz validator).
// Coal is a deliberate neutral. NT has no public market data, so it stays grey.
const FUEL = {
  coal: { label: "Coal", color: "#52514e" },
  gas: { label: "Gas & oil", color: "#e34948" },
  solar: { label: "Solar", color: "#eda100" },
  wind: { label: "Wind", color: "#1baf7a" },
  hydro: { label: "Hydro", color: "#2a78d6" },
  battery: { label: "Battery & pumps", color: "#4a3aa7" },
  bioenergy: { label: "Bioenergy", color: "#008300" },
  other: { label: "Other", color: "#898781" },
};
const FUEL_ORDER = Object.keys(FUEL);
const STATUSES = ["operating", "commissioning", "committed", "retired"];
const SEQ_RAMP = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"];
const AUS_BOUNDS = [[112.5, -43.8], [154, -10]];
const REGION_NAMES = { NSW1: "New South Wales", QLD1: "Queensland", VIC1: "Victoria", SA1: "South Australia", TAS1: "Tasmania", WEM: "Western Australia" };
const REGION_NOTES = { WEM: "WEM: south-west WA only, a separate market from the NEM" };

// Map a raw fueltech or a governed fueltech_group to a display group.
function fuelGroup(value) {
  const v = String(value || "").toLowerCase();
  if (v.startsWith("coal")) return "coal";
  if (v.startsWith("gas") || v === "distillate" || v === "fossil") return "gas";
  if (v.startsWith("solar")) return "solar";
  if (v.startsWith("wind")) return "wind";
  if (v.startsWith("hydro")) return "hydro";
  if (v.startsWith("battery") || v === "pumps") return "battery";
  if (v.startsWith("bioenergy") || v === "renewable") return "bioenergy";
  return "other";
}

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
};
const fmt = (n, digits) => {
  if (typeof n !== "number" || !isFinite(n)) return String(n ?? "");
  const d = digits ?? (Math.abs(n) >= 100 ? 0 : Math.abs(n) >= 10 ? 1 : 2);
  return n.toLocaleString("en-AU", { maximumFractionDigits: d, minimumFractionDigits: 0 });
};
const isDayString = (v) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}/.test(v);
const dayOnly = (v) => (isDayString(v) ? v.slice(0, 10) : v);
const humanize = (col) => col.replace(/_/g, " ").replace(/\baud\b/g, "AUD").replace(/\bmwh\b/g, "MWh").replace(/\bmw\b/g, "MW").replace(/\bpct\b/g, "%");
const prettyDate = (v) => {
  if (!v) return "";
  const d = new Date(dayOnly(v) + "T00:00:00");
  return d.toLocaleDateString("en-AU", { day: "numeric", month: "short", year: "numeric" });
};

const PATH_LABEL = {
  cube: "✓ Governed cube",
  sql: "▲ SQL fallback",
  refused: "✕ Refused — no data",
  chart: "◆ Chart",
  context: "• Context",
};

// ---------------------------------------------------------------------------
// State

const state = {
  overview: null,
  chips: [],
  runToken: 0, // bumps on every new run; stale playback loops stop
  skip: false,
  live: null,
};

// ---------------------------------------------------------------------------
// Run renderer: consumes events, paints timeline + answer + charts + map.

function resetRun(question, meta = {}) {
  state.runToken += 1;
  state.skip = false;
  $("timeline").replaceChildren();
  $("charts").replaceChildren();
  $("answer-text").replaceChildren(el("p", { class: "thinking", text: "The agent is working…" }));
  $("answer-question").textContent = question;
  $("path-badge").hidden = true;
  $("recorded").textContent = meta.recorded_at
    ? `Recorded ${new Date(meta.recorded_at).toLocaleString("en-AU", { dateStyle: "medium", timeStyle: "short" })}` +
      (meta.data_window ? ` · data ${prettyDate(meta.data_window.first_day)} – ${prettyDate(meta.data_window.last_day)}` : "")
    : meta.live ? "Live run" : "";
  renderMeters(0, 0);
  mapView.reset();
  return state.runToken;
}

function renderMeters(turns, tokens) {
  const meter = (label, value, max, text) => {
    const pct = Math.min(100, (value / max) * 100);
    return el("div", { class: "meter" },
      el("span", { text: label }),
      el("div", { class: "meter-track" }, el("div", { class: "meter-fill" + (pct > 85 ? " hot" : ""), style: `width:${pct}%` })),
      el("span", { class: "meter-val", text }));
  };
  $("meters").replaceChildren(
    meter("Turns", turns, TURNS_BUDGET, `${turns} / ${TURNS_BUDGET}`),
    meter("Tokens", tokens, TOKENS_BUDGET, `${fmt(tokens, 0)} / ${fmt(TOKENS_BUDGET, 0)}`));
}

const stepNodes = new Map();

function handleEvent(ev) {
  switch (ev.type) {
    case "step_start": {
      const node = el("li", { class: "step running" },
        el("div", { class: "step-head" },
          el("span", { class: "step-n", text: `#${ev.step}` }),
          el("span", { class: "step-tool", text: ev.tool }),
          el("span", { class: "step-meta" })),
        describeInput(ev.tool, ev.input));
      stepNodes.set(ev.step, node);
      $("timeline").append(node);
      node.scrollIntoView({ block: "nearest" });
      break;
    }
    case "step_end": {
      const node = stepNodes.get(ev.step);
      if (!node) break;
      node.classList.remove("running");
      const head = node.querySelector(".step-head");
      const badgeKind = ev.status === "error" ? "error" : ev.path;
      const badgeText = ev.status === "error" ? "✕ Error" : PATH_LABEL[ev.path] || ev.path;
      head.insertBefore(el("span", { class: `badge b-${badgeKind}`, text: badgeText }), head.querySelector(".step-meta"));
      head.querySelector(".step-meta").textContent = `${fmt(ev.duration_ms, 0)} ms`;
      if (ev.row_count != null) {
        node.append(el("div", { class: "step-detail", text: `${fmt(ev.row_count, 0)} row${ev.row_count === 1 ? "" : "s"}${ev.truncated ? " (truncated)" : ""} · ${ev.columns.join(", ")}` }));
      }
      if (ev.error) node.append(el("div", { class: "step-error", text: ev.error.replace(/^Error executing tool \w+: /, "").slice(0, 400) }));
      if (ev.sql) {
        node.append(el("details", { class: "sql" },
          el("summary", { text: ev.tool === "query_cube" ? "SQL WrenAI generated from the cube" : "SQL WrenAI compiled for Trino" }),
          el("pre", { text: ev.sql })));
      }
      break;
    }
    case "reasoning":
      $("timeline").append(el("li", { class: "reasoning", text: ev.text }));
      break;
    case "usage":
      renderMeters(ev.turns, ev.tokens);
      break;
    case "chart":
      renderChart(ev);
      if (ev.spec.map) mapView.showAnswer(ev);
      break;
    case "answer": {
      renderMeters(ev.turns, typeof ev.tokens === "number" ? ev.tokens : 0);
      const badge = $("path-badge");
      badge.className = `path-badge b-${ev.path}`;
      badge.textContent = PATH_LABEL[ev.path] || ev.path;
      badge.hidden = false;
      $("answer-text").innerHTML = DOMPurify.sanitize(marked.parse(ev.text || ""));
      $("skip").hidden = true;
      break;
    }
    case "error":
      $("answer-text").replaceChildren(el("p", { class: "step-error", text: `The run failed: ${ev.message}` }));
      $("skip").hidden = true;
      break;
  }
}

function describeInput(tool, input) {
  if (!input) return null;
  if (tool === "query_cube") {
    const parts = [`cube ${input.cube}`];
    if (input.measures?.length) parts.push(`measures ${input.measures.join(", ")}`);
    if (input.dimensions?.length) parts.push(`by ${input.dimensions.join(", ")}`);
    if (input.time_dimension) parts.push(`time ${input.time_dimension}`);
    if (input.filters?.length) parts.push(`where ${input.filters.join(", ")}`);
    return el("div", { class: "step-detail", text: parts.join(" · ") });
  }
  if (tool === "run_sql" || tool === "dry_run") {
    return el("details", { class: "sql" }, el("summary", { text: "SQL the agent wrote (against governed models)" }), el("pre", { text: input.sql.trim() }));
  }
  if (tool === "show_chart") return el("div", { class: "step-detail", text: `${input.kind}: ${input.title}` });
  const args = Object.entries(input).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`);
  return args.length ? el("div", { class: "step-detail", text: args.join(" · ").slice(0, 160) }) : null;
}

// ---------------------------------------------------------------------------
// Charts (Vega-Lite). The spec names columns; rows come from the captured step.

function renderChart(ev) {
  const { spec, rows, columns } = ev;
  const card = el("div", { class: "chart-card" }, el("div", { class: "chart-title", text: spec.title }));
  const plot = el("div");
  card.append(plot);
  const table = dataTable(columns, rows);
  if (spec.kind !== "table") card.append(el("details", {}, el("summary", { text: "Show data table" }), table));
  else card.append(table);
  $("charts").append(card);
  if (spec.kind === "table") return;

  const data = rows.map((r) => {
    const o = {};
    for (const c of columns) o[c] = dayOnly(r[c]);
    return o;
  });
  const xTemporal = data.length && isDayString(data[0][spec.x]);
  const multiY = spec.y.length > 1;
  const colorField = spec.series || (multiY ? "measure" : null);
  const fuelish = colorField && /fuel/.test(colorField);
  const xFuelish = /fuel/.test(spec.x);
  const valueField = multiY ? "value" : spec.y[0];

  const colorScale = (field) => {
    if (/fuel/.test(field)) {
      const domain = [...new Set(data.map((d) => d[field]))];
      return { domain, range: domain.map((v) => FUEL[fuelGroup(v)].color) };
    }
    return { range: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"] };
  };

  const tooltip = [
    { field: spec.x, type: xTemporal ? "temporal" : "nominal", title: humanize(spec.x) },
    ...(colorField ? [{ field: colorField, type: "nominal" }] : []),
    { field: valueField, type: "quantitative", format: ",.2~f", title: multiY ? "value" : humanize(spec.y[0]) },
  ];

  let encoding;
  let mark;
  if (spec.kind === "line") {
    mark = { type: "line", strokeWidth: 2, strokeCap: "round", strokeJoin: "round", point: { filled: true, size: 36 } };
    encoding = {
      x: { field: spec.x, type: xTemporal ? "temporal" : "ordinal", title: null, axis: { format: xTemporal ? "%d %b" : undefined } },
      y: { field: valueField, type: "quantitative", title: multiY ? null : humanize(spec.y[0]), axis: { format: ",.2~s" } },
    };
  } else {
    // Horizontal bars: category labels (regions, facility names) stay readable.
    mark = { type: "bar", cornerRadiusEnd: 4, height: { band: 0.7 } };
    const sortField = spec.kind === "stacked_bar" ? "x" : "-x";
    encoding = {
      y: { field: spec.x, type: xTemporal ? "temporal" : "nominal", title: null, sort: xTemporal ? undefined : sortField },
      x: { field: valueField, type: "quantitative", title: multiY ? null : humanize(spec.y[0]), axis: { format: ",.2~s" }, stack: spec.kind === "stacked_bar" ? "zero" : null },
    };
  }
  if (colorField) {
    encoding.color = { field: colorField, type: "nominal", scale: colorScale(colorField), legend: { orient: "bottom", title: null } };
  } else if (xFuelish && mark.type === "bar") {
    encoding.color = { field: spec.x, type: "nominal", scale: colorScale(spec.x), legend: null };
  } else {
    encoding.color = { value: "#2a78d6" };
  }
  encoding.tooltip = tooltip;

  const categories = new Set(data.map((d) => d[spec.x])).size;
  const vl = {
    $schema: "https://vega.github.io/schema/vega-lite/v5.json",
    data: { values: data },
    transform: multiY ? [{ fold: spec.y, as: ["measure", "value"] }] : [],
    mark,
    encoding,
    width: "container",
    height: mark.type === "bar" && !xTemporal ? Math.max(120, Math.min(categories, 24) * 22) : 220,
    config: {
      background: null,
      font: "system-ui, -apple-system, Segoe UI, sans-serif",
      view: { stroke: null },
      axis: { labelColor: "#52514e", titleColor: "#898781", gridColor: "#e1e0d9", domainColor: "#c3c2b7", tickColor: "#c3c2b7", labelFontSize: 11, titleFontSize: 11, titleFontWeight: 500, labelLimit: 180 },
      legend: { labelColor: "#52514e", labelFontSize: 11, symbolType: "circle" },
      bar: { binSpacing: 2 },
    },
  };
  vegaEmbed(plot, vl, { actions: false, renderer: "svg" }).catch((e) => plot.replaceChildren(el("p", { class: "step-error", text: `Chart failed: ${e.message}` })));
}

function dataTable(columns, rows) {
  const t = el("table", { class: "data-table" });
  t.append(el("thead", {}, el("tr", {}, ...columns.map((c) => el("th", { text: c })))));
  const body = el("tbody");
  for (const r of rows.slice(0, 200)) {
    body.append(el("tr", {}, ...columns.map((c) => {
      const v = r[c];
      return typeof v === "number" ? el("td", { class: "num", text: fmt(v) }) : el("td", { text: dayOnly(v ?? "") });
    })));
  }
  t.append(body);
  return t;
}

// ---------------------------------------------------------------------------
// Map: states shaded by a region metric, facility dots by fuel and capacity.

const mapView = {
  map: null,
  ready: null,
  hiddenFuels: new Set(),
  statuses: new Set(["operating"]),
  highlight: null, // Set of facility codes from an answer, or null
  regionMetric: null, // { title, values: {NSW1: n}, format }

  init(overview, states) {
    this.overview = overview;
    this.states = states;
    this.map = new maplibregl.Map({
      container: "map",
      style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#eef3f8" } }] },
      bounds: AUS_BOUNDS,
      fitBoundsOptions: { padding: 16 },
      attributionControl: false,
      dragRotate: false,
    });
    this.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    this.ready = new Promise((resolve) => this.map.on("load", () => { this.map.resize(); this.addLayers(); resolve(); }));
    new ResizeObserver(() => {
      this.map.resize();
      if (this.overviewShown) this.map.fitBounds(AUS_BOUNDS, { padding: 16, duration: 0 });
    }).observe($("map"));
    this.renderControls();
  },

  addLayers() {
    const m = this.map;
    m.addSource("states", { type: "geojson", data: this.states });
    m.addLayer({ id: "states-fill", type: "fill", source: "states", paint: { "fill-color": "#f0efec", "fill-opacity": 1 } });
    m.addLayer({ id: "states-line", type: "line", source: "states", paint: { "line-color": "#fcfcfb", "line-width": 1.5 } });
    m.addSource("facilities", { type: "geojson", data: this.facilityGeoJSON() });
    m.addLayer({
      id: "facilities",
      type: "circle",
      source: "facilities",
      paint: {
        "circle-color": ["get", "color"],
        "circle-radius": ["*", ["case", ["get", "hi"], 1.4, 1],
          ["interpolate", ["linear"], ["sqrt", ["max", ["get", "capacity_mw"], 0]], 0, 2.5, 10, 4, 30, 9, 55, 15]],
        "circle-opacity": ["case", ["get", "dim"], 0.15, 0.9],
        "circle-stroke-color": ["case", ["get", "hi"], "#0b0b0b", "#fcfcfb"],
        "circle-stroke-width": ["case", ["get", "hi"], 2, 1],
        "circle-stroke-opacity": ["case", ["get", "dim"], 0.15, 1],
      },
    });
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 });
    m.on("mousemove", "facilities", (e) => {
      const p = e.features[0].properties;
      m.getCanvas().style.cursor = "pointer";
      popup.setLngLat(e.lngLat).setHTML(
        `<strong>${escapeHtml(p.facility_name)}</strong><br>${escapeHtml(FUEL[p.group].label)} · ${escapeHtml(p.status)}<br>${fmt(p.capacity_mw, 0)} MW · ${escapeHtml(p.network_region)}`
      ).addTo(m);
    });
    m.on("mouseleave", "facilities", () => { m.getCanvas().style.cursor = ""; popup.remove(); });
    const regionPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
    m.on("mousemove", "states-fill", (e) => {
      if (m.queryRenderedFeatures(e.point, { layers: ["facilities"] }).length) { regionPopup.remove(); return; }
      const p = e.features[0].properties;
      const metric = this.regionMetric;
      const v = p.region && metric ? metric.values[p.region] : null;
      regionPopup.setLngLat(e.lngLat).setHTML(
        p.region ? `<strong>${escapeHtml(REGION_NAMES[p.region] || p.region)}</strong> (${p.region})${REGION_NOTES[p.region] ? `<br><em>${escapeHtml(REGION_NOTES[p.region])}</em>` : ""}${v != null ? `<br>${escapeHtml(metric.title)}: ${fmt(v)}${escapeHtml(metric.unit || "")}` : ""}`
                 : `<strong>${escapeHtml(p.name)}</strong><br>Not covered: no public market data`
      ).addTo(m);
    });
    m.on("mouseleave", "states-fill", () => regionPopup.remove());
    this.reset();
  },

  facilityGeoJSON() {
    const rows = this.overview.facilities;
    return {
      type: "FeatureCollection",
      features: rows
        .filter((r) => this.statuses.has(r.status) || this.highlight?.has(r.facility_code))
        .map((r) => {
          const group = fuelGroup(r.fueltech_group);
          const hidden = this.hiddenFuels.has(group);
          const hi = this.highlight ? this.highlight.has(r.facility_code) : false;
          return {
            type: "Feature",
            geometry: { type: "Point", coordinates: [r.lng, r.lat] },
            properties: { ...r, group, color: FUEL[group].color, dim: hidden || Boolean(this.highlight && !hi), hi },
          };
        })
        .filter((f) => !(this.hiddenFuels.has(f.properties.group) && !f.properties.hi))
        // Big sites first so small ones stay clickable on top; answer sites topmost.
        .sort((a, b) => (a.properties.hi - b.properties.hi) || (b.properties.capacity_mw - a.properties.capacity_mw)),
    };
  },

  refreshFacilities() {
    const src = this.map?.getSource("facilities");
    if (src) src.setData(this.facilityGeoJSON());
  },

  shadeRegions(metric) {
    this.regionMetric = metric;
    const vals = Object.values(metric.values).filter((v) => typeof v === "number");
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const stops = SEQ_RAMP.map((c, i) => [lo + ((hi - lo) * i) / (SEQ_RAMP.length - 1), c]);
    const match = ["match", ["get", "region"]];
    for (const [region, v] of Object.entries(metric.values)) {
      if (typeof v !== "number") continue;
      const step = hi === lo ? SEQ_RAMP[2] : stops.reduce((acc, [s, c]) => (v >= s ? c : acc), SEQ_RAMP[0]);
      match.push(region, step);
    }
    match.push("#f0efec");
    this.map.setPaintProperty("states-fill", "fill-color", match.length > 3 ? match : "#f0efec");
    $("region-legend").replaceChildren(
      el("span", { text: metric.title }),
      el("span", { text: `${fmt(lo)}${metric.unit || ""}` }),
      el("span", { class: "ramp", style: `background:linear-gradient(90deg,${SEQ_RAMP.join(",")})` }),
      el("span", { text: `${fmt(hi)}${metric.unit || ""}` }),
      el("span", { style: "color:#898781", text: "· grey: no value · NT not covered" }));
  },

  async reset() {
    await this.ready;
    this.highlight = null;
    this.overviewShown = true;
    $("map-title").textContent = "Australia at a glance";
    $("map-reset").hidden = true;
    const values = Object.fromEntries(this.overview.regions.map((r) => [r.network_region, r.renewable_pct]));
    this.shadeRegions({ title: "Renewable share, last 30 days", values, unit: "%" });
    this.refreshFacilities();
    this.map.fitBounds(AUS_BOUNDS, { padding: 16, duration: 600 });
  },

  async showAnswer(ev) {
    await this.ready;
    const { key, metric } = ev.spec.map;
    const rows = ev.rows;
    this.overviewShown = false;
    $("map-reset").hidden = false;
    if (key === "network_region" || rows.every((r) => REGION_NAMES[r[key]])) {
      const values = {};
      for (const r of rows) if (typeof r[metric] === "number") values[r[key]] = (values[r[key]] ?? 0) + r[metric];
      this.shadeRegions({ title: ev.spec.title, values });
      $("map-title").textContent = "Answer, by region";
      return;
    }
    // Facility answers: highlight those sites (all statuses), dim the rest.
    this.highlight = new Set(rows.map((r) => r[key]));
    this.refreshFacilities();
    $("map-title").textContent = "Answer, by facility";
    const pts = this.overview.facilities.filter((f) => this.highlight.has(f.facility_code));
    if (pts.length) {
      const b = new maplibregl.LngLatBounds();
      pts.forEach((p) => b.extend([p.lng, p.lat]));
      this.map.fitBounds(b, { padding: 50, maxZoom: 7, duration: 800 });
    }
  },

  renderControls() {
    const groups = new Set(this.overview.facilities.map((f) => fuelGroup(f.fueltech_group)));
    $("fuel-legend").replaceChildren(...FUEL_ORDER.filter((g) => groups.has(g)).map((g) =>
      el("button", {
        type: "button",
        class: "fuel-item",
        "aria-pressed": String(!this.hiddenFuels.has(g)),
        onclick: () => {
          this.hiddenFuels.has(g) ? this.hiddenFuels.delete(g) : this.hiddenFuels.add(g);
          this.renderControls();
          this.refreshFacilities();
        },
      }, el("span", { class: "dot", style: `background:${FUEL[g].color}` }), FUEL[g].label)));
    $("status-filter").replaceChildren(el("span", { text: "Show:" }), ...STATUSES.map((s) => {
      const box = el("input", { type: "checkbox", checked: this.statuses.has(s) });
      box.addEventListener("change", () => {
        box.checked ? this.statuses.add(s) : this.statuses.delete(s);
        this.refreshFacilities();
      });
      return el("label", {}, box, s);
    }), el("span", { style: "color:#898781", text: "· dot size = registered MW" }));
  },
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function showIntro() {
  const badge = (kind, text) => el("span", { class: `badge b-${kind}`, text });
  $("answer-text").replaceChildren(
    el("p", { text: "Ask a question about Australia's two main electricity markets: the east-coast NEM and Western Australia's WEM. The agent can't see raw tables. It works through a governed semantic layer (WrenAI) that defines what \"renewable\", \"price\" and \"capacity\" mean, and it compiles the SQL." }),
    el("p", { text: "Pick a demo question above to replay a real run. On the left, watch each step it took:" }),
    el("ul", {},
      el("li", {}, badge("cube", PATH_LABEL.cube), " a governed metric answered the question"),
      el("li", {}, badge("sql", PATH_LABEL.sql), " no metric fit, so it wrote SQL against governed models"),
      el("li", {}, badge("refused", PATH_LABEL.refused), " the data can't answer it, and it says so instead of guessing")),
    el("p", { text: "Charts are drawn only from rows the semantic layer returned. The agent picks the columns but never types the numbers." }));
}

// ---------------------------------------------------------------------------
// Replay

async function playReplay(chip) {
  document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.id === chip.id)));
  let replay;
  try {
    replay = await fetch(`data/replays/${chip.id}.json`).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
  } catch (e) {
    resetRun(chip.question);
    handleEvent({ type: "error", message: `could not load the recording (${e.message})` });
    return;
  }
  const token = resetRun(replay.question, replay);
  $("skip").hidden = false;
  let prevT = 0;
  for (const ev of replay.events) {
    if (token !== state.runToken) return; // another run started
    if (!state.skip) {
      const gap = Math.min(MAX_GAP_MS, Math.max(0, (ev.t - prevT) * 1000));
      if (gap > 30) await new Promise((r) => setTimeout(r, gap));
      if (token !== state.runToken) return;
    }
    prevT = ev.t;
    handleEvent(ev);
  }
}

// ---------------------------------------------------------------------------
// Live mode: POST the question, read server-sent events off the response body.

async function initLive() {
  const live = new URLSearchParams(location.search).get("live");
  const status = $("live-status");
  if (!live) {
    status.textContent = "Demo questions replay recorded runs, so they're free to view. Live questions need a share link.";
    return;
  }
  state.live = live.replace(/\/$/, "");
  status.textContent = "Checking live mode…";
  try {
    const health = await fetch(`${state.live}/api/health`).then((r) => r.json());
    $("question").disabled = false;
    $("ask-btn").disabled = false;
    $("question").placeholder = "Ask anything about the NEM or WA…";
    status.textContent = `Live mode on · ${health.remaining} of ${health.daily_cap} live questions left today`;
  } catch {
    status.textContent = "Live mode offline. Try a demo question above.";
  }
}

async function askLive(question) {
  let passcode = sessionStorageGet("passcode");
  if (!passcode) {
    passcode = prompt("Passcode for live questions:");
    if (!passcode) return;
  }
  document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", "false"));
  const token = resetRun(question, { live: true });
  $("ask-btn").disabled = true;
  try {
    const resp = await fetch(`${state.live}/api/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, passcode }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      if (resp.status === 401) sessionStorageSet("passcode", "");
      handleEvent({ type: "error", message: body.detail || `HTTP ${resp.status}` });
      return;
    }
    sessionStorageSet("passcode", passcode);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done || token !== state.runToken) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const data = chunk.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("\n");
        if (data) handleEvent(JSON.parse(data));
      }
    }
  } catch (e) {
    handleEvent({ type: "error", message: `live mode unreachable (${e.message})` });
  } finally {
    $("ask-btn").disabled = false;
    initLive();
  }
}

function sessionStorageGet(k) { try { return sessionStorage.getItem(k); } catch { return null; } }
function sessionStorageSet(k, v) { try { v ? sessionStorage.setItem(k, v) : sessionStorage.removeItem(k); } catch { /* private mode */ } }

// ---------------------------------------------------------------------------
// Boot

async function boot() {
  $("show-reasoning").addEventListener("change", (e) => document.body.classList.toggle("show-reasoning", e.target.checked));
  $("skip").addEventListener("click", () => { state.skip = true; });
  $("map-reset").addEventListener("click", () => mapView.reset());
  $("ask-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("question").value.trim();
    if (q && state.live) askLive(q);
  });

  const [overview, states, index] = await Promise.all([
    fetch("data/overview.json").then((r) => r.json()),
    fetch("data/australia-states.geojson").then((r) => r.json()),
    fetch("data/replays/index.json").then((r) => (r.ok ? r.json() : { chips: [] })).catch(() => ({ chips: [] })),
  ]);
  state.overview = overview;
  mapView.init(overview, states);
  state.chips = index.chips.filter((c) => c.ok);
  $("chips").replaceChildren(...state.chips.map((c) =>
    el("button", { type: "button", class: "chip", role: "listitem", "data-id": c.id, "aria-pressed": "false", title: c.question, onclick: () => playReplay(c) }, c.label)));
  showIntro();
  initLive();
  // Deep link: ?play=<chip id> starts that replay.
  const deep = state.chips.find((c) => c.id === new URLSearchParams(location.search).get("play"));
  if (deep) playReplay(deep);
}

boot();
