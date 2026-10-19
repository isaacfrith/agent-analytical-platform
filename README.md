# Agent Analytical Platform

Ask questions about data in plain English and get answers you can trust. An AI agent works through a **governed semantic layer**: it never sees raw tables and never types the numbers itself. WrenAI compiles every query from governed models, metrics and business rules, and Trino runs it over an Iceberg lakehouse on S3.

![The demo UI: the agent's steps, the answer with its chart, and the map](docs/ui-ask.png)

Two domains share the platform:

- **Energy** (the showcase): 90 days of live Australian electricity data for the east-coast NEM and Western Australia's WEM, from [Open Electricity](https://openelectricity.org.au). It comes with a web demo: recorded runs anyone can replay, a map, and a browsable data model.
- **Ecommerce**: generated orders, payments and returns. A small, deterministic domain for testing the infrastructure and the agent's memory.

Inspired by [jaehyeon-kim/agentic-analytics-system](https://github.com/jaehyeon-kim/agentic-analytics-system), adapted for AWS (S3, Bedrock, Terraform).

## How it works

![Architecture](docs/architecture_diagram.png)

| Component | Technology | Role |
| --- | --- | --- |
| Agent | Strands SDK on Bedrock | One fresh agent per question; plans tool calls under a turn and token budget |
| Semantic layer | WrenAI (MDL) | Governed models, joins, cubes (metrics) and business rules, exposed as MCP tools |
| Execution | Trino + Iceberg (Lakekeeper catalog, S3 storage) | Runs the SQL WrenAI compiles |
| Memory | Mem0 over Valkey | Optional: user definitions that persist across sessions |
| Infrastructure | Terraform (AWS) + Docker Compose (local) | S3 bucket and IAM on AWS; Postgres, Lakekeeper, Trino and Valkey locally |

The agent's policy is written down in [`prompts/`](prompts): metrics first (cubes), validate SQL before running it, refuse instead of guessing, and say which definition was used when a word is ambiguous. The business rules live in the semantic layer itself ([`wren/knowledge/rules/`](wren/knowledge/rules)).

## Quick start

You need [uv](https://docs.astral.sh/uv/) (Python 3.12+), Docker, and an AWS profile with Bedrock and S3 access.

```bash
cp .env.example .env              # AWS profile, region, bucket, Bedrock model, Open Electricity key
make preflight                    # check AWS identity, bucket and Bedrock access
make infra-init infra-apply       # create the S3 bucket and IAM roles (Terraform)
make up                           # start Postgres, Lakekeeper, Trino and Valkey
make bootstrap                    # one-time: create the S3-backed warehouse
make energy-data                  # load the energy data (needs OPENELECTRICITY_API_KEY)
make data pipeline                # load the ecommerce data
make mdl memory                   # compile the semantic layer and index it
```

`make help` lists every target. `make down` stops the local stack (data survives); `make infra-destroy` deletes the AWS resources, including the bucket.

## Try it

Every answer prints to stdout, and the agent's trace to stderr: one `Tool #N:` line per step, then a `[budget]` line. The trace is where you see the governance working.

```bash
make ask q="What share of NSW generation was renewable over the last 30 days?"
```

| Question | What to watch for |
| --- | --- |
| What share of NSW generation was renewable over the last 30 days? | One `query_cube` call on `renewable_share`, using the governed share measure |
| On the highest-price day in the last 30 days, which fuel types generated the most? | A two-step question answered with a single SQL query |
| Show me operating coal capacity by state. | Capacity in MW from the registry; "coal" includes Victoria's brown coal |
| Show me generation by unit efficiency. | A refusal: no such data, so it says so instead of inventing a metric |
| How many orders are there in total? | No metric fits, so it validates SQL (`dry_run`) and then runs it (30,000) |
| Show me orders by status. | `status` exists on two tables, so it states which one it used |

Domain flags and memory:

```bash
uv run python scripts/ask.py --domain energy "Compare emissions for NSW and WA last week"
make ask-mem q="Remember: high-value orders means total_amount greater than 1000."
make ask-mem q="Show me high-value orders from last week."     # a fresh agent recalls it
make memory-check
```

Teach the semantic layer a new rule (rebuilds and reindexes in one step):

```bash
make refresh rule="Refunds by status always use return_status from returned_orders." title="Refunds by Status"
```

## The demo UI

A static site in [`ui/`](ui), published to GitHub Pages on every push to `main`.

- **Ask.** Seven demo questions replay recorded runs, free to view and identical every time. Each one shows the agent's steps (labelled governed cube, SQL fallback or refused), the SQL WrenAI compiled, a chart drawn only from rows the semantic layer returned, and a map that recolours by region or highlights power stations.
- **What's in the data.** The governed model as a diagram: topics, cubes, the pre-joined view and the tables, with every column and join. Click a topic to light up exactly which columns its questions use; hover a column for its meaning and real values.

![The data model tab with the Renewables topic traced](docs/ui-data.png)

```bash
make demo-serve     # view locally at http://localhost:8765 (serve it; don't open the file directly)
make demo-record    # re-record the questions, map and data catalog (~$1 of Bedrock)
make demo-live      # live questions from this laptop through a Cloudflare quick tunnel
```

Commit `ui/data/` after recording to publish it. Live mode needs `DEMO_PASSCODE` in `.env` and `brew install cloudflared`, and is capped at 30 questions a day. The design and its decisions are in [`docs/energy-ui.md`](docs/energy-ui.md); a presenter script for the CLI is in [`demos/energy.md`](demos/energy.md).

## The data

**Energy** (`energy` namespace, refreshed with `make energy-data`). Every fact row carries `network`: the NEM and the WEM are separate, unconnected markets, so "the NEM" never includes WA. The Northern Territory has no public market data and is not covered. Data from Open Electricity, licensed [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/).

| Table | Rows | Grain |
| --- | --- | --- |
| `facilities` | ~1,140 | Generating unit: fuel, status, registered capacity (MW), location |
| `generation_daily` | ~5,940 | Region × fuel × day: energy (MWh), emissions (tCO2e), market value (AUD) |
| `market_daily` | 540 | Region × day, 6 regions: spot price (AUD/MWh), demand |
| `fueltech_groups` | 29 | Governed mapping: fuel tech → fuel group and the `is_renewable` flag |

Three cubes sit on top: `daily_generation`, `daily_price` and `renewable_share`.

**Ecommerce** (`ecommerce` namespace, `make data pipeline`): generated customers, products, orders (30,000), order items, payments and returns, with the `daily_revenue`, `product_performance` and `refunds_cube` cubes.

## Quality

```bash
make test           # unit tests (no services needed)
make lint           # ruff lint and format check
make mdl-test       # 24 checks on the semantic layer: models, joins, cubes, example queries (no LLM)
make energy-test    # 15 checks on the energy data: coverage, keys, networks, locations (no LLM)
make evals          # 16 cases against the real agent (~30-45 min)
```

The eval suite ([`evaluations/golden_suite.json`](evaluations/golden_suite.json)) covers cube routing, semantic-model questions, refusals and the memory lifecycle. A judge model grades open-ended answers against a rubric. Run one case with `uv run python evaluations/run_evals.py --tests energy_cube_time_window`.

## Project layout

```
src/
  agent/          the agent: orchestrator, cube tool, trace events for the UI, memory tools
  api/            live mode for the demo UI (passcode, daily cap, server-sent events)
  demo/           builds the UI's data catalog from the compiled semantic layer
  memory/         the Memory store over Mem0 and Valkey
  config.py       runtime configuration from the environment
  repo_config.py  repo paths and helpers shared by the scripts
scripts/          entry points: ask, data loading, checks, demo recording and live mode
prompts/          the agent's policy: base rules plus one block per domain
wren/             the semantic layer: models, views, cubes, joins, rules, example queries
ui/               the static demo site (data/ holds the recordings and catalog)
evaluations/      the eval suite and its harness
tests/            unit tests
demos/            the CLI presenter script and the UI's curated topics
docs/             diagrams, screenshots and the UI design notes
docker/           the local lakehouse stack
terraform/        AWS resources
```

## Scope and known limitations

A single-user local proof of concept: data reloads are destructive, memory has one fixed user, the agent can run SQL, and secrets live in `.env`. See upstream's [PRODUCTION_CONSIDERATIONS.md](https://github.com/jaehyeon-kim/agentic-analytics-system/blob/main/PRODUCTION_CONSIDERATIONS.md) for what production would take.

Two workarounds for WrenAI 0.15/0.16, both worth retiring when fixed upstream:

- **Cube time windows.** Wren compiles cube date filters to untyped text, which Trino rejects. The agent's `query_cube` ([`src/agent/cube_query.py`](src/agent/cube_query.py)) types those literals before running the query.
- **Row order.** Wren's `run_sql` can return rows out of `ORDER BY` order (the right rows, unsorted). Charts and answers re-sort, so nothing user-facing depends on it.
