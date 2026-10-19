// "What's in the data" tab — renders data/model.json (built by scripts/demo_record.py
// from the compiled MDL, the curated topics file and record-time stats).
// Three tiers: topics → cubes/view → models; SVG edges drawn between measured boxes.
// The diagram has a fixed natural width and is scaled/panned inside the canvas,
// so the whole model fits any window; edge geometry uses unscaled offsets.
// Uses globals from core.js and app.js: $, el, fmt, state, playReplay; marked and DOMPurify (vendor/).

const TYPE_MARK = { TIMESTAMP: "date", DATE: "date", VARCHAR: "text", BOOLEAN: "y/n", DOUBLE: "num", INTEGER: "num", BIGINT: "num", DECIMAL: "num", REAL: "num" };
const TIER_ORDER = {
  // Left-to-right orders chosen so joined boxes sit side by side (fewer crossings).
  topics: ["topic:prices", "topic:generation", "topic:emissions", "topic:renewables", "topic:stations"],
  cubes: ["cube:daily_price", "cube:daily_generation", "cube:renewable_share", "view:generation_with_group"],
  models: ["model:market_daily", "model:generation_daily", "model:fueltech_groups", "model:facilities"],
};

const catalog = {
  data: null,
  selected: null,
  edges: [], // {from, to, kind, fromCol?, toCol?, title?}

  view: { scale: 1, x: 0, y: 0, userMoved: false },

  async init() {
    this.bindCanvas();
    try {
      this.data = await fetch("data/model.json").then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
    } catch (e) {
      this.loadError(e);
      return;
    }
    const d = this.data;
    const rows = d.models.reduce((n, m) => n + (m.rows || 0), 0);
    $("catalog-meta").textContent = `${d.cubes.length} cubes · ${d.views.length} view · ${d.models.length} tables · ${fmt(rows, 0)} rows`;
    // The diagram first: nothing below may stop it from drawing.
    this.render();
    this.showPanel(null);
    const rules = d.rules_md.replace(/^# .*\n/, "");
    try {
      $("rules").innerHTML = DOMPurify.sanitize(marked.parse(rules));
    } catch {
      $("rules").replaceChildren(el("pre", { class: "rules-plain", text: rules }));
    }
  },

  loadError(e) {
    const fromDisk = location.protocol === "file:";
    $("diagram").hidden = true;
    $("canvas").append(el("div", { class: "load-error" },
      el("strong", { text: "The data model couldn't load." }),
      fromDisk
        ? el("p", {}, "This page was opened straight from disk, and browsers block it from reading its data files. Serve it instead: run ", el("code", { text: "make demo-serve" }), " and open ", el("code", { text: "http://localhost:8765/#data" }), ".")
        : el("p", {}, `${e.message}. Run `, el("code", { text: "make demo-record" }), " (or ", el("code", { text: "uv run python scripts/demo_record.py --catalog-only" }), ") to build data/model.json.")));
    $("topic-panel").replaceChildren(el("p", { class: "muted", text: "Nothing to show until the data model loads." }));
  },

  // -- canvas: fit, zoom, pan --------------------------------------------------

  bindCanvas() {
    const canvas = $("canvas");
    $("zoom-in").addEventListener("click", () => this.zoomBy(1.2));
    $("zoom-out").addEventListener("click", () => this.zoomBy(1 / 1.2));
    $("zoom-fit").addEventListener("click", () => { this.view.userMoved = false; this.fit(); });
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = canvas.getBoundingClientRect();
      this.zoomBy(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    let drag = null;
    canvas.addEventListener("pointerdown", (e) => {
      if (e.target.closest("button, .row")) return;
      drag = { x: e.clientX, y: e.clientY, vx: this.view.x, vy: this.view.y };
      canvas.setPointerCapture(e.pointerId);
      canvas.classList.add("dragging");
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!drag) return;
      this.view.x = drag.vx + e.clientX - drag.x;
      this.view.y = drag.vy + e.clientY - drag.y;
      this.view.userMoved = true;
      this.applyView();
    });
    const end = () => { drag = null; canvas.classList.remove("dragging"); };
    canvas.addEventListener("pointerup", end);
    canvas.addEventListener("pointercancel", end);
    new ResizeObserver(() => { if (!this.view.userMoved) this.fit(); }).observe(canvas);
  },

  fit() {
    const canvas = $("canvas"), diagram = $("diagram");
    if (!this.data || canvas.clientWidth === 0) return; // tab hidden; fit on show
    const w = diagram.offsetWidth, h = diagram.offsetHeight;
    const scale = Math.min(canvas.clientWidth / w, canvas.clientHeight / h, 1.25);
    this.view.scale = scale;
    this.view.x = (canvas.clientWidth - w * scale) / 2;
    this.view.y = Math.max(0, (canvas.clientHeight - h * scale) / 2);
    this.applyView();
  },

  zoomBy(factor, cx, cy) {
    const canvas = $("canvas");
    const v = this.view;
    const px = cx ?? canvas.clientWidth / 2, py = cy ?? canvas.clientHeight / 2;
    const scale = Math.min(3, Math.max(0.25, v.scale * factor));
    v.x = px - ((px - v.x) * scale) / v.scale;
    v.y = py - ((py - v.y) * scale) / v.scale;
    v.scale = scale;
    v.userMoved = true;
    this.applyView();
  },

  applyView() {
    const v = this.view;
    $("diagram").style.transform = `translate(${v.x}px, ${v.y}px) scale(${v.scale})`;
    $("col-tip").hidden = true;
  },

  // -- boxes ----------------------------------------------------------------

  render() {
    const d = this.data;
    const byId = Object.fromEntries([...d.topics, ...d.cubes, ...d.views, ...d.models].map((o) => [o.id, o]));
    const ordered = (ids, all) => [...ids.filter((id) => byId[id]), ...all.map((o) => o.id).filter((id) => !ids.includes(id))];

    $("tier-topics").replaceChildren(...ordered(TIER_ORDER.topics, d.topics).map((id) =>
      el("button", { type: "button", class: "node topic", "data-node": id, "aria-pressed": "false", onclick: () => this.select(id) }, byId[id].name)));

    $("tier-cubes").replaceChildren(...ordered(TIER_ORDER.cubes, [...d.cubes, ...d.views]).map((id) => {
      const o = byId[id];
      if (id.startsWith("view:")) return this.box(o, "view", "pre-joined view", o.columns.map((c) => this.colRow(o.id, c, c.type)));
      const members = [
        ...o.measures.map((m) => this.memberRow(o.id, m, "Σ", "measure")),
        ...o.dimensions.map((m) => this.memberRow(o.id, m, m.kind === "time" ? "time" : "by", m.kind)),
      ];
      return this.box(o, "cube", "cube", members);
    }));

    $("tier-models").replaceChildren(...ordered(TIER_ORDER.models, d.models).map((id) => {
      const o = byId[id];
      return this.box(o, "model", o.rows != null ? `table · ${fmt(o.rows, 0)} rows` : "table", o.columns.map((c) => this.colRow(o.id, c, c.type)));
    }));

    // Edges: topic → what it uses; cube → base; view → sources; model ↔ model joins on key columns.
    const edges = [];
    for (const t of d.topics) for (const [a, b] of t.path.edges) if (a === t.id) edges.push({ from: a, to: b, kind: "use" });
    for (const c of d.cubes) edges.push({ from: c.id, to: c.base, kind: "base" });
    for (const v of d.views) for (const s of v.sources) edges.push({ from: v.id, to: `model:${s}`, kind: "base" });
    for (const j of d.joins) for (const k of j.keys) edges.push({ from: k.from, to: k.to, fromCol: k.from_col, toCol: k.to_col, kind: "join", title: `${j.name}: ${j.condition}` });
    this.edges = edges;
    requestAnimationFrame(() => { this.drawEdges(); this.fit(); });
  },

  box(o, kind, subtitle, rows) {
    return el("div", { class: `node box ${kind}`, "data-node": o.id },
      el("div", { class: "box-head", title: o.description },
        el("span", { class: "box-name", text: o.name }),
        el("span", { class: "box-sub", text: subtitle })),
      el("ul", { class: "box-rows" }, ...rows));
  },

  colRow(nodeId, c, type) {
    const li = el("li", { class: "row", "data-node": nodeId, "data-col": c.name, tabindex: "0" },
      el("span", { class: "row-name", text: c.name }),
      el("span", { class: `mark m-${TYPE_MARK[String(type).toUpperCase()] || "other"}`, text: TYPE_MARK[String(type).toUpperCase()] || type }));
    this.bindTip(li, () => this.columnTip(c, type));
    return li;
  },

  memberRow(nodeId, m, mark, kind) {
    const li = el("li", { class: `row member ${kind}`, "data-node": nodeId, "data-col": m.name, tabindex: "0" },
      el("span", { class: "row-name", text: m.name }),
      el("span", { class: `mark m-${kind}`, text: mark }));
    this.bindTip(li, () => [
      el("strong", { text: m.name }),
      el("div", { class: "tip-kind", text: kind === "measure" ? "Measure (computed by the cube)" : kind === "time" ? "Time dimension: window or series by day/week/month" : "Dimension: break results down by this" }),
      el("code", { text: m.expression }),
    ]);
    return li;
  },

  columnTip(c, type) {
    const parts = [el("strong", { text: c.name }), el("div", { class: "tip-kind", text: `${TYPE_MARK[String(type).toUpperCase()] || type}${c.source ? ` · from ${c.source}` : ""}` })];
    if (c.description) parts.push(el("p", { text: c.description }));
    if (c.values?.length) {
      parts.push(el("div", { class: "tip-label", text: `Values (${c.values.length})` }), el("div", { class: "tip-values" }, ...c.values.map((v) => el("code", { text: v }))));
    } else if (c.min != null && c.max != null) {
      const isNum = typeof c.min === "number";
      parts.push(el("div", { class: "tip-label", text: "Range in the data" }), el("div", { text: `${isNum ? fmt(c.min) : c.min} to ${isNum ? fmt(c.max) : c.max}` }));
    }
    if (c.distinct != null && !c.values?.length) parts.push(el("div", { class: "tip-meta", text: `${fmt(c.distinct, 0)} distinct values` }));
    return parts;
  },

  bindTip(node, content) {
    const tip = $("col-tip");
    const show = () => {
      tip.replaceChildren(...content());
      tip.hidden = false;
      const r = node.getBoundingClientRect();
      const w = tip.offsetWidth, h = tip.offsetHeight;
      let left = r.right + 10;
      if (left + w > window.innerWidth - 8) left = r.left - w - 10;
      const top = Math.min(Math.max(8, r.top - 6), window.innerHeight - h - 8);
      tip.style.left = `${Math.max(8, left)}px`;
      tip.style.top = `${top}px`;
    };
    const hide = () => { tip.hidden = true; };
    node.addEventListener("mouseenter", show);
    node.addEventListener("focus", show);
    node.addEventListener("mouseleave", hide);
    node.addEventListener("blur", hide);
  },

  // -- edges ----------------------------------------------------------------

  drawEdges() {
    const diagram = $("diagram"), svg = $("edges");
    if (!diagram || !svg || !this.data || diagram.offsetParent === null) return; // hidden; redraw on show
    svg.setAttribute("width", diagram.offsetWidth);
    svg.setAttribute("height", diagram.offsetHeight);
    // Offsets ignore the zoom transform, so the geometry is in diagram units at any scale.
    const rect = (sel) => {
      const n = diagram.querySelector(sel);
      if (!n) return null;
      let x = 0, y = 0;
      for (let p = n; p && p !== diagram; p = p.offsetParent) { x += p.offsetLeft; y += p.offsetTop; }
      const w = n.offsetWidth, h = n.offsetHeight;
      return { l: x, r: x + w, t: y, b: y + h, cx: x + w / 2, cy: y + h / 2 };
    };
    const paths = [];
    for (const e of this.edges) {
      let d;
      if (e.kind === "join") {
        const a = rect(`.row[data-node="${e.from}"][data-col="${e.fromCol}"]`);
        const b = rect(`.row[data-node="${e.to}"][data-col="${e.toCol}"]`);
        if (!a || !b) continue;
        const [left, right] = a.cx < b.cx ? [a, b] : [b, a];
        const adjacent = right.l > left.r;
        if (adjacent) {
          const mx = (left.r + right.l) / 2;
          d = `M${left.r},${left.cy} C${mx},${left.cy} ${mx},${right.cy} ${right.l},${right.cy}`;
        } else {
          // Not side by side (rare): route below.
          const y = Math.max(left.b, right.b) + 18;
          d = `M${left.cx},${left.b} C${left.cx},${y} ${right.cx},${y} ${right.cx},${right.b}`;
        }
      } else {
        const a = rect(`.node[data-node="${e.from}"]`);
        const b = rect(`.node[data-node="${e.to}"]`);
        if (!a || !b) continue;
        if (Math.abs(a.cy - b.cy) < 4 || (b.t < a.b && b.b > a.t)) {
          // Same tier (cube → view): side to side.
          const [left, right] = a.cx < b.cx ? [a, b] : [b, a];
          const y1 = left.t + 24, y2 = right.t + 24, mx = (left.r + right.l) / 2;
          d = `M${left.r},${y1} C${mx},${y1} ${mx},${y2} ${right.l},${y2}`;
        } else {
          const my = (a.b + b.t) / 2;
          d = `M${a.cx},${a.b} C${a.cx},${my} ${b.cx},${my} ${b.cx},${b.t}`;
        }
      }
      paths.push({ e, d });
    }
    svg.replaceChildren(...paths.map(({ e, d }) => {
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", d);
      p.setAttribute("class", `edge e-${e.kind}`);
      p.dataset.from = e.from;
      p.dataset.to = e.to;
      if (e.fromCol) { p.dataset.fromCol = e.fromCol; p.dataset.toCol = e.toCol; }
      if (e.title) {
        const t = document.createElementNS("http://www.w3.org/2000/svg", "title");
        t.textContent = e.title;
        p.append(t);
      }
      return p;
    }));
    this.applyHighlight();
  },

  // -- topic selection -------------------------------------------------------

  select(id) {
    this.selected = this.selected === id ? null : id;
    document.querySelectorAll(".node.topic").forEach((n) => n.setAttribute("aria-pressed", String(n.dataset.node === this.selected)));
    this.applyHighlight();
    this.showPanel(this.selected);
  },

  applyHighlight() {
    const diagram = $("diagram");
    const topic = this.data?.topics.find((t) => t.id === this.selected);
    diagram.classList.toggle("tracing", Boolean(topic));
    const nodes = new Set(topic ? topic.path.nodes : []);
    const cols = topic ? topic.path.columns : {};
    diagram.querySelectorAll(".node").forEach((n) => n.classList.toggle("hi", nodes.has(n.dataset.node)));
    diagram.querySelectorAll(".row").forEach((r) => r.classList.toggle("hi", Boolean(cols[r.dataset.node]?.includes(r.dataset.col))));
    diagram.querySelectorAll(".edge").forEach((p) => {
      const { from, to, fromCol, toCol } = p.dataset;
      let on;
      if (!topic) on = false;
      else if (from.startsWith("topic:")) on = from === topic.id;
      else if (fromCol) on = Boolean(cols[from]?.includes(fromCol) && cols[to]?.includes(toCol));
      else on = nodes.has(from) && nodes.has(to);
      p.classList.toggle("hi", on);
    });
  },

  showPanel(id) {
    const panel = $("topic-panel");
    const topic = this.data.topics.find((t) => t.id === id);
    if (!topic) {
      panel.replaceChildren(
        el("h2", { text: "What can I ask?" }),
        el("p", { text: "Pick a topic to see example questions and light up the exact data behind them." }),
        el("ul", { class: "topic-list" }, ...this.data.topics.map((t) =>
          el("li", {}, el("button", { type: "button", class: "link", onclick: () => this.select(t.id) }, t.name), el("span", { class: "muted", text: ` — ${t.summary}` })))),
        el("div", { class: "limits-box" },
          el("h3", { text: "What it can't answer" }),
          el("ul", {}, ...this.data.limits.map((l) => el("li", { text: l })))),
        el("h3", { text: "How to read the diagram" }),
        el("ul", { class: "legend-list" },
          el("li", {}, el("span", { class: "mark m-measure", text: "Σ" }), " measure: a governed calculation (sums, averages, shares)"),
          el("li", {}, el("span", { class: "mark m-dimension", text: "by" }), " dimension: what you can break results down by"),
          el("li", {}, el("span", { class: "mark m-time", text: "time" }), " time window or series (day, week, month)"),
          el("li", {}, el("span", { class: "mark m-text", text: "text" }), el("span", { class: "mark m-num", text: "num" }), el("span", { class: "mark m-date", text: "date" }), el("span", { class: "mark m-y/n", text: "y/n" }), " column types")),
        el("div", { class: "panel-actions" },
          el("button", { type: "button", onclick: () => $("rules-dialog").showModal() }, "How we define things: the governed rules →")));
      return;
    }
    const exampleItem = (ex) => {
      const btn = el("button", { type: "button", class: "example", onclick: () => this.ask(ex) }, ex.question);
      const recorded = state.chips.some((c) => c.id === ex.replay);
      const note = recorded ? el("span", { class: "ex-note", text: "▶ recorded run" }) : el("span", { class: "ex-note muted", text: state.live ? "ask live" : "live mode only" });
      const item = el("li", {}, btn, note);
      if (ex.sql) item.append(el("details", { class: "sql" }, el("summary", { text: "Stored SQL for this question" }), el("pre", { text: ex.sql })));
      return item;
    };
    const rank = { cube: 0, view: 1, model: 2 };
    const uses = topic.path.nodes.filter((n) => !n.startsWith("topic:")).sort((a, b) => rank[a.split(":")[0]] - rank[b.split(":")[0]]).map((n) => {
      const [kind, name] = n.split(":");
      return el("li", {}, el("code", { text: name }), ` ${kind === "model" ? "table" : kind}`);
    });
    panel.replaceChildren(
      el("button", { type: "button", class: "link back", onclick: () => this.select(topic.id) }, "← All topics"),
      el("h2", { text: topic.name }),
      el("p", { text: topic.summary }),
      el("h3", { text: "Try asking" }),
      el("ul", { class: "examples" }, ...topic.examples.map(exampleItem)),
      el("h3", { text: "Data it uses" }),
      el("ul", { class: "uses" }, ...uses),
      ...(topic.limits.length ? [el("h3", { text: "Watch out for" }), el("ul", {}, ...topic.limits.map((l) => el("li", { text: l })))] : []));
  },

  ask(ex) {
    const chip = state.chips.find((c) => c.id === ex.replay);
    if (chip) {
      showTab("ask");
      playReplay(chip);
    } else if (state.live) {
      showTab("ask");
      $("question").value = ex.question;
      $("question").focus();
    } else {
      $("live-status").textContent = "That question needs live mode, which is offline. Try one marked ▶ recorded run.";
      showTab("ask");
    }
  },
};

function showTab(name) {
  const data = name === "data";
  $("tab-ask").hidden = data;
  $("tab-data").hidden = !data;
  $("tab-btn-ask").setAttribute("aria-selected", String(!data));
  $("tab-btn-data").setAttribute("aria-selected", String(data));
  document.body.classList.toggle("on-data", data);
  try { history.replaceState(null, "", data ? "#data" : location.pathname + location.search); } catch { /* file:// */ }
  if (data) requestAnimationFrame(() => { catalog.drawEdges(); if (!catalog.view.userMoved) catalog.fit(); });
}

$("tab-btn-ask").addEventListener("click", () => showTab("ask"));
$("tab-btn-data").addEventListener("click", () => showTab("data"));
$("rules-close").addEventListener("click", () => $("rules-dialog").close());
if (location.hash === "#data") showTab("data");
catalog.init();
