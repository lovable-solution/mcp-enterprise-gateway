# mcp-enterprise-gateway: developer entry points. `make` lists the targets.
#
# Paths stay quoted everywhere: checkouts under directories with spaces are common.

SHELL         := bash
.SHELLFLAGS   := -euo pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS     += --no-print-directory

COMPOSE  ?= docker compose
UV_RUN   ?= uv run --frozen
POLICY   ?= config/policies.yaml
SCHEMA   ?= config/policies.schema.json
TF_ENV   ?= dev
TF_IMAGE ?= hashicorp/terraform:1.17.2

# Prefer a local terraform binary; otherwise run the pinned image as the current user.
ifeq ($(shell command -v terraform 2>/dev/null),)
TERRAFORM = docker run --rm --user "$$(id -u):$$(id -g)" \
	-e HOME=/tmp -e CHECKPOINT_DISABLE=1 -e TF_IN_AUTOMATION=1 \
	-v "$(CURDIR)/infra/terraform:/workspace" -w /workspace $(TF_IMAGE)
else
TERRAFORM = cd infra/terraform && terraform
endif

ROLE   ?= analyst
TENANT ?= acme

.PHONY: help env config build up down reset ps logs token sync lint fmt test test-unit \
	test-integration policy-check policy-matrix schema tf-fmt tf-validate verify

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} \
		/^##@/ {printf "\n%s\n", substr($$0, 5)} \
		/^[a-zA-Z0-9_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

##@ Local stack
env: ## Create or update .env with generated secrets (never changes existing values)
	@./scripts/bootstrap-env.sh

config: env ## Validate docker-compose.yml with .env interpolated
	@$(COMPOSE) config --quiet && echo "docker compose config: OK"

build: ## Build the gateway and crm-ops images
	$(COMPOSE) build gateway crm-ops

up: env ## Start the whole stack (builds images if needed) and wait until healthy
	$(COMPOSE) up --detach --wait --build

down: ## Stop the local stack (keeps data volumes)
	$(COMPOSE) down

reset: ## Stop the local stack and DELETE its data volumes
	$(COMPOSE) down --volumes --remove-orphans

ps: ## Show service status
	@$(COMPOSE) ps

logs: ## Follow logs (SERVICE=<name> to filter)
	$(COMPOSE) logs --follow --tail=100 $(SERVICE)

token: ## Mint a local dev JWT for the gateway (ROLE=analyst TENANT=acme)
	@$(UV_RUN) mcp-gateway-token --role "$(ROLE)" --tenant "$(TENANT)"

##@ Python
sync: ## Install the Python workspace exactly as locked
	uv sync --all-packages --frozen

lint: ## Ruff (lint + format check) and mypy --strict
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format --check .
	$(UV_RUN) mypy

fmt: ## Apply ruff formatting and safe autofixes
	$(UV_RUN) ruff format .
	$(UV_RUN) ruff check --fix .

test: ## Run every test (integration tests need `make up`)
	$(UV_RUN) pytest

test-unit: ## Run the tests that need no running services
	$(UV_RUN) pytest -m "not integration"

test-integration: ## Run the guardrail suite against Postgres + Redis (`make up` first)
	$(UV_RUN) pytest -m integration

##@ Policy
policy-check: ## Validate the policy and check its JSON Schema is current
	$(UV_RUN) mcp-gateway-policy validate "$(POLICY)"
	$(UV_RUN) mcp-gateway-policy schema --output "$(SCHEMA)" --check

policy-matrix: ## Print effective role x tool and role x action tables
	@$(UV_RUN) mcp-gateway-policy matrix "$(POLICY)"

schema: ## Regenerate the policy JSON Schema from the Pydantic models
	$(UV_RUN) mcp-gateway-policy schema --output "$(SCHEMA)"

##@ Terraform
tf-fmt: ## terraform fmt -check across every module
	$(TERRAFORM) fmt -check -recursive -diff

tf-validate: ## terraform init (no backend) + validate (downloads providers, ~800 MB)
	$(TERRAFORM) -chdir=environments/$(TF_ENV) init -backend=false -input=false
	$(TERRAFORM) -chdir=environments/$(TF_ENV) validate

##@ Gates
verify: config policy-check lint test tf-fmt tf-validate ## Everything CI runs
	@echo "verify: all checks passed"
