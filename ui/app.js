// Australian Energy Analyst — one page, two event sources.
// Replay: a recorded event stream from data/replays/<id>.json, played back on its clock.
// Live:   the same events streamed from the backend (?live=<url>).
// Every renderer consumes the same event shapes (see src/agent/trace.py).

// ---------------------------------------------------------------------------
// Run renderer: consumes events, paints timeline + answer + charts + map.

function resetRun(question, meta = {}) {
  state.runToken += 1;
  state.skip = false;
  state.current = meta.id ? state.questions.find((q) => q.id === meta.id) || null : null;
  suggest.clear();
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
      suggest.showNext(state.current);
      break;
    }
    case "error":
      $("answer-text").replaceChildren(el("p", { class: "step-error", text: `The run failed: ${ev.message}` }));
      $("skip").hidden = true;
      suggest.showNext(state.current);
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
  $("answer-question").textContent = "Ask about Australia's electricity";
  $("answer-text").replaceChildren(
    el("p", { text: "Real data for the east-coast NEM and Western Australia's WEM. The agent can't see raw tables: it asks a governed semantic layer, which defines what \"renewable\", \"price\" and \"capacity\" mean and compiles the SQL. Every number comes from a real query." }),
    el("p", { class: "legend-line" },
      badge("cube", PATH_LABEL.cube), " governed metric ",
      badge("sql", PATH_LABEL.sql), " SQL on approved tables ",
      badge("refused", PATH_LABEL.refused), " won't guess"),
    el("p", {}, el("button", { type: "button", class: "link", onclick: () => showTab("data") }, "See what's in the data →")));
  suggest.showStart();
}

// ---------------------------------------------------------------------------
// Replay

async function playReplay(q) {
  suggest.seen.add(q.id);
  $("answer-scroll").scrollTop = 0;
  let replay;
  try {
    replay = await fetch(`data/replays/${q.id}.json`).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
  } catch (e) {
    resetRun(q.question, { id: q.id });
    handleEvent({ type: "error", message: `could not load the recording (${e.message})` });
    return;
  }
  const token = resetRun(replay.question, { ...replay, id: q.id });
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
// Live questions: POST the question, read server-sent events off the response body.
// The server enforces every limit; the page only reflects them.

const TURNSTILE_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

// Two ways to find the live API:
//   ?live=<url>                     a server you run yourself (make demo-live)
//   live-config.json control_url    the hosted server, woken on demand through
//                                   the wake/status Lambda (terraform/live_control.tf)
async function initLive() {
  const live = new URLSearchParams(location.search).get("live");
  if (live) {
    state.live = live.replace(/\/$/, "");
    await refreshLive();
    return;
  }
  const cfg = await fetch("live-config.json").then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
  if (!cfg.control_url) {
    setLive(false, "Suggestions replay real recorded runs, free and instant.");
    return;
  }
  state.control = cfg.control_url.replace(/\/$/, "");
  await checkServer();
}

const WAKE_POLL_MS = 5000;
const WAKE_GIVE_UP_MS = 8 * 60 * 1000;

// Ask the wake Lambda where the hosted server is, and offer to wake it if asleep.
async function checkServer() {
  let s;
  try {
    s = await fetch(`${state.control}/status`).then((r) => r.json());
  } catch {
    setLive(false, "Live questions are offline right now. Pick a suggestion.");
    return null;
  }
  if (s.turnstile_site_key && s.turnstile_site_key !== "-") loadBotCheck(s.turnstile_site_key);
  if (s.ready && s.url) {
    showWake(false);
    state.live = s.url.replace(/\/$/, "");
    await refreshLive(true);
  } else if (s.state === "running" && /failed|missing/.test(s.stage || "")) {
    showWake(false);
    setLive(false, "The live server couldn't start. Try again later; suggestions are free.");
  } else if (s.state === "pending" || s.state === "running") {
    showWake(false);
    setLive(false, `Starting the live server: ${s.stage || "booting"}…`, "Live questions are starting up — about 2 minutes");
  } else if (!s.enabled) {
    showWake(false);
    setLive(false, "Live questions are switched off right now. Pick a suggestion.");
  } else if (s.can_wake) {
    showWake(true);
    setLive(false, "Live questions sleep when nobody's asking. Start them to ask your own (takes about 2 minutes).", "Live questions are asleep — start them below");
  } else {
    showWake(false);
    setLive(false, "Live questions have used today's allowance. Come back tomorrow; suggestions are free.");
  }
  return s;
}

function showWake(on) {
  $("wake-btn").hidden = !on;
  $("wake-btn").disabled = false;
}

async function wakeServer() {
  $("wake-btn").disabled = true;
  setLive(false, "Checking you're human…");
  const turnstile_token = await botToken();
  let resp;
  try {
    resp = await fetch(`${state.control}/wake`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ turnstile_token }),
    });
  } catch (e) {
    showWake(true);
    setLive(false, `Couldn't reach the live server's switch (${e.message}).`);
    return;
  }
  if (!resp.ok && resp.status !== 202) {
    const body = await resp.json().catch(() => ({}));
    showWake(body.code === "bot_check");
    setLive(false, body.detail || `Couldn't start live questions (HTTP ${resp.status}).`);
    return;
  }
  showWake(false);
  const started = Date.now();
  while (Date.now() - started < WAKE_GIVE_UP_MS) {
    await new Promise((r) => setTimeout(r, WAKE_POLL_MS));
    const s = await checkServer();
    if (state.live || !s || s.state === "stopped" || /failed|missing/.test(s.stage || "")) return;
  }
  setLive(false, "The live server is taking too long to start. Try again later; suggestions are free.");
}

