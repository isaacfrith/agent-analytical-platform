// Australian Energy Analyst — one page, two event sources.
// Replay: a recorded event stream from data/replays/<id>.json, played back on its clock.
// Live:   the same events streamed from the backend (?live=<url>), passcode-gated.
// Every renderer consumes the same event shapes (see src/agent/trace.py).

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
    case "chart": {
      renderChart(ev);
      const map = ev.spec.map || inferRegionMap(ev);
      if (map) mapView.showAnswer({ ...ev, spec: { ...ev.spec, map } });
      break;
    }
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

// Region rows renamed by the agent (e.g. "state") still map: recognise them by value.
function inferRegionMap(ev) {
  const key = ev.columns.find((c) => ev.rows.length && ev.rows.every((r) => REGION_NAMES[r[c]]));
  const metric = ev.spec.y.find((c) => ev.rows.some((r) => typeof r[c] === "number"));
  return key && metric ? { key, metric } : null;
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

function showIntro() {
  const badge = (kind, text) => el("span", { class: `badge b-${kind}`, text });
  $("answer-text").replaceChildren(
    el("p", { text: "Ask a question about Australia's two main electricity markets: the east-coast NEM and Western Australia's WEM. The agent can't see raw tables. It works through a governed semantic layer (WrenAI) that defines what \"renewable\", \"price\" and \"capacity\" mean, and it compiles the SQL." }),
    el("p", { text: "Pick a demo question above to replay a real run. On the left, watch each step it took:" }),
    el("ul", {},
      el("li", {}, badge("cube", PATH_LABEL.cube), " a governed metric answered the question"),
      el("li", {}, badge("sql", PATH_LABEL.sql), " no metric fit, so it wrote SQL against governed models"),
      el("li", {}, badge("refused", PATH_LABEL.refused), " the data can't answer it, and it says so instead of guessing")),
    el("p", { text: "Charts are drawn only from rows the semantic layer returned. The agent picks the columns but never types the numbers." }),
    el("p", {}, el("button", { type: "button", class: "link", onclick: () => showTab("data") }, "Not sure what to ask? See what's in the data →")));
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
  try {
    mapView.init(overview, states);
  } catch (e) {
    // The map library comes from a CDN; without it the rest of the page still works.
    $("map").replaceChildren(el("p", { class: "step-error", text: `The map couldn't load (${e.message}). Reload to try again.` }));
  }
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
