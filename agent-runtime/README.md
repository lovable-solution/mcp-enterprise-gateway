# agent-runtime

A governed agent: a LangGraph graph around a PydanticAI agent (Claude Opus 5.5) that
reaches tools only through the MCP gateway and pauses durably while a human decides.
The repository README explains the design. This page lists how to run and configure it.

```bash
agent-runtime run "Check the receivables of Cedar Analytics" --wait   # new thread
agent-runtime status t-3f2a9c1e5b7d4a60
agent-runtime resume t-3f2a9c1e5b7d4a60 --wait                       # after a decision
agent-runtime retry  t-3f2a9c1e5b7d4a60                              # after a halt
```

Exit codes: 0 completed, 2 awaiting approval, 3 halted by the gateway, 1 failed.

## Configuration (`AGENT_*`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `AGENT_GATEWAY_URL` | `http://localhost:8000` | the gateway, the agent's only route to tools |
| `AGENT_GATEWAY_TOKEN` | - | IdP-issued token for one session (its `sid` is the thread id) |
| `AGENT_DEV_JWT_SECRET` | `GATEWAY_JWT_SECRET` | local stack only: mint per-thread HS256 tokens |
| `AGENT_TENANT_ID`, `AGENT_SUBJECT`, `AGENT_ROLE` | `acme`, `crm-copilot`, `admin` | identity in minted tokens |
| `ANTHROPIC_API_KEY` | - | Claude API key (or `AGENT_ANTHROPIC_API_KEY`) |
| `AGENT_MODEL`, `AGENT_EFFORT` | `claude-opus-5-5`, `medium` | model and effort (thinking is always adaptive) |
| `AGENT_SERVER_SIDE_FALLBACKS` | `true` | `fallbacks: "default"`: a declined request is re-run on the recommended fallback model |
| `AGENT_MAX_MODEL_REQUESTS` | `30` | local backstop per run; budgets are enforced by the gateway |
| `AGENT_CHECKPOINTER` | `postgres` | `postgres` or `memory` |
| `AGENT_DATABASE_HOST` / `_PORT` / `_NAME` / `_USER` / `_PASSWORD` / `_SSL` | `localhost` / `15432` / `agent_runtime` / `agent_runtime` / - / `prefer` | LangGraph checkpoints |
| `AGENT_TRACE_CONTENT` | `false` | record prompts and tool payloads (CRM data) in trace spans |

From the repository root the CLI also reads `./.env`, so `uv run agent-runtime ...`
works against the local stack without exporting anything.