// fromCheck: called by checkServer, which just said the server is ready. If the API
// still doesn't answer, say so and stop, rather than bouncing back to checkServer.
async function refreshLive(fromCheck = false) {
  let health;
  try {
    health = await fetch(`${state.live}/api/health`).then((r) => r.json());
  } catch {
    state.live = state.control ? null : state.live;
    if (state.control && !fromCheck) {
      await checkServer(); // the server may have gone to sleep since
      return;
    }
    setLive(false, "Live questions are offline right now. Pick a suggestion.");
    return;
  }
  state.health = health;
  if (!health.enabled) {
    setLive(false, "Live questions are switched off right now. Pick a suggestion.");
  } else if (health.visitor_remaining <= 0) {
    setLive(false, "You've used today's live questions. Suggestions are always free.", "You've used today's live questions — come back tomorrow");
  } else {
    const left = `${health.visitor_remaining} left today`;
    setLive(true, `Live: your question runs the real agent now (${left}). Suggestions are free.`, `Ask your own question (${left})`);
    if (health.bot_check && health.turnstile_site_key) loadBotCheck(health.turnstile_site_key);
  }
}

function setLive(on, status, placeholder) {
  $("question").disabled = !on;
  $("ask-btn").disabled = !on;
  $("question").placeholder = placeholder || "Live questions are unavailable right now — pick a suggestion";
  $("live-status").textContent = status;
}

// Cloudflare Turnstile, rendered once and invisibly; it only shows a challenge if unsure.
function loadBotCheck(siteKey) {
  if (state.botCheck) return;
  let resolveReady;
  const ready = new Promise((resolve) => { resolveReady = resolve; });
  state.botCheck = { ready, resolveReady };
  const script = el("script", { src: TURNSTILE_SRC, async: true, defer: true });
  script.addEventListener("load", () => {
    state.botCheck.widget = turnstile.render("#bot-check", {
      sitekey: siteKey,
      execution: "execute",
      appearance: "interaction-only",
      callback: (token) => state.botCheck.onToken?.(token),
      "error-callback": () => state.botCheck.onToken?.(""),
    });
    state.botCheck.resolveReady();
  });
  document.head.append(script);
}

const BOT_CHECK_TIMEOUT_MS = 15000;

// A fresh single-use token, or "" if the check can't run (the server then refuses politely).
async function botToken() {
  if (!state.botCheck) return "";
  const timeout = new Promise((resolve) => setTimeout(() => resolve(""), BOT_CHECK_TIMEOUT_MS));
  const token = state.botCheck.ready.then(() => new Promise((resolve) => {
    state.botCheck.onToken = resolve;
    turnstile.reset(state.botCheck.widget);
    turnstile.execute(state.botCheck.widget);
  }));
  return Promise.race([token, timeout]);
}

async function askLive(question) {
  setLive(false, "Checking you're human…");
  const turnstile_token = await botToken();
  const token = resetRun(question, { live: true });
  $("question").value = "";
  $("live-status").textContent = "The agent is working on your question…";
  try {
    const resp = await fetch(`${state.live}/api/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, turnstile_token }),
    });
    if (!resp.ok) {
      // A refusal from the limits, not a failed run: say so calmly and go back to suggestions.
      const body = await resp.json().catch(() => ({}));
      showIntro();
      $("live-status").textContent = body.detail || `Live questions are unavailable (HTTP ${resp.status}).`;
      return;
    }
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
    await refreshLive();
  }
}

// ---------------------------------------------------------------------------
// Boot

async function boot() {
  $("show-reasoning").addEventListener("change", (e) => document.body.classList.toggle("show-reasoning", e.target.checked));
  $("skip").addEventListener("click", () => { state.skip = true; });
  $("map-reset").addEventListener("click", () => mapView.reset());
  $("wake-btn").addEventListener("click", wakeServer);
  $("ask-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("question").value.trim();
    if (q && state.live) askLive(q);
  });

  const empty = { questions: [], topics: [] };
  const [overview, states, index, model] = await Promise.all([
    fetch("data/overview.json").then((r) => r.json()),
    fetch("data/australia-states.geojson").then((r) => r.json()),
    fetch("data/replays/index.json").then((r) => (r.ok ? r.json() : empty)).catch(() => empty),
    fetch("data/model.json").then((r) => (r.ok ? r.json() : empty)).catch(() => empty),
  ]);
  state.overview = overview;
  try {
    mapView.init(overview, states);
  } catch (e) {
    // The map library comes from a CDN; without it the rest of the page still works.
    $("map").replaceChildren(el("p", { class: "step-error", text: `The map couldn't load (${e.message}). Reload to try again.` }));
  }
  // Only questions that recorded cleanly are offered.
  state.questions = (index.questions || index.chips || []).filter((q) => q.ok);
  state.topicNames = Object.fromEntries((model.topics || []).map((t) => [t.id.replace(/^topic:/, ""), t.name]));
  showIntro();
  initLive();
  // Deep link: ?play=<question id> starts that replay.
  const deep = state.questions.find((q) => q.id === new URLSearchParams(location.search).get("play"));
  if (deep) playReplay(deep);
}

boot();
