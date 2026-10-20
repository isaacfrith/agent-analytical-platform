# Development guide

## Asking questions from the terminal

```bash
make ask q="What share of NSW generation was renewable over the last 30 days?"
```

The terminal shows each tool call, the answer, and the turns and tokens used:

```
Tool #1: get_instructions          ← reads the business rules
Tool #2: describe_cube             ← looks up the renewable_share cube
Tool #3: query_cube                ← asks WrenAI for the number
**Answer: 46.3% of NSW generation was renewable over the last 30 days.**
[budget] turns=3/10 tokens=19088/150000 (within budget)
```

Questions that show the agent's behaviour well:

| Question | What to look for |
| --- | --- |
| What share of NSW generation was renewable over the last 30 days? | One `query_cube` call: a cube does all the work |
| On the highest-price day in the last 30 days, which fuel types generated the most? | A two-step question answered with one SQL query |
| Show me operating coal capacity by state. | "Coal" includes Victoria's brown coal, because the rules say so |
| Show me generation by unit efficiency. | A refusal: there's no efficiency data |
| Show me orders by status. | "status" exists in two tables, so it says which one it used |

**Options:**

```bash
uv run python scripts/ask.py --domain energy "Compare emissions for NSW and WA last week"   # energy rules only
make ask-mem q="Remember: high-value orders means total_amount greater than 1000."          # save a definition
make ask-mem q="Show me high-value orders from last week."                                 # a new agent uses it
make memory-check                                                                          # list saved definitions
```

## The data

**Energy.** Every row is tagged with its market (`network`): the NEM and the WEM are separate markets, so "the NEM" never includes WA. The Northern Territory has no public market data. Data from Open Electricity, licensed [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) (non-commercial use).

| Table | Rows | One row per… |
| --- | --- | --- |
| `facilities` | ~1,140 | Generating unit: fuel, status, registered capacity (MW), location |
| `generation_daily` | ~5,940 | Region, fuel and day: energy (MWh), emissions (tCO2e), market value (AUD) |
| `market_daily` | 540 | Region and day (6 regions): spot price (AUD/MWh), demand |
| `fueltech_groups` | 29 | Fuel type: its group (coal, gas, solar…) and whether it counts as renewable |

Cubes: `daily_generation`, `daily_price` and `renewable_share`.

**Ecommerce.** Load with `make data pipeline`. Generated customers, products, 30,000 orders, order items, payments and returns. Cubes: `daily_revenue`, `product_performance` and `refunds_cube`.

## Quality checks

| Command | What it checks | Uses AI? |
| --- | --- | --- |
| `make test` | Unit tests | No |
| `make lint` | Code style (ruff) | No |
| `make mdl-test` | 24 checks on the semantic layer: tables, joins, cubes, example queries | No |
| `make energy-test` | 15 checks on the energy data: coverage, duplicates, markets, locations | No |
| `make evals` | 16 questions against the real agent, graded automatically (30–45 minutes) | Yes |

The eval cases are in [`evaluations/golden_suite.json`](../evaluations/golden_suite.json): cube routing, harder data-model questions, refusals and the memory lifecycle. Run one with `uv run python evaluations/run_evals.py --tests energy_cube_time_window`.

## Project layout

```
wren/             the WrenAI semantic layer: models, views, cubes, joins, rules, example queries
prompts/          the agent's instructions: shared rules plus one file per dataset
src/
  agent/          the agent: orchestrator, cube tool, trace events for the UI, memory tools
  api/            live questions (bot check, limits, streaming)
  demo/           builds the data catalog from the semantic layer
  memory/         saved user definitions (Mem0 over Valkey)
scripts/          entry points: ask, load data, checks, record and serve the demo
  hosted/         the live server's boot sequence and watchdog
ui/               the demo website (data/ holds the recordings and the catalog)
demos/            the suggested questions, topics and a terminal presenter script
evaluations/      eval questions and the harness that grades them
tests/            unit tests
docker/           the local services
terraform/        AWS: bucket and IAM; the live server, wake Lambda and budget
docs/             screenshots and the UI's design notes
```
