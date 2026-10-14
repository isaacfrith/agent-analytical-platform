# Agent Analytical Platform

Conversational analytics over an Iceberg lakehouse: a Strands agent answers data questions through a governed WrenAI semantic layer, with Trino execution and Mem0 long-term memory. Inspired by [jaehyeon-kim/agentic-analytics-system](https://github.com/jaehyeon-kim/agentic-analytics-system), adapted for AWS (S3 + Bedrock + Terraform).

The LLM never sees raw tables. It reasons over governed models, cubes, and business rules; WrenAI compiles the SQL deterministically.

## Architecture

| Component | Technology | Role |
| --- | --- | --- |
| Agent | Strands SDK (Bedrock) | Interprets questions, plans tool calls, one fresh agent per question |
| Semantic layer | WrenAI (MDL) | Governed models, relationships, cubes, business rules; exposes MCP tools |
| Execution | Trino + Iceberg (Lakekeeper catalog, S3 storage) | Physical query engine over the lakehouse |
| Memory | Mem0 over Valkey | User preferences persist across sessions |
| Infra | Terraform (AWS) + Docker Compose (local) | Bucket/IAM provisioned; Postgres, Lakekeeper, Trino, Valkey local |

## Prerequisites

- [uv](https://docs.astral.sh/uv/) with Python 3.12+
- Docker with compose support
- AWS credentials under a profile (`~/.aws/credentials`), with Bedrock + S3 access

## Setup

```bash
cp .env.example .env   # fill in profile, region, bucket, Bedrock model ID
make preflight         # verify AWS identity, bucket, Bedrock access
make infra-init infra-apply   # provision S3 bucket + IAM (Terraform)
make up                # start Postgres, Lakekeeper, Trino, Valkey
make bootstrap         # create the S3-backed warehouse
make data pipeline     # generate Faker data -> landing Parquet -> Iceberg tables
make mdl memory        # compile MDL, index semantic memory
```

## Usage

```bash
make ask q="What was daily gross revenue over the last 7 days?"
make ask-mem q="Show me high-value orders from last week."   # memory-enabled
make memory-check      # dump stored preferences
make wren-query q="SELECT COUNT(*) FROM orders"              # query WrenAI directly
```

Teach it a rule (rebuilds + reindexes in one step):

```bash
make refresh rule="When asked for refunds by status, always use return_status from returned_orders." title="Refunds by Status"
```

Check the layer without the agent (fast, deterministic, no LLM):

```bash
make mdl-test
```

Run the eval suite (8 cases: cube routing, ambiguity, refusals, memory lifecycle):

```bash
make evals
```

## Layout

- `src/agent/` — orchestrator (system prompt, budgets) and memory tools
- `scripts/` — `ask.py`, pipeline, bootstrap, `refresh_semantics.py`, `test_semantics.py`
- `wren/` — MDL project: `models/`, `cubes/`, `relationships.yml`, `knowledge/` (rules, seed SQL)
- `evaluations/` — golden suite + harness (`run_evals.py`)
- `terraform/` — S3 bucket, IAM roles
- `docker/` — local lakehouse stack

## Scope

Single-user local proof of concept: destructive data reloads, one fixed memory user, agent holds `run_sql`, secrets in `.env`. See upstream's [PRODUCTION_CONSIDERATIONS.md](https://github.com/jaehyeon-kim/agentic-analytics-system/blob/main/PRODUCTION_CONSIDERATIONS.md) for what production would take.
