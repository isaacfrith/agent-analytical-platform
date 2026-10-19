// Shared by every script: constants, DOM helpers, formatting and page state.
// Load order (index.html): core.js, charts.js, map.js, suggest.js, app.js, catalog.js.

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
  questions: [], // recorded suggested questions (data/replays/index.json)
  topicNames: {}, // topic id -> display name (data/model.json)
  current: null, // the suggested question playing, if any
  runToken: 0, // bumps on every new run; stale playback loops stop
  skip: false,
  live: null, // live API base URL, when live questions are available
  control: null, // wake/status Lambda URL for the hosted server (live-config.json)
  health: null,
  botCheck: null,
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

