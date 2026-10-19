# Energy demo UI — plan

Self-serve, near-free visual demo of the energy domain (laptop screens only).
Ecommerce stays a basic CLI demo for infra checks.

## Cost model (hybrid)

- Six suggestion chips **replay recorded runs** (timeline + chart + map): $0
  per view, deterministic.
- Free-text box goes **live**: shared passcode, **30 questions/day** cap
  (Valkey counter), existing 10-turn / 150k-token per-question budget.
  Worst case ~$5/day.
- No memory in the UI — memory stays in the CLI demo (`demos/energy.md`).

## Hosting

- Static site on **GitHub Pages** via a GitHub Action; replay JSON committed
  to the repo (versioned).
- Live mode runs **on the laptop via a free Cloudflare quick tunnel**.
  `make demo-live` prints a share link `?live=<tunnel-url>`. Laptop off →
  text box shows "live mode offline".
- Re-record manually: `make demo-record` (~$1); each replay shows a
  "recorded on {date}" badge.

## UI (three panes: timeline | answer + chart | map)

- **Timeline:** one entry per tool call — path badge (cube / SQL fallback /
  refused), expandable compiled SQL, row count, duration, turns/token meter.
  The model's text between steps sits behind a "show reasoning" toggle.
- **Chart:** the agent calls a `show_chart` tool with a spec (type, x, y,
  series, map key) that names columns. Numbers always come from captured
  tool results, never from LLM text.
- **Map (MapLibre):** on load, facility dots coloured by fuel group, sized
  by registered MW, operating only by default with a status filter; the five
  NEM states shaded by 30-day renewable share. Plain SQL, no LLM. Answers
  keyed by region or facility recolour the map.
- Footer on every view: "Data: Open Electricity, CC BY-NC 4.0". Stays
  non-commercial.

## Stack

Static single-page app + FastAPI server-sent events, in this repo: `ui/` and
`src/api/`. `answer_question` gets an optional event callback, so the CLI and
evals are unchanged.

## Build order

1. **Data:** facility `lat`/`lng` through fetch → pipeline → MDL →
   `energy-test`. Some facilities may have no location.
2. **Agent seam:** event callback + `show_chart` tool; CLI and evals green.
3. **Replay site:** `make demo-record`, the UI, GitHub Pages — shareable here.
4. **Live mode:** FastAPI SSE, passcode, daily cap, `make demo-live`.

## As built (2026-10-19)

All four phases are in place.

| Phase | Pieces |
| --- | --- |
| 1 Data | `latitude`/`longitude` in `scripts/fetch_energy_data.py`, the `facilities` MDL model, and two new `energy-test` gates (14 of 1,016 units unlocated) |
| 2 Agent seam | `src/agent/trace.py` (TraceRecorder hooks + `show_chart`), opt-in via `answer_question(on_event=...)`; CLI and evals unchanged (energy evals 4/4) |
| 3 Replay site | `scripts/demo_record.py`, `ui/` (static, no build step), `.github/workflows/pages.yml`, deep links `?play=<chip id>` |
| 4 Live mode | `src/api/server.py` (passcode, one run at a time, daily cap in Valkey), `scripts/demo_live.py` |

Deviations from the plan:

