# mcp-gateway

Policy enforcement point between AI agents and enterprise MCP servers. Agents connect to
`/mcp` (MCP Streamable HTTP) and nowhere else. Operators use the control-plane API.

## Every `tools/call`, in order

| # | Step | Store | Refusal seen by the agent |
|---|---|---|---|
| 0 | JWT validation (python-jose: alg allow-list, `iss`/`aud`/`exp`, `tenant_id`, `role`, `sid`) | | HTTP 401 / 403 |
| 1 | Loop detection: >3 identical calls in 60 s trips the session's breaker | Redis (Lua) | `CIRCUIT_OPEN` |
| 2 | RBAC from `policies.yaml` (default deny; denied and nonexistent tools look the same) | policy engine | JSON-RPC `-32602 Unknown tool` |
| 3 | Arguments validated against the tool's closed input schema | | `INVALID_ARGUMENTS` |
| 4 | Budget pre-check (tool calls, tokens, USD) | Redis | `BUDGET_EXCEEDED` |
| 5 | Human approval for `requires_approval` tools | PostgreSQL | `PENDING_APPROVAL`, `REJECTED`, `EXPIRED`, `APPROVAL_FAILED` |
| 6 | Atomic charge + forward with a signed identity assertion | Redis, upstream | `UPSTREAM_UNAVAILABLE` |

Refusals from steps 1 and 3-6 are `CallToolResult`s with `is_error: true`. Each carries a
message the model can act on and a machine-readable entry that agent runtimes branch on:

```json
"_meta": {"mcp-enterprise-gateway/governance": {"code": "PENDING_APPROVAL", "approval_id": "…", "status": "PENDING_APPROVAL", "expires_at": "…"}}
```

## Human in the loop

1. The call is persisted as `PENDING_APPROVAL` (the payload is immutable in the database).
   An identical call already pending in the same session is reused, never duplicated.
2. It is announced as `approval.requested` on `/v1/events` (SSE) and as a signed webhook.
3. The agent's `tools/call` is held for `approvals.inline_wait_seconds`, with progress
   notifications as keep-alives. If an operator decides in time, the agent receives the
   real result. Otherwise it receives `PENDING_APPROVAL` with the approval id, checkpoints,
   and resumes later from `GET /v1/approvals/{id}`.
4. `POST /v1/approvals/{id}/decide` checks the approver role, the four-eyes rule and the
   tenant. On approval the gateway executes exactly the stored payload, once.

The states are `PENDING_APPROVAL -> APPROVED -> EXECUTING -> EXECUTED | FAILED`, or
`PENDING_APPROVAL -> REJECTED | EXPIRED`. A database trigger enforces them, and an
append-only audit trail records every transition.

## Control-plane API

| Endpoint | Who |
|---|---|
| `GET /v1/approvals?status=…` | `approvals:read` |
| `GET /v1/approvals/{id}` | `approvals:read`, or the requesting agent session |
| `POST /v1/approvals/{id}/decide` `{"decision": "approve"\|"reject", "reason": …}` | `approvals:decide` + approver role, not the requester |
| `GET /v1/sessions`, `GET /v1/sessions/{sid}` | `sessions:read` (or own session) |
| `POST /v1/sessions/{sid}/usage` (Anthropic-style `usage`) | the session itself |
| `POST /v1/sessions/{sid}/circuit-breaker/reset` | `circuit_breakers:reset` |
| `GET /v1/events` (SSE, `Last-Event-ID` resume) | `approvals:read` / `sessions:read` |
| `GET /v1/policy` | `policy:read` |
| `GET /healthz`, `GET /readyz` | unauthenticated |

Errors are RFC 9457 problem documents. OpenAPI docs are served at `/docs`.

## Configuration (`GATEWAY_*`)

| Variable | Purpose |
|---|---|
| `POLICY_PATH` | `policies.yaml` (baked into the image at `/app/config/policies.yaml`) |
| `DATABASE_HOST`/`PORT`/`NAME`/`USER`/`PASSWORD`/`SSL` | approval store |
| `REDIS_HOST`/`PORT`/`PASSWORD`/`TLS`/`KEY_PREFIX` | budgets, breakers, events |
| `UPSTREAMS` | `{"crm-ops": "http://crm-ops:8001/mcp"}` |
| `UPSTREAM_SIGNING_KEY` | signs identity assertions to upstreams |
| `JWT_ISSUER`, `JWT_AUDIENCE`, `JWT_ALGORITHMS` | token contract |
| `JWT_JWKS_URL` (RS*/ES*) or `JWT_SECRET` (HS*, local only) | token keys |
| `WEBHOOK_URL`, `WEBHOOK_HMAC_KEY` | signed approval webhooks |
| `ALLOWED_HOSTS`, `CORS_ORIGINS` | DNS-rebinding protection, operator console origin |

Exporters follow the standard `OTEL_*` variables.

## Commands

```bash
mcp-gateway                                          # run the server
mcp-gateway-policy validate|matrix|schema            # policy tooling
mcp-gateway-token --role analyst --tenant acme       # local dev JWT (HS256)
```
