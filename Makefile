# Agent Analytical Platform — infra and tooling targets.
# Recipes need real tabs (Make syntax); VS Code's Makefile mode handles it.

COMPOSE := docker compose -f docker/compose.yaml

.PHONY: preflight infra-init infra-plan infra-apply infra-destroy \
        up down logs ps bootstrap

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
