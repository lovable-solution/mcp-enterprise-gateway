// Shapes of the gateway's control-plane API (mcp_gateway.api / approvals.models).

export type ApprovalStatus =
  | "PENDING_APPROVAL"
  | "APPROVED"
  | "REJECTED"
  | "EXPIRED"
  | "EXECUTING"
  | "EXECUTED"
  | "FAILED";

export interface AuditEntry {
  id: number;
  at: string;
  from_status: ApprovalStatus | null;
  to_status: ApprovalStatus;
  actor_subject: string | null;
  actor_role: string | null;
  detail: Record<string, unknown>;
}

export interface Approval {
  id: string;
  tenant_id: string;
  session_id: string;
  requester_subject: string;
  requester_role: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  arguments_digest?: string;
  risk: string;
  approver_roles: string[];
  status: ApprovalStatus;
  policy_name?: string;
  policy_digest?: string;
  policy_rule?: string;
  trace_id?: string | null;
  created_at: string;
  expires_at: string;
  decided_at?: string | null;
  decided_by_subject?: string | null;
  decided_by_role?: string | null;
  decision_reason?: string | null;
  executed_at?: string | null;
  result?: Record<string, unknown> | null;
  error?: string | null;
  status_changed_at?: string | null;
  audit?: AuditEntry[];
}

export interface BudgetSnapshot {
  tool_calls: number;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  cache_write_tokens: number;
  cache_read_tokens: number;
  cost_usd: string;
  llm_cost_usd: string;
  tool_cost_usd: string;
  max_tool_calls: number;
  max_total_tokens: number;
  max_cost_usd: string;
  exhausted_limit: "max_tool_calls" | "max_total_tokens" | "max_cost_usd" | null;
  utilisation?: number;
}

export interface SessionInfo {
  session_id: string;
  subject: string;
  role: string;
  first_seen: string;
  last_seen: string;
}

export interface BreakerStatus {
  open: boolean;
  retry_after_seconds: number | null;
  trip: Record<string, unknown> | null;
}

export interface SessionStatus {
  session: SessionInfo;
  budget: BudgetSnapshot;
  circuit_breaker: BreakerStatus;
}

export type GatewayEventType =
  | "approval.requested"
  | "approval.approved"
  | "approval.rejected"
  | "approval.executed"
  | "approval.failed"
  | "approval.expired"
  | "circuit_breaker.tripped"
  | "circuit_breaker.reset"
  | "budget.warning"
  | "budget.exhausted"
  | "tool_call.denied"
  | "session.activity";

export const GATEWAY_EVENT_TYPES: readonly GatewayEventType[] = [
  "approval.requested",
  "approval.approved",
  "approval.rejected",
  "approval.executed",
  "approval.failed",
  "approval.expired",
  "circuit_breaker.tripped",
  "circuit_breaker.reset",
  "budget.warning",
  "budget.exhausted",
  "tool_call.denied",
  "session.activity",
];

export interface GatewayEvent {
  id: string | null;
  type: GatewayEventType;
  tenant_id: string;
  session_id: string | null;
  occurred_at: string;
  data: Record<string, unknown>;
}

/** What the console knows about the signed-in operator (never the token itself). */
export interface Operator {
  subject: string;
  role: string;
  tenant: string;
  expiresAt: number;
  /** The role's control-plane actions per the gateway's policy (e.g. "approvals:decide").
   * Only shapes the UI: the gateway authorises every request itself. */
  actions: string[];
}
