# Agent Analytical Platform. Run `make help` for the list of targets.
# Recipes need real tabs (Make syntax).

COMPOSE := docker compose -f docker/compose.yaml
.DEFAULT_GOAL := help

.PHONY: help preflight infra-init infra-plan infra-apply infra-destroy up down logs ps \
        bootstrap data pipeline energy-data energy-test mdl memory mdl-test refresh wren-query \
        ask ask-mem memory-check memory-wipe test test-integration lint evals \
        demo-record demo-serve demo-live

help: ## Show this list
	@awk 'BEGIN {FS = ":.*## "} /^## / {printf "\n\033[1m%s\033[0m\n", substr($$0, 4)} /^[a-z-]+:.*## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

## Infrastructure
preflight: ## Check AWS access as the app user (identity, bucket, Bedrock)
	uv run python scripts/aws_preflight.py
infra-init: ## Terraform init (admin credentials)
	cd terraform && terraform init
infra-plan: ## Terraform plan
	cd terraform && terraform plan
infra-apply: ## Create the S3 bucket and IAM roles
	cd terraform && terraform apply
infra-destroy: ## Destroy AWS resources, including bucket contents
	cd terraform && terraform destroy
up: ## Start the local stack (Postgres, Lakekeeper, Trino, Valkey)
	$(COMPOSE) up -d --wait
down: ## Stop the local stack (keeps the Postgres volume)
	$(COMPOSE) down
logs: ## Follow the local stack's logs
	$(COMPOSE) logs -f
ps: ## Show the local stack's containers
	$(COMPOSE) ps
bootstrap: ## One-time: create the S3-backed Iceberg warehouse
	uv run python scripts/bootstrap_catalog.py

## Data
data: ## Ecommerce: generate fake data into landing Parquet
	uv run python scripts/generate_data.py
pipeline: ## Ecommerce: load landing Parquet into Iceberg
	uv run python scripts/run_pipeline.py
energy-data: ## Energy: fetch 90 days (NEM + WA) from Open Electricity into Iceberg
	uv run python scripts/fetch_energy_data.py
energy-test: ## Energy: data checks against Trino (no LLM)
	uv run python scripts/check_energy_data.py

## Semantic layer
mdl: ## Compile the Wren model (MDL)
	cd wren && uv run wren context build
memory: ## Index the model and example queries for retrieval
	cd wren && uv run wren memory index
mdl-test: ## Check the semantic layer end to end (no LLM)
	uv run python scripts/check_semantics.py
refresh: ## Add a business rule, rebuild and reindex: rule="..." title="..."
	uv run python scripts/refresh_semantics.py --rule "$(rule)" --title "$(title)"
wren-query: ## Run SQL through the semantic layer: q="SELECT ..."
	cd wren && uv run wren --sql "$(q)"

## Agent
ask: ## Ask a question: q="..."
	uv run python scripts/ask.py "$(q)"
ask-mem: ## Ask with saved preferences enabled: q="..."
	uv run python scripts/ask.py --use-memory "$(q)"
memory-check: ## List saved preferences
	PYTHONPATH=src uv run python -m memory check
memory-wipe: ## Delete every saved preference (destructive)
	PYTHONPATH=src uv run python -m memory wipe

## Quality
test: ## Unit tests
	uv run pytest
test-integration: ## Integration tests (needs `make up` and AWS credentials)
	uv run pytest -m integration -v
lint: ## Lint and format check
	uv run ruff check src scripts tests evaluations
	uv run ruff format --check src scripts tests evaluations
evals: ## Full eval suite against the real agent (~30-45 min, costs Bedrock tokens)
	uv run python evaluations/run_evals.py

## Demo UI
demo-record: ## Re-record the demo questions, map and data catalog (~$1 of Bedrock)
	uv run python scripts/demo_record.py
demo-serve: ## Serve the UI at http://localhost:8765
	@echo "http://localhost:8765"
	cd ui && python3 -m http.server 8765
demo-live: ## Live questions from this laptop via a Cloudflare quick tunnel
	uv run python scripts/demo_live.py
