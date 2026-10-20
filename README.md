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
- [Live questions: hosted and local](#live-questions-hosted-and-local)
- [How it works](#how-it-works) · [The data](#the-data) · [Quality checks](#quality-checks) · [Project layout](#project-layout) · [Limitations](#scope-and-limitations)

## Using the demo

Open the [live demo](https://isaacfrith.github.io/agent-analytical-platform/). It has two tabs.

### The "Ask" tab

1. **Click a suggested question** in the middle panel. Three suggestions rotate there while you look, drawn from 24 recorded questions (one tagged *Beyond the data* shows the agent refusing). Each replays a real recorded run of the agent: free, instant and identical every time. After an answer, **Ask next** offers related questions.
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

The box at the bottom of the middle panel runs the real agent on your own question. The server behind it **sleeps when nobody's asking**: if it's asleep, click **Start live questions** and wait about two minutes while it starts the whole lakehouse, then ask. Each visitor gets **2 live questions a day**, after an invisible bot check, and there's a cap on the total per day. When live questions aren't available the box says why; the suggested questions always work.

## Choose your path

| I want to… | What I need | Commands |
| --- | --- | --- |
| **Look at the demo** | A browser | Open the [live demo](https://isaacfrith.github.io/agent-analytical-platform/) |
| **Run the demo site on my machine** | This repo and Python 3 | `make demo-serve`, then open http://localhost:8765 |
| **Ask the agent my own questions** | The full [setup](#setup) | `make ask q="…"` |
| **Re-record the demo with fresh data** | The full setup | [Updating and publishing the demo](#updating-and-publishing-the-demo) |
| **Run the always-on live server** | The full setup, admin AWS credentials | [Live questions: hosted and local](#live-questions-hosted-and-local) |

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
make profile                      # one-time: tells WrenAI how to reach the local Trino
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
2. **Re-record the demo:** `make demo-record`. This re-runs all 24 suggested questions through the real agent (about 15 minutes; roughly a million tokens, about a dollar on Haiku 4.5) and checks each one still gets the expected kind of answer: a question marked `refused` must be refused, every other one must be answered. It also rebuilds the map data and the "What's in the data" catalog. Each recording shows a "Recorded on…" date in the UI.
   - Only some questions: `uv run python scripts/demo_record.py --only price_by_region,wa_vs_nem`
   - Only the data tab (free, no AI): `uv run python scripts/demo_record.py --catalog-only`
3. **Check it locally:** `make demo-serve`, then open http://localhost:8765. Always use this: opening `ui/index.html` straight from disk won't work, because browsers block it from loading its data files.
4. **Publish:** commit `ui/data/` and push to `main`.

To change the suggested questions, edit [`demos/energy-questions.yml`](demos/energy-questions.yml): each has an id, a topic and an expected outcome, and they also become the example questions on the data tab. To change the topics and limits on the data tab, edit [`demos/energy-topics.yml`](demos/energy-topics.yml). Recording fails if either file names something that doesn't exist.

## Live questions: hosted and local

### How the hosted server works

The website is static (GitHub Pages) and can't run the agent, so live questions run on **one AWS server that starts on demand**: the same Docker stack as your laptop (Trino, Iceberg, Lakekeeper, Postgres, Valkey) plus WrenAI and the agent, in Sydney next to the S3 data.

- A visitor clicks **Start live questions**. After a bot check, a small Lambda function starts the server. On every start the server pulls the latest `main`, starts the stack, and opens an outbound Cloudflare tunnel; the website connects through it once the agent answers.
- The server **stops itself** after 20 minutes without a question, and runs at most 3 hours a day.
- Its data lives under `hosted/` in the bucket, apart from your laptop's, and refreshes itself when it's more than 3 days old.

**Limits** (all enforced server-side): 2 live questions per visitor per day, 20 a day in total, 30 a day of your own with the [owner pass](#your-own-allowance-the-owner-pass), one at a time, 6 wakes a day, 3 hours of running a day. **Backstop:** an AWS budget of $20 a month for the whole account emails you at 50% and 80%, and at 100% stops the server and blocks the wake function. **Typical cost:** a few dollars a month (the server runs only when used, plus about $3 a month of disk).

**Security:** no inbound ports and no SSH (Session Manager for emergencies); no stored AWS keys (an IAM role limited to the `hosted/` prefix, the one Bedrock model and its own settings); secrets in SSM Parameter Store, never in the repo or Terraform state; the agent is read-only.

### Deploy it (once)

You need admin AWS credentials for Terraform (the app user can't create these resources), a free Cloudflare Turnstile widget for your site's domain, and your Open Electricity key.

1. **Create the resources:** `make infra-init infra-plan`, check the plan (28 to add: the server, its role and firewall, the wake Lambda, the budget and its actions, the settings; 1 to change: the vending role now also trusts the server), then `make infra-apply`.
2. **Set the two secrets** straight away (Terraform prints these commands as `live_set_secrets`):
   ```bash
   aws ssm put-parameter --region ap-southeast-2 --overwrite --type SecureString --name /agent-analytics/live/turnstile-secret --value 'YOUR_TURNSTILE_SECRET'
   aws ssm put-parameter --region ap-southeast-2 --overwrite --type SecureString --name /agent-analytics/live/openelectricity-key --value 'YOUR_OPENELECTRICITY_KEY'
   ```
3. **Point the site at it:** `make live-config`, then commit `ui/live-config.json` and push. The first boot installs everything and loads the data (about 15 minutes); after that, starts take about 2 minutes.

### Your own allowance: the owner pass

The owner pass gives you **30 live questions a day of your own**, on the public site, without using up visitors' 20. Everything else still applies to you: the bot check, one question at a time, 6 wakes and 3 hours of running a day, and the budget.

1. **Create the pass** (Terraform prints these commands as `live_owner_pass`). The first command stores a random pass; the second prints your private link:
   ```bash
   aws ssm put-parameter --region ap-southeast-2 --overwrite --type SecureString --name /agent-analytics/live/owner-pass --value "$(openssl rand -hex 24)"
   echo "https://isaacfrith.github.io/agent-analytical-platform/?owner=$(aws ssm get-parameter --region ap-southeast-2 --with-decryption --name /agent-analytics/live/owner-pass --query Parameter.Value --output text)"
   ```
2. **Open the link once** in each browser you'll use. The page keeps the pass in that browser and removes it from the address bar straight away; the box then shows **Owner · N of 30 left today**. **Forget owner pass** removes it from that browser.

The server reads the pass when it starts, so if it's running, a new pass works from its next start. To change the pass, run step 1 again: from the server's next start, browsers holding the old pass are refused and need the new link. Keep the link private: it's the only thing that unlocks your allowance. Locally, set `DEMO_OWNER_PASS` (16+ characters) in `.env`.

### Run it

| I want to… | Do this |
| --- | --- |
| Turn live questions off now | `aws ssm put-parameter --region ap-southeast-2 --overwrite --type String --name /agent-analytics/live/switch --value off` (`on` to undo) |
| See what the server is doing | AWS console → Systems Manager → Session Manager → connect, then `journalctl -u live -f` |
| Recover after the budget action fired | Check the spend, then AWS console → Budgets → `agent-analytics-monthly` → Actions → reverse both |
| Remove it all | `make infra-destroy` (also deletes the bucket) |

### Test live questions on your laptop

Run the live API locally and point the local site at it. Local mode skips the bot check; the limits still apply (counted in your local Valkey).

```bash
make up            # the local services
make demo-live     # terminal 1: the live API; it prints a link
make demo-serve    # terminal 2: the website
```

Open the link `make demo-live` prints (`http://localhost:8765/?live=…`). Every question costs tokens on your AWS account (a few cents each on Haiku 4.5). Settings live in `.env` (see `.env.example`): `DEMO_DAILY_CAP`, `DEMO_PER_VISITOR_CAP`, `DEMO_OWNER_PASS` and `DEMO_OWNER_CAP`, and `DEMO_ENABLED=false` to switch live questions off.

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
  api/            live questions for the demo (bot check, limits, streaming)
  demo/           builds the demo's data catalog from the semantic layer
  memory/         saved user definitions (Mem0 over Valkey)
  config.py       settings from the environment
  repo_config.py  repo paths and helpers shared by the scripts
scripts/          command-line entry points: ask, load data, checks, record and serve the demo
  hosted/         the live server's boot sequence and idle/runtime watchdog
prompts/          the agent's instructions: shared rules plus one file per dataset
wren/             the semantic layer: tables, views, metrics, joins, rules, example queries
ui/               the demo website (data/ holds the recordings and the catalog)
evaluations/      the eval questions and the harness that grades them
tests/            unit tests
demos/            the demo's suggested questions and topics, and a terminal presenter script
docs/             diagrams, screenshots and the demo's design notes
docker/           the local services
terraform/        the AWS resources: bucket and IAM; the live server, wake Lambda and budget
```

## Scope and limitations

This is a single-user proof of concept, not a production system. Data reloads replace everything, saved definitions belong to one user, the agent can run SQL (read-only), and secrets live in `.env`. Upstream's [PRODUCTION_CONSIDERATIONS.md](https://github.com/jaehyeon-kim/agentic-analytics-system/blob/main/PRODUCTION_CONSIDERATIONS.md) covers what production would need.

Two workarounds for bugs in WrenAI 0.15 and 0.16, to remove once they're fixed upstream:

- **Date filters on metrics.** WrenAI writes cube date filters as plain text, which Trino rejects. The agent's own `query_cube` tool ([`src/agent/cube_query.py`](src/agent/cube_query.py)) fixes the dates before the query runs.
- **Row order.** WrenAI's `run_sql` can return the right rows in the wrong order. Charts and answers sort their rows, so you won't see it in the demo.

Inspired by [jaehyeon-kim/agentic-analytics-system](https://github.com/jaehyeon-kim/agentic-analytics-system), adapted for AWS (S3, Bedrock and Terraform).
