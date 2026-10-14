# Agent Analytical Platform — infra and tooling targets.
# Recipes need real tabs (Make syntax); VS Code's Makefile mode handles it.

COMPOSE := docker compose -f docker/compose.yaml

.PHONY: preflight infra-init infra-plan infra-apply infra-destroy \
        up down logs ps bootstrap data pipeline mdl memory wren-query ask ask-mem memory-check

## preflight — verify AWS access as the app user (identity, bucket, Bedrock)
preflight:
	uv run python scripts/aws_preflight.py

## infra-* — Terraform lifecycle. Runs with ADMIN credentials (default chain).
infra-init:
	cd terraform && terraform init
infra-plan:
	cd terraform && terraform plan
infra-apply:
	cd terraform && terraform apply
infra-destroy:
	cd terraform && terraform destroy # wipes bucket contents (force_destroy)

## up/down/logs/ps — the local lakehouse stack (postgres, lakekeeper, trino, valkey)
up:
	$(COMPOSE) up -d --wait
down:
	$(COMPOSE) down # keeps the postgres volume — bootstrap survives restarts
logs:
	$(COMPOSE) logs -f
ps:
	$(COMPOSE) ps

## bootstrap — one-time-per-install: accept terms, create the S3-backed warehouse
bootstrap:
	uv run python scripts/bootstrap_catalog.py

## data/pipeline — Phase 2: Faker → landing Parquet → Iceberg tables
data:
	uv run python scripts/generate_data.py
pipeline:
	uv run python scripts/run_pipeline.py

## mdl/memory/wren-query — Phase 3: compile MDL, index memory, query the layer
mdl:
	cd wren && uv run wren context build
memory:
	cd wren && uv run wren memory index
wren-query:
	cd wren && uv run wren --sql "$(q)"

## ask — Phase 4: ask the analytics agent (fresh agent per question)
ask:
	uv run python scripts/ask.py "$(q)"

## ask-mem/memory-check — Phase 5: memory-enabled agent, direct store reads
ask-mem:
	uv run python scripts/ask.py --use-memory "$(q)"
memory-check:
	PYTHONPATH=src uv run python -c "from agent.memory import debug_dump; debug_dump()"
