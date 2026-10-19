// The map: states shaded by a region metric, facility dots by fuel and capacity.

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
