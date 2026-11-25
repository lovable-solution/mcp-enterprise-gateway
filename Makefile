# mcp-enterprise-gateway: developer entry points. `make` lists the targets.
#
# Paths stay quoted everywhere: checkouts under directories with spaces are common.

SHELL         := bash
.SHELLFLAGS   := -euo pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS     += --no-print-directory

COMPOSE  ?= docker compose
PIP_AUDIT ?= uvx pip-audit@2.10.1
UV_RUN   ?= uv run --frozen
POLICY   ?= config/policies.yaml
SCHEMA   ?= config/policies.schema.json
TF_ENV   ?= dev
TF_IMAGE ?= hashicorp/terraform:1.17.2

# Prefer a local terraform binary; otherwise run the pinned image as the current user.
# Providers are downloaded once into a shared plugin cache (git-ignored) and linked
# into every working directory: the root module and each module's tests.
ifeq ($(shell command -v terraform 2>/dev/null),)
TERRAFORM = docker run --rm --user "$$(id -u):$$(id -g)" \
	-e HOME=/tmp -e CHECKPOINT_DISABLE=1 -e TF_IN_AUTOMATION=1 \
	-e TF_PLUGIN_CACHE_DIR=/workspace/.terraform-plugin-cache \
	-v "$(CURDIR)/infra/terraform:/workspace" -w /workspace $(TF_IMAGE)
else
TERRAFORM = cd infra/terraform && TF_PLUGIN_CACHE_DIR="$(CURDIR)/infra/terraform/.terraform-plugin-cache" terraform
endif

TF_MODULES = vpc secrets_manager rds_postgres elasticache_redis ecs_fargate

ROLE   ?= analyst
TENANT ?= acme

.PHONY: help env config build up down reset ps logs token sync lint fmt test test-unit \
	test-integration test-live ui-install ui-check ui-dev agent-run agent-resume \
	policy-check policy-matrix schema tf-fmt tf-validate tf-test verify audit

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} \
		/^##@/ {printf "\n%s\n", substr($$0, 5)} \
		/^[a-zA-Z0-9_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

##@ Local stack
env: ## Create or update .env with generated secrets (never changes existing values)
	@./scripts/bootstrap-env.sh

config: env ## Validate docker-compose.yml with .env interpolated
	@$(COMPOSE) config --quiet && echo "docker compose config: OK"

build: ## Build every image (gateway, crm-ops, operator-ui, agent-runtime)
	$(COMPOSE) --profile agent build

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

test-live: ## Run the agent against the real Claude API (costs money; needs ANTHROPIC_API_KEY)
	$(UV_RUN) pytest -m live -s agent-runtime/tests

# Accepted advisories (no fixed release exists; re-check whenever the lock changes):
#  PYSEC-2026-4298  python-jose accepts a DER-encoded public key as an HMAC secret
#                   (alg confusion). Not reachable: the gateway checks `alg` against a
#                   single-family allow-list before choosing a key, never mixes HS* with
#                   asymmetric algorithms, and passes JWKS keys as JWK dicts
#                   (test_der_encoded_public_keys_cannot_forge_hs256_tokens).
#  PYSEC-2026-1325  ecdsa (Minerva timing leak when *signing*). Pulled in by python-jose,
#                   never used: jose runs on its cryptography backends, and the gateway
#                   only verifies (test_jose_uses_the_cryptography_backends).
audit: ## Scan the locked Python dependencies for known vulnerabilities (pip-audit)
	uv export --frozen --all-packages --no-emit-workspace --quiet \
		| $(PIP_AUDIT) --disable-pip --require-hashes --progress-spinner off -r /dev/stdin \
			--ignore-vuln PYSEC-2026-4298 --ignore-vuln PYSEC-2026-1325

##@ Operator console (operator-ui)
ui-install: ## Install the console's dependencies exactly as locked
	cd operator-ui && pnpm install --frozen-lockfile

ui-check: ## Console: ESLint, TypeScript and unit tests
	cd operator-ui && pnpm lint && pnpm typecheck && pnpm test

ui-dev: ## Run the console with hot reload on :3000 against the local gateway
	cd operator-ui && GATEWAY_URL=http://localhost:8000 DEV_SIGNIN=true COOKIE_SECURE=false \
		SESSION_SECRET="$$(grep '^CONSOLE_SESSION_SECRET=' ../.env | cut -d= -f2-)" \
		GATEWAY_JWT_SECRET="$$(grep '^GATEWAY_JWT_SECRET=' ../.env | cut -d= -f2-)" \
		pnpm dev

##@ Agent runtime
# The prompt is passed verbatim: $(value) stops make expanding "$600" and the single
# quotes stop the shell doing so (embedded single quotes are escaped).
agent-run: ## Start an agent thread in the stack: make agent-run PROMPT='...'
	@test -n '$(subst ','\'',$(value PROMPT))' || { echo "usage: make agent-run PROMPT='...'"; exit 2; }
	$(COMPOSE) --profile agent run --rm agent-runtime run '$(subst ','\'',$(value PROMPT))'

agent-resume: ## Resume a paused thread after a decision: make agent-resume THREAD=t-...
	@test -n "$(THREAD)" || { echo 'usage: make agent-resume THREAD=t-...'; exit 2; }
	$(COMPOSE) --profile agent run --rm agent-runtime resume "$(THREAD)" --wait

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

tf-validate: ## terraform init (no backend) + validate (downloads providers once, ~800 MB)
	$(TERRAFORM) -chdir=environments/$(TF_ENV) init -backend=false -input=false
	$(TERRAFORM) -chdir=environments/$(TF_ENV) validate

# Modules are tested with the environment's locked provider versions (copied lock file).
tf-test: tf-validate ## terraform test: every module + the environment, mocked providers (no AWS account)
	@for module in $(TF_MODULES); do \
		cp "infra/terraform/environments/$(TF_ENV)/.terraform.lock.hcl" "infra/terraform/modules/$$module/.terraform.lock.hcl"; \
		echo "== modules/$$module"; \
		$(TERRAFORM) -chdir=modules/$$module init -backend=false -input=false >/dev/null; \
		$(TERRAFORM) -chdir=modules/$$module test -no-color | grep -E '^Success|^Failure|^  run|Error' ; \
		test "$${PIPESTATUS[0]}" -eq 0 || exit 1; \
	done
	@echo "== environments/$(TF_ENV)"
	@$(TERRAFORM) -chdir=environments/$(TF_ENV) test -no-color | grep -E '^Success|^Failure|^  run|Error'; test "$${PIPESTATUS[0]}" -eq 0

##@ Gates
verify: config policy-check lint test ui-check tf-fmt tf-test ## Everything CI runs (tf-test includes tf-validate)
	@echo "verify: all checks passed"
