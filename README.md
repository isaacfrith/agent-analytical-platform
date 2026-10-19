# Agent Analytical Platform

Ask questions about data in plain English and get answers you can trust.

An AI agent answers each question, but it can't see raw tables and never types the numbers itself. It works through a **semantic layer**: an agreed set of tables, metrics and business rules (for example, "batteries don't count as renewable"). The semantic layer turns the agent's request into SQL, and the SQL runs on a data lakehouse in AWS. Every number in an answer comes from a real query, and you can see exactly which one.

**▶ Try the live demo: [isaacfrith.github.io/agent-analytical-platform](https://isaacfrith.github.io/agent-analytical-platform/)**. Nothing to install.

![The demo: the agent's steps, the answer with its chart, and the map](docs/ui-ask.png)

The platform holds two sets of data:

- **Energy.** 90 days of real Australian electricity data: generation, prices, emissions and power stations for the east-coast market (the NEM) and Western Australia (the WEM), from [Open Electricity](https://openelectricity.org.au). The demo uses this data.
- **Ecommerce.** Generated orders, payments and returns, used to test the infrastructure and the agent's memory.

## Contents

- [Using the demo](#using-the-demo): no setup needed
- [Choose your path](#choose-your-path): what you need for each way of running it
- [Setup](#setup): running the whole platform yourself
- [Asking questions from the terminal](#asking-questions-from-the-terminal)
- [Updating and publishing the demo](#updating-and-publishing-the-demo)
- [Letting others ask live questions](#letting-others-ask-live-questions)
- [How it works](#how-it-works) · [The data](#the-data) · [Quality checks](#quality-checks) · [Project layout](#project-layout) · [Limitations](#scope-and-limitations)

## Using the demo

Open the [live demo](https://isaacfrith.github.io/agent-analytical-platform/). It has two tabs.

### The "Ask" tab

1. **Click a question** at the top, for example *Renewable share, NSW*. It replays a real recorded run of the agent. Replays are free and identical every time.
2. **Read the three panels from left to right:**

   | Panel | What it shows |
   | --- | --- |
   | **How it got there** (left) | Each step the agent took, in order, with how long it took. Expand the SQL links under a step to see the SQL the agent wrote and the SQL the semantic layer compiled. The meters at the top show how much of its budget (10 turns, 150k tokens) the question used. |
   | **Answer** (middle) | The question, the answer, and a chart. The chart is drawn only from rows a query actually returned. |
   | **Map** (right) | Before a question: power stations coloured by fuel and sized by capacity, with states shaded by renewable share. After an answer by region, states are recoloured by that answer. After an answer about power stations, those stations are highlighted. |

3. **Look at the label on each step and on the answer.** It tells you how the agent got the number:

   | Label | Meaning |
   | --- | --- |
   | ✓ **Governed cube** | A pre-defined metric answered the question. This is the most trustworthy path. |
   | ▲ **SQL fallback** | No metric fitted, so the agent wrote SQL, but only against the approved tables. |
   | ✕ **Refused** | The data can't answer the question, and the agent said so instead of guessing. |
   | ✕ **Error** (on a step) | A step failed, and the next step shows how the agent recovered. |

4. Tick **Show reasoning** to see the agent's own notes between steps.

### The "What's in the data" tab

Use this tab to find out what you can ask.

![The data model tab with the Renewables topic traced](docs/ui-data.png)

- **The right-hand panel** lists the five topics you can ask about and what the data **can't** answer. For example, there's no hourly data and no Northern Territory.
- **The diagram** shows the data in three rows: **topics** at the top, the **metrics** (cubes) built for them, and the **tables** underneath with every column and how the tables join.
- **Click a topic** to light up exactly which metrics, tables and columns its questions use. The panel then shows example questions; click one to play its recording.
- **Hover a column** to see what it means and its real values, such as the four power-station statuses or the range of prices.
- **How we define things** (at the bottom of the panel) opens the business rules the agent must follow.
- Drag to move the diagram, scroll or use **− Fit +** to zoom.

### Asking your own questions

The question box at the top only works when the owner is running live mode and has given you a **share link** and a **passcode** (see [Letting others ask live questions](#letting-others-ask-live-questions)). Open the share link, type a question, and enter the passcode when asked. You'll watch the agent work in real time. Without a share link, the box says "Live mode offline".

## Choose your path

| I want to… | What I need | Commands |
| --- | --- | --- |
| **Look at the demo** | A browser | Open the [live demo](https://isaacfrith.github.io/agent-analytical-platform/) |
| **Run the demo site on my machine** | This repo and Python 3 | `make demo-serve`, then open http://localhost:8765 |
| **Ask the agent my own questions** | The full [setup](#setup) | `make ask q="…"` |
| **Re-record the demo with fresh data** | The full setup | [Updating and publishing the demo](#updating-and-publishing-the-demo) |
| **Let other people ask live questions** | The full setup, plus `cloudflared` | [Letting others ask live questions](#letting-others-ask-live-questions) |

The recordings and the data catalog are committed in `ui/data/`, so the first two need no AWS, no Docker and no keys.

## Setup

Running the whole platform yourself: the agent, the semantic layer and the lakehouse.

**You need:**

- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 and the dependencies for you) and Docker.
- An AWS account and a named profile in `~/.aws/credentials` with access to S3 and to an Anthropic model on Bedrock. The template in `.env.example` uses Claude Haiku 4.5.
- A free Open Electricity API key from [openelectricity.org.au](https://openelectricity.org.au), for the energy data.

**Steps:**

```bash
cp .env.example .env              # then fill in: AWS profile, region, bucket name, Bedrock model ID, Open Electricity key
make preflight                    # checks your AWS identity, the bucket and Bedrock access
make infra-init infra-apply       # creates the S3 bucket and IAM roles (Terraform)
make up                           # starts Postgres, Lakekeeper, Trino and Valkey in Docker
make bootstrap                    # one-time: creates the data warehouse on S3
make energy-data                  # loads 90 days of energy data
make data pipeline                # loads the ecommerce data
make mdl memory                   # builds the semantic layer and its search index
make mdl-test energy-test         # checks it all works (no AI involved)
```

`make help` lists every command. When you're done, `make down` stops the local services (your data is kept), and `make infra-destroy` deletes the AWS resources, including the bucket.

**What it costs:** each question uses about 15,000–50,000 tokens. On Claude Haiku 4.5 that's a few cents per question. The checks (`make mdl-test`, `make energy-test`, `make test`) use no AI and cost nothing.

## Asking questions from the terminal

```bash
make ask q="What share of NSW generation was renewable over the last 30 days?"
```

The terminal shows each step the agent takes (`Tool #N:` lines), then the answer, then a `[budget]` line with the turns and tokens it used. For this question (abridged, with notes added after the arrows):

```
Tool #1: get_instructions          ← reads the business rules
Tool #2: describe_cube             ← looks up the renewable_share metric
Tool #3: query_cube                ← asks the semantic layer for the number
**Answer: 46.3% of NSW generation was renewable over the last 30 days.**
[budget] turns=3/10 tokens=19088/150000 (within budget)
```

Questions that show the agent's behaviour well:

| Question | What to look for |
| --- | --- |
| What share of NSW generation was renewable over the last 30 days? | One `query_cube` call: a governed metric does all the work |
| On the highest-price day in the last 30 days, which fuel types generated the most? | A two-step question answered with one SQL query |
| Show me operating coal capacity by state. | Capacity in MW, and "coal" includes Victoria's brown coal |
| Show me generation by unit efficiency. | A refusal: there's no efficiency data, so it says so |
| How many orders are there in total? | No metric fits, so it checks its SQL (`dry_run`), then runs it |
| Show me orders by status. | "status" exists in two tables, so it tells you which one it used |

**Options:**

```bash
uv run python scripts/ask.py --domain energy "Compare emissions for NSW and WA last week"   # energy rules only
make ask-mem q="Remember: high-value orders means total_amount greater than 1000."          # save a definition
make ask-mem q="Show me high-value orders from last week."                                 # a new agent uses it
make memory-check                                                                          # list saved definitions
```

**Teach it a new rule.** This adds the rule to the semantic layer, rebuilds it and reindexes it in one step:

```bash
make refresh rule="Refunds by status always use return_status from returned_orders." title="Refunds by Status"
```

## Updating and publishing the demo

The demo site is plain files in [`ui/`](ui). GitHub Pages republishes it automatically about a minute after every push to `main` that changes `ui/`.

1. **Refresh the data** (optional, since the energy data is a rolling 90 days): `make energy-data`
2. **Re-record the demo:** `make demo-record`. This re-runs the seven demo questions through the real agent, which takes a few minutes and costs about seven questions' worth of tokens. It also rebuilds the map data and the "What's in the data" catalog. Each recording shows a "Recorded on…" date in the UI.
   - Only some questions: `uv run python scripts/demo_record.py --only price_by_region,wa_vs_nem`
   - Only the data tab (free, no AI): `uv run python scripts/demo_record.py --catalog-only`
3. **Check it locally:** `make demo-serve`, then open http://localhost:8765. Always use this: opening `ui/index.html` straight from disk won't work, because browsers block it from loading its data files.
4. **Publish:** commit `ui/data/` and push to `main`.

To change which questions the demo offers, edit `CHIPS` in [`scripts/demo_record.py`](scripts/demo_record.py). To change the topics, example questions and limits on the data tab, edit [`demos/energy-topics.yml`](demos/energy-topics.yml). Recording fails if that file names a table, column or metric that doesn't exist.

## Letting others ask live questions

Live mode lets people type their own questions on the public site. The agent runs **on your laptop**, so it only works while you have it running.

**One-time setup:**

1. Install the tunnel tool: `brew install cloudflared`
2. Pick a passcode and add it to `.env`: `DEMO_PASSCODE=something-long`

**Each time you want to share it:**

1. Make sure the local services are running: `make up`
2. Start live mode: `make demo-live`
3. It prints a **share link**. Send it, along with the passcode, to the people you want to try it:
   ```
   Share link (with the passcode):
     https://isaacfrith.github.io/agent-analytical-platform/?live=https%3A%2F%2Frandom-words.trycloudflare.com
   ```
4. Press **Ctrl-C** to stop. The question box then shows "Live mode offline".

**Things to know:**

- **The link changes every time** you run `make demo-live`, so send a fresh one each session. Only people with the current link can reach your laptop.
- **Spending is capped:** 30 live questions a day in total, across everyone. Change it with `DEMO_DAILY_CAP` in `.env`. Only one question runs at a time.
- **Every question costs tokens** on your AWS account (a few cents each on Haiku 4.5).
- **Testing on your own machine without a tunnel:** run `uv run python scripts/demo_live.py --local` and `make demo-serve`, then open the local link it prints.

There's also a presenter script for demoing from the terminal: [`demos/energy.md`](demos/energy.md).

## How it works

![Architecture](docs/architecture_diagram.png)

**Key terms:**

- **Semantic layer** ([WrenAI](https://github.com/Canner/WrenAI)): the agreed definitions of the data. These are the tables (*models*), how they join, the *cubes* (pre-defined metrics such as "average spot price" or "renewable share"), and the business rules. It's defined in [`wren/`](wren) as an **MDL** (modeling definition language) and turns requests into SQL.
- **Cube:** a governed metric. When the agent asks the `renewable_share` cube for "share by region over 30 days", the definition of "renewable" is fixed in the cube, not left to the AI.
- **MCP:** the protocol the agent uses to call the semantic layer's tools (`query_cube`, `run_sql`, `dry_run` and so on). The tools are read-only: the agent cannot change data.

| Part | Technology | Role |
| --- | --- | --- |
| Agent | Strands SDK, Claude on Amazon Bedrock | A fresh agent for each question, with a budget of 10 turns and 150k tokens |
| Semantic layer | WrenAI | Governed tables, joins, metrics and rules; compiles SQL |
| Query engine | Trino over Apache Iceberg (Lakekeeper catalog, S3 storage) | Runs the SQL |
| Memory | Mem0 over Valkey | Optional: user definitions that persist between sessions |
| Infrastructure | Terraform (AWS), Docker Compose (local) | S3 and IAM on AWS; everything else runs locally |

The agent's instructions are plain text in [`prompts/`](prompts): use metrics first, check SQL before running it, refuse rather than guess, and say which definition it used when a word is ambiguous. The business rules live in the semantic layer itself, in [`wren/knowledge/rules/`](wren/knowledge/rules).

## The data

**Energy.** Refresh with `make energy-data`. Every row is tagged with its market (`network`): the NEM and the WEM are separate, unconnected markets, so "the NEM" never includes WA. The Northern Territory has no public market data and isn't included. Data from Open Electricity, licensed [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) (non-commercial use).

| Table | Rows | One row per… |
| --- | --- | --- |
| `facilities` | ~1,140 | Generating unit: fuel, status, registered capacity (MW), location |
| `generation_daily` | ~5,940 | Region, fuel and day: energy (MWh), emissions (tCO2e), market value (AUD) |
| `market_daily` | 540 | Region and day (6 regions): spot price (AUD/MWh), demand |
| `fueltech_groups` | 29 | Fuel type: its group (coal, gas, solar…) and whether it counts as renewable |

Metrics (cubes): `daily_generation`, `daily_price` and `renewable_share`.

**Ecommerce.** Load with `make data pipeline`. Generated customers, products, 30,000 orders, order items, payments and returns. Metrics: `daily_revenue`, `product_performance` and `refunds_cube`.

## Quality checks

| Command | What it checks | Uses AI? |
| --- | --- | --- |
| `make test` | Unit tests | No |
| `make lint` | Code style (ruff) | No |
| `make mdl-test` | 24 checks on the semantic layer: tables, joins, metrics, example queries | No |
| `make energy-test` | 15 checks on the energy data: coverage, duplicates, markets, locations | No |
| `make evals` | 16 test questions against the real agent, graded automatically (about 30–45 minutes) | Yes |

The eval cases live in [`evaluations/golden_suite.json`](evaluations/golden_suite.json). They cover metric routing, harder data-model questions, refusals and the memory lifecycle. To run just one: `uv run python evaluations/run_evals.py --tests energy_cube_time_window`

## Project layout

```
src/
  agent/          the agent: orchestrator, cube tool, trace events for the UI, memory tools
  api/            live mode for the demo (passcode, daily cap, streaming)
  demo/           builds the demo's data catalog from the semantic layer
  memory/         saved user definitions (Mem0 over Valkey)
  config.py       settings from the environment
  repo_config.py  repo paths and helpers shared by the scripts
scripts/          command-line entry points: ask, load data, checks, record and serve the demo
prompts/          the agent's instructions: shared rules plus one file per dataset
wren/             the semantic layer: tables, views, metrics, joins, rules, example queries
ui/               the demo website (data/ holds the recordings and the catalog)
evaluations/      the eval questions and the harness that grades them
tests/            unit tests
demos/            the terminal presenter script and the demo's topic list
docs/             diagrams, screenshots and the demo's design notes
docker/           the local services
terraform/        the AWS resources
```

## Scope and limitations

This is a single-user proof of concept, not a production system. Data reloads replace everything, saved definitions belong to one user, the agent can run SQL (read-only), and secrets live in `.env`. Upstream's [PRODUCTION_CONSIDERATIONS.md](https://github.com/jaehyeon-kim/agentic-analytics-system/blob/main/PRODUCTION_CONSIDERATIONS.md) covers what production would need.

Two workarounds for bugs in WrenAI 0.15 and 0.16, to remove once they're fixed upstream:

- **Date filters on metrics.** WrenAI writes cube date filters as plain text, which Trino rejects. The agent's own `query_cube` tool ([`src/agent/cube_query.py`](src/agent/cube_query.py)) fixes the dates before the query runs.
- **Row order.** WrenAI's `run_sql` can return the right rows in the wrong order. Charts and answers sort their rows, so you won't see it in the demo.

Inspired by [jaehyeon-kim/agentic-analytics-system](https://github.com/jaehyeon-kim/agentic-analytics-system), adapted for AWS (S3, Bedrock and Terraform).