- **Starlette instead of FastAPI.** Starlette (FastAPI's base) and uvicorn
  were already installed via `mcp`; two endpoints didn't justify a new dependency.
- **Light theme only.** The fuel palette passes the all-pairs colour checks on
  the light surface; no dark step set passes for six fuel colours.
- **Chips:** renewable share NSW, spot price by region, fuel mix on the price
  spike, coal capacity by state, largest wind and solar farms (facility map),
  generation by efficiency (refusal).

Known issue — **cube time filters fail on Trino.** Every `query_cube` time
filter (`time_dimension` ranges or `day:gte:...`) compiles to
`timestamp <= varchar` and Trino rejects it (TYPE_MISMATCH). Non-time
filters work. So every time-windowed question falls back to SQL, and no chip
currently shows the "governed cube" path. It also burns turns on retries:
two chips use 10/10 turns.

## "What's in the data" tab (agreed 2026-10-19)

Visitors don't know what they can ask because they can't see the data. A
second top-level tab shows the governed model underneath, energy only.

- **Placement:** tabs **Ask** | **What's in the data**; the Ask empty state
  links across ("Not sure what to ask? See what's in the data →").
- **Source:** `make demo-record` writes `ui/data/model.json` from the compiled
  MDL (energy cubes, view, models, relationships), plain-SQL facts (row
  counts, window, categorical values, measure ranges) and a small curated
  topics file. Recording fails if the topics file names anything missing
  from the MDL. Columns a topic touches are derived by parsing cube
  expressions and the view SQL; Power stations (no cube) lists its columns.
- **Layout:**
  1. "What it can't answer" strip: daily only, 90 days, no NT, WA south-west
     only, no per-station output (capacity only), no forecasts.
  2. Three-tier diagram: topics → cubes/view → models with every column
     (name + type marker), joins labelled with keys. Hover a column for its
     description and real values/range. Click a topic to light up its path
     down to the exact columns and dim the rest; a side panel shows example
     questions and the topic's limits.
  3. "How we define things": the governed rules from `energy.md`.
- **Example questions:** play the replay if one is recorded; fill the box in
  live mode; else plain text with a "live mode only" note. Stored SQL shows
  in an expandable block.

As built: the tab fills the viewport — a pan/zoom canvas (fit on load,
±, drag, scroll) beside the side panel, which holds the topic list and the
"What it can't answer" box; the rules open in a dialog. marked and DOMPurify
are vendored in `ui/vendor/` and the diagram draws before anything that
needs a library, so a slow or blocked CDN can't blank it. Opening
`ui/index.html` from disk shows how to serve it instead (`make demo-serve`).

## Always-on live questions and a cleaner UI (agreed 2026-10-20)

**UI.** The question buttons go. The answer pane becomes chat-style with the
question box at its bottom ("Ask your own (2 left today)"). A pool of ~24
curated questions across the five topics plus refusal examples, all
pre-recorded, lives in one file; recording checks each still gets the
expected kind of answer. Empty state: 3 suggestion cards auto-rotating one at
a time (~6 s, soft fade; pause on hover/focus; still for reduced motion; no
repeats until the pool is exhausted). After an answer: 3 still "Ask next"
cards from the same topic, unseen first.

**Hosting.** Website stays on GitHub Pages. Live questions run the same stack
(Trino, Iceberg, Lakekeeper, Postgres, Valkey, WrenAI, agent) on one
fixed-size ARM EC2 instance in ap-southeast-2, with its own warehouse prefix
so it never touches the laptop's tables. Asleep → "Start it (about 2
minutes)": bot check, then a Lambda starts the instance; waking doesn't use a
visitor's questions; at most 6 wakes a day. On wake: pull `main`, start the
stack, health-check before accepting questions, refresh data in the
background if older than 3 days.

**Cost ceiling: $20/month.** Idle stop after 20 min; at most 3 h running a
day; 20 live questions a day globally, 2 per visitor; an AWS Budgets action
stops the instance at $20/month (email alerts at 50% and 80%).

**Security.** No inbound ports (outbound Cloudflare tunnel only; no SSH,
Session Manager for emergencies). IAM instance role, no stored keys: hosted
warehouse prefix, the one Bedrock model, registering its address. Every live
question is checked server-side: Turnstile token, per-visitor limit, global
cap, 500 characters, one at a time, turn/token budgets, CORS to the Pages
origin. Read-only WrenAI. The Lambda can only start/stop/describe that one
instance. Secrets (Turnstile secret, kill switch) in SSM Parameter Store.
Encrypted disk, IMDSv2 required, automatic security updates. The passcode is
removed; `demo_live.py --local` stays for laptop testing.

**Owner tasks:** Cloudflare Turnstile widget (site key; secret into SSM),
budget alert email, `terraform apply` with admin credentials.

**Build order:** 1 UI (pool, layout, rotation) · 2 live API (Turnstile,
per-visitor limit, cap, counter, kill switch) · 3 Terraform · 4 instance
startup (pull, stack, refresh, tunnel, register, idle stop) · 5 wake/status UI
and README.

As built (2026-10-20): steps 1-5 are implemented. UI: `ui/suggest.js` +
`demos/energy-questions.yml` (24 recorded). Live API: `src/api/server.py`
(Turnstile, per-visitor + daily limits, kill switch, activity marker).
Hosted server: `terraform/live.tf`, `live_control.tf` (+ `lambda/live_control.py`),
`budget.tf`, `templates/live_user_data.sh.tftpl`; boot and watchdog in
`scripts/hosted/` (start.sh, live_watchdog.py); Docker overrides in `docker/compose.hosted.yaml`; Lakekeeper
uses `aws-system-identity` with an external ID (no stored keys). Deployment
runbook: README, "Live questions: hosted and local".
