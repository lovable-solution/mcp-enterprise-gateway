# MCP Enterprise Gateway & Governed Agent Runtime

A policy enforcement point between autonomous AI agents and internal Model Context
Protocol (MCP) servers. Agents connect to the gateway and nowhere else. The gateway:
- authenticates them (JWT);
- authorises every tool call against a declarative policy (RBAC);
- enforces per-session budgets and loop detection (Redis);
- suspends high-consequence calls for human approval (PostgreSQL);
- traces all of it (OpenTelemetry).

> **Status: Phase 2 of 4.** The gateway runtime and the crm-ops MCP server are done,
> together with the guardrail test suite. Still to come: the agent runtime and operator
> console (Phase 3), then the Terraform implementation (Phase 4). See the [build plan](#build-plan).

## Repository layout

```
mcp-enterprise-gateway/
├── config/policies.yaml         governance policy (+ generated policies.schema.json)
├── gateway/                     FastAPI + MCP SDK: auth, RBAC, budgets, breaker, HITL, API
├── mcp-servers/crm-ops/         sample MCP server (MCPServer): 3 CRM tools, tenant-scoped
├── libs/platform-telemetry/     shared OpenTelemetry bootstrap
├── agent-runtime/               LangGraph + PydanticAI orchestrator              (Phase 3)
├── operator-ui/                 Next.js 15 operator console                      (Phase 3)
├── infra/local/                 compose support: OTel Collector, Prometheus, Postgres init
├── infra/terraform/             AWS modules + dev environment (interfaces; Phase 4 resources)
├── docker-compose.yml           gateway, crm-ops, Postgres, Redis, OTel Collector, Jaeger, Prometheus
└── Makefile                     `make` lists every target
```

## Quick start

Requires Docker with Compose v2, [uv](https://docs.astral.sh/uv/) >= 0.9 and GNU Make.

```bash
make env       # .env with generated secrets (git-ignored, mode 600)
make up        # build and start the whole stack; waits until it is healthy
make sync      # Python workspace (CPython 3.12)
make verify    # compose config, policy, lint, mypy --strict, all tests, terraform fmt + validate
```

| Service | Address (127.0.0.1 only) |
| --- | --- |
| Gateway: MCP endpoint / API docs | http://localhost:8000/mcp, http://localhost:8000/docs |
| Jaeger / Prometheus | http://localhost:16686, http://localhost:9090 |
| PostgreSQL / Redis | localhost:15432, localhost:16379 |

crm-ops publishes no port: it is reachable from the gateway only.

## Try it

```bash
AGENT=$(make -s token ROLE=admin)                       # an agent session token
OPS=$(uv run mcp-gateway-token --role operator --no-session)
```

Point any MCP client at `http://localhost:8000/mcp` with `Authorization: Bearer $AGENT`.

1. The `analyst` role sees only the two read tools; `admin` also sees
   `crm-ops__update_credit_limit`.
2. Call `crm-ops__update_credit_limit`. The call is held, and
   `curl -H "Authorization: Bearer $OPS" localhost:8000/v1/approvals` shows it pending.
3. Approve it:

   ```bash
   curl -X POST -H "Authorization: Bearer $OPS" -H 'Content-Type: application/json' \
     -d '{"decision":"approve"}' localhost:8000/v1/approvals/<id>/decide
   ```

   The held call returns the real result.
4. Repeat any identical call four times within 60 s. The session's breaker trips and
   every further call is refused until an operator resets it.

## The policy

`config/policies.yaml` is the single source of truth for governance (`make policy-matrix`):

```
tool                          access  risk  analyst  admin  operator  auditor
crm-ops__search_accounts      read    low   ALLOW    ALLOW  -         -
crm-ops__get_account_metrics  read    low   ALLOW    ALLOW  -         -
crm-ops__update_credit_limit  write   high  -        HITL   -         -
```

The loader rejects the whole file, and the gateway will not start, if any invariant is
broken:
- **Closed world:** unknown keys and duplicate YAML keys are errors.
- **Strict types:** nothing is coerced, and money is exact `Decimal`.
- **Default deny:** only registered tools exist for agents.
- **Deny wins:** `deny` beats `allow`, even through inheritance.
- **Checked references:** grant patterns and approver roles must resolve.
- **Segregation of duties:** a role that can call an approval-gated tool can never
  approve it.
- **Timing and pricing:** approval timings must be consistent, and fallback pricing
  conservative.

## Guardrail tests

`make test` runs 174 tests in about 20 s. The 36 integration tests run against real
Postgres and Redis (`make up`), real gateway and crm-ops servers, and the official MCP
client:

| Suite | Proves |
| --- | --- |
| `test_rbac.py` | (a) unauthorised tools are denied before anything reaches upstream; tools/list filtered per role; tenant isolation; bad tokens (expired, forged, `alg=none`, wrong audience) rejected |
| `test_circuit_breaker.py` | (b) the 4th identical call in 60 s trips the session breaker; per-session; denied-tool hammering trips too; operator-only reset; signed webhook |
| `test_hitl.py` | (c) pause -> approve -> resume inline; checkpoint + resume after the inline wait; reject; de-duplication (executed once); four-eyes and tenant guards; expiry; database-enforced invariants |
| `test_budgets.py` | tool-call and USD caps; exact per-token-type pricing; conservative fallback; metered tools |
| `test_events_and_tracing.py` | SSE with `Last-Event-ID` resume; one trace spanning agent -> gateway -> crm-ops |

## Design decisions

| Decision | Why |
| --- | --- |
| The policy's tool registry is authoritative; upstream MCP annotations are advisory | A careless or compromised MCP server cannot reclassify its own write tool as read-only. |
| HITL: bounded inline wait, then `PENDING_APPROVAL` + agent checkpoint | Approvals take minutes; holding HTTP requests that long breaks behind load balancers. |
| The gateway executes the approved payload itself, once | The stored request is immutable, so arguments cannot change between review and execution. |
| Agents report model usage to the gateway (`POST /v1/sessions/{sid}/usage`) | The gateway prices it and enforces the cap on every tool call. LLM-side enforcement relies on the agent runtime reporting faithfully; routing model calls through the gateway would make it authoritative. |
| Stateless MCP transport on both hops | Any replica serves any request; no session affinity behind the ALB. |
| Upstreams receive signed, short-lived identity assertions, never agent tokens | MCP servers trust the gateway's authorisation and can scope data to the asserted tenant. |
| Redis Lua scripts for every check-and-update; server time; `noeviction` | Atomic across replicas; an evicted key cannot silently reset a budget. |
| Events on Redis Streams | SSE clients resume with `Last-Event-ID`, and waiters cannot miss a decision. |
| Separate Postgres database and role per service | The agent runtime can never write to the approvals table. |
| No default credentials anywhere | Compose refuses to start without generated secrets; in AWS the IdP's JWKS, RDS-managed credentials and write-only secrets replace them. |

## Build plan

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Repo structure, compose stack, policy schema + engine, Terraform interfaces | done |
| 2 | crm-ops MCP server; gateway: JWT RBAC, Redis budget/loop middleware, HITL state machine; guardrail tests | done |
| 3 | agent-runtime (LangGraph + PydanticAI), operator-ui (Next.js) | next |
| 4 | Terraform module implementations, comprehensive README with Mermaid diagrams | |
