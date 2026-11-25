# crm-ops MCP server

Sample downstream MCP server (`MCPServer` from the official SDK) over stateless Streamable
HTTP with JSON responses. Data is an in-memory, seeded, multi-tenant CRM (tenants `acme`
and `zenith`).

| Tool | Qualified name at the gateway | Access | Gate |
| --- | --- | --- | --- |
| `search_accounts(query, segment?, limit?)` | `crm-ops__search_accounts` | read | none |
| `get_account_metrics(account_id)` | `crm-ops__get_account_metrics` | read | metered, $0.002 per call |
| `update_credit_limit(account_id, new_limit_usd, reason)` | `crm-ops__update_credit_limit` | write | human approval |

Every tool returns a strict Pydantic model, published as its `outputSchema` and returned
as `structuredContent`.

## Trust model

- Reachable only from the gateway: an internal compose network locally, and a security
  group admitting the gateway's security group alone on ECS.
- Every `/mcp` request must carry a gateway identity assertion: HS256, `iss=mcp-gateway`,
  `aud=crm-ops`, at most 60 s old, with `tenant_id`, `sub`, `sid`, `role` and, for an
  approved call, `approval_id`. Without one the request gets HTTP 401 before any MCP
  processing. Agents' own tokens never reach this server.
- Every query is scoped to the asserted tenant, so another tenant's accounts are
  indistinguishable from missing ones.
- MCP tool annotations (`readOnlyHint`, `destructiveHint`) are informational only. The
  gateway's `policies.yaml` is authoritative.
- crm-ops applies its own credit policy on top of the gateway's approval. Accounts on hold
  cannot change, and limits above $5M need manual underwriting.

## Configuration (`CRM_OPS_*`)

`GATEWAY_ASSERTION_KEY` (required, shared with the gateway), `ALLOWED_HOSTS`, `HOST`, `PORT`
(8001), `ENVIRONMENT`. Telemetry follows the standard `OTEL_*` variables.
