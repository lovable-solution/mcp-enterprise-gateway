# agent-runtime (Phase 3)

Sample orchestrator agent: LangGraph for control flow and checkpointing, PydanticAI for
typed model calls (Anthropic Claude).

Contract:

- Connects **only** to the gateway's MCP endpoint, with a JWT carrying `sub`,
  `tenant_id`, `role` and `sid` (the budget/session key). It has no network route
  to MCP servers and no credentials for Redis or the gateway database.
- Handles `PENDING_APPROVAL` results with a LangGraph `interrupt()`, checkpointing to
  its own Postgres database (`agent_runtime`, separate role); resumes when the
  approval decision event arrives.
- Reports model token usage to the gateway so session budgets cover LLM spend.
