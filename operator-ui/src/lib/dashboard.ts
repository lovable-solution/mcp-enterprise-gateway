// Dashboard state: the REST snapshot plus every live event, folded by a pure reducer.
// Everything here is deterministic and unit-tested; components only render it.

import { toNumber } from "./format";
import type {
  Approval,
  ApprovalStatus,
  BudgetSnapshot,
  GatewayEvent,
  SessionStatus,
} from "./types";

export const ACTIVE_WINDOW_MS = 15 * 60 * 1000;
/** The shipped policy's `budgets.warn_at_ratio`; the gateway's own budget.warning events
 * flag a session too. */
export const WARN_AT_RATIO = 0.8;
/** Enough live history for the activity log and its 15-minute histogram. */
export const MAX_EVENTS = 400;
const MAX_ALERTS = 50;
const MAX_SEEN = 1000;
const BURN_WINDOW_MS = 5 * 60 * 1000;
/** An event that arrives later than this after it happened is history (the console loads
 * recent history when it connects): it updates state but is not a spend sample "now". */
const LIVE_LAG_MS = 15_000;
const BREAKER_SETTLE_MS = 10_000;

export type Severity = "warning" | "serious" | "critical";

export interface Alert {
  id: string;
  kind: "breaker" | "budget_exhausted" | "budget_warning" | "approval_failed" | "denied";
  severity: Severity;
  sessionId: string | null;
  title: string;
  detail: string;
  at: string;
  resolved: boolean;
}

export interface SpendSample {
  at: number;
  usd: number;
}

export interface DashboardState {
  approvals: Record<string, Approval>;
  sessions: Record<string, SessionStatus>;
  warned: Record<string, true>;
  alerts: Alert[];
  events: GatewayEvent[];
  seen: string[];
  spend: SpendSample[];
}

export const initialState: DashboardState = {
  approvals: {},
  sessions: {},
  warned: {},
  alerts: [],
  events: [],
  seen: [],
  spend: [],
};

export type Action =
  | { type: "approvals/loaded"; approvals: Approval[] }
  | { type: "approval/loaded"; approval: Approval }
  | { type: "sessions/loaded"; sessions: SessionStatus[]; at: number }
  | { type: "event"; event: GatewayEvent; at: number }
  | { type: "alert/dismiss"; id: string };

export function reduce(state: DashboardState, action: Action): DashboardState {
  switch (action.type) {
    case "approvals/loaded": {
      const approvals = { ...state.approvals };
      for (const approval of action.approvals) {
        approvals[approval.id] = { ...approvals[approval.id], ...approval };
      }
      return { ...state, approvals };
    }
    case "approval/loaded":
      return {
        ...state,
        approvals: {
          ...state.approvals,
          [action.approval.id]: { ...state.approvals[action.approval.id], ...action.approval },
        },
      };
    case "sessions/loaded": {
      const sessions: Record<string, SessionStatus> = {};
      for (const status of action.sessions) {
        sessions[status.session.session_id] = status;
      }
      // A breaker that cools down on its own emits no event: the snapshot is how the
      // console learns it closed. A trip younger than the settle time is left alone, in
      // case this snapshot was read just before the trip.
      const alerts = state.alerts.map((alert) => {
        const status = alert.sessionId === null ? undefined : sessions[alert.sessionId];
        const settled = action.at - Date.parse(alert.at) > BREAKER_SETTLE_MS;
        return alert.kind === "breaker" && !alert.resolved && status && !status.circuit_breaker.open && settled
          ? { ...alert, resolved: true }
          : alert;
      });
      return withSpendSample({ ...state, sessions, alerts }, action.at);
    }
    case "event":
      return applyEvent(state, action.event, action.at);
    case "alert/dismiss":
      return { ...state, alerts: state.alerts.filter((alert) => alert.id !== action.id) };
  }
}

function applyEvent(state: DashboardState, event: GatewayEvent, at: number): DashboardState {
  if (event.id !== null && state.seen.includes(event.id)) {
    return state; // replayed after a reconnect
  }
  let next: DashboardState = {
    ...state,
    events: [event, ...state.events].slice(0, MAX_EVENTS),
    seen: event.id === null ? state.seen : [event.id, ...state.seen].slice(0, MAX_SEEN),
  };
  const data = event.data;
  const sessionId = event.session_id;
  switch (event.type) {
    case "approval.requested":
      next = upsertApproval(next, approvalFromEvent(event));
      break;
    case "approval.approved":
      next = patchApproval(next, event, "APPROVED");
      break;
    case "approval.rejected":
      next = patchApproval(next, event, "REJECTED");
      break;
    case "approval.executed":
      next = patchApproval(next, event, "EXECUTED");
      break;
    case "approval.expired":
      next = patchApproval(next, event, "EXPIRED");
      break;
    case "approval.failed":
      next = patchApproval(next, event, "FAILED");
      next = addAlert(next, {
        id: `failed:${str(data.approval_id)}`,
        kind: "approval_failed",
        severity: "serious",
        sessionId,
        title: `Approved call failed: ${str(data.tool)}`,
        detail: str(data.error) || "the call could not be executed",
        at: event.occurred_at,
        resolved: false,
      });
      break;
    case "session.activity":
    case "budget.exhausted":
    case "budget.warning": {
      const budget = data.budget as BudgetSnapshot | undefined;
      if (sessionId !== null && budget) {
        next = patchSession(next, sessionId, event, budget);
      }
      if (event.type === "budget.warning" && sessionId !== null) {
        next = { ...next, warned: { ...next.warned, [sessionId]: true } };
        next = addAlert(next, {
          id: `warning:${sessionId}`,
          kind: "budget_warning",
          severity: "warning",
          sessionId,
          title: `Budget at ${Math.round(Number(data.warn_at_ratio ?? WARN_AT_RATIO) * 100)}%`,
          detail: `${str(data.subject)} has used most of its session budget`,
          at: event.occurred_at,
          resolved: false,
        });
      }
      if (event.type === "budget.exhausted") {
        next = addAlert(next, {
          id: `exhausted:${sessionId}`,
          kind: "budget_exhausted",
          severity: "critical",
          sessionId,
          title: "Budget exhausted",
          detail: `${str(data.subject)} reached ${str(data.limit).replace("max_", "the ").replaceAll("_", " ")} cap; further tool calls are refused`,
          at: event.occurred_at,
          resolved: false,
        });
      }
      if (at - Date.parse(event.occurred_at) <= LIVE_LAG_MS) {
        next = withSpendSample(next, at);
      }
      break;
    }
    case "circuit_breaker.tripped":
      if (sessionId !== null) {
        next = patchBreaker(next, sessionId, true, data);
      }
      next = addAlert(next, {
        id: `breaker:${sessionId}`,
        kind: "breaker",
        severity: "critical",
        sessionId,
        title: `Loop detected: ${str(data.tool)}`,
        detail: `${str(data.subject)} repeated an identical call ${str(data.identical_calls)} times in ${str(data.window_seconds)}s; the session is paused`,
        at: event.occurred_at,
        resolved: false,
      });
      break;
    case "circuit_breaker.reset":
      if (sessionId !== null) {
        next = patchBreaker(next, sessionId, false, null);
      }
      next = {
        ...next,
        alerts: next.alerts.map((alert) =>
          alert.kind === "breaker" && alert.sessionId === sessionId ? { ...alert, resolved: true } : alert,
        ),
      };
      break;
    case "tool_call.denied":
      next = addAlert(next, {
        id: `denied:${event.id ?? event.occurred_at}`,
        kind: "denied",
        severity: "serious",
        sessionId,
        title: `Denied: ${str(data.tool)}`,
        detail: `${str(data.subject)} (${str(data.role)}): ${str(data.reason)}`,
        at: event.occurred_at,
        resolved: false,
      });
      break;
  }
  return next;
}

function str(value: unknown): string {
  return value === undefined || value === null ? "" : String(value);
}

function approvalFromEvent(event: GatewayEvent): Approval {
  const data = event.data;
  const requester = (data.requester ?? {}) as { subject?: string; role?: string };
  return {
    id: str(data.approval_id),
    tenant_id: event.tenant_id,
    session_id: event.session_id ?? "",
    requester_subject: requester.subject ?? "",
    requester_role: requester.role ?? "",
    tool_name: str(data.tool),
    arguments: (data.arguments ?? {}) as Record<string, unknown>,
    risk: str(data.risk),
    approver_roles: (data.approver_roles ?? []) as string[],
    status: (data.status as ApprovalStatus | undefined) ?? "PENDING_APPROVAL",
    created_at: event.occurred_at,
    expires_at: str(data.expires_at),
    trace_id: (data.trace_id as string | null | undefined) ?? null,
  };
}

function upsertApproval(state: DashboardState, approval: Approval): DashboardState {
  if (!approval.id) {
    return state;
  }
  const existing = state.approvals[approval.id];
  return { ...state, approvals: { ...state.approvals, [approval.id]: { ...approval, ...existing } } };
}

function patchApproval(state: DashboardState, event: GatewayEvent, status: ApprovalStatus): DashboardState {
  const id = str(event.data.approval_id);
  const base = state.approvals[id] ?? approvalFromEvent(event);
  const patched: Approval = {
    ...base,
    status,
    decided_by_subject: (event.data.decided_by as string | null | undefined) ?? base.decided_by_subject,
    decision_reason: (event.data.decision_reason as string | null | undefined) ?? base.decision_reason,
    error: (event.data.error as string | undefined) ?? base.error,
    status_changed_at: event.occurred_at,
  };
  return id ? { ...state, approvals: { ...state.approvals, [id]: patched } } : state;
}

function patchSession(
  state: DashboardState,
  sessionId: string,
  event: GatewayEvent,
  budget: BudgetSnapshot,
): DashboardState {
  const existing = state.sessions[sessionId];
  if (existing && Date.parse(existing.session.last_seen) > Date.parse(event.occurred_at)) {
    return state; // an older snapshot (history or a late event) never overwrites a newer one
  }
  const status: SessionStatus = existing
    ? { ...existing, budget, session: { ...existing.session, last_seen: event.occurred_at } }
    : {
        session: {
          session_id: sessionId,
          subject: str(event.data.subject),
          role: str(event.data.role),
          first_seen: event.occurred_at,
          last_seen: event.occurred_at,
        },
        budget,
        circuit_breaker: { open: false, retry_after_seconds: null, trip: null },
      };
  return { ...state, sessions: { ...state.sessions, [sessionId]: status } };
}

function patchBreaker(
  state: DashboardState,
  sessionId: string,
  open: boolean,
  trip: Record<string, unknown> | null,
): DashboardState {
  const existing = state.sessions[sessionId];
  if (!existing) {
    return state; // appears with the next sessions refresh
  }
  return {
    ...state,
    sessions: {
      ...state.sessions,
      [sessionId]: {
        ...existing,
        circuit_breaker: {
          open,
          retry_after_seconds: open ? Number(trip?.retry_after_seconds ?? 0) || null : null,
          trip,
        },
      },
    },
  };
}

function addAlert(state: DashboardState, alert: Alert): DashboardState {
  const others = state.alerts.filter((existing) => existing.id !== alert.id);
  return { ...state, alerts: [alert, ...others].slice(0, MAX_ALERTS) };
}

function withSpendSample(state: DashboardState, at: number): DashboardState {
  const usd = totalSpend(state);
  const recent = state.spend.filter((sample) => sample.at >= at - 2 * BURN_WINDOW_MS);
  return { ...state, spend: [...recent, { at, usd }] };
}

// --- selectors -------------------------------------------------------------------------
export function totalSpend(state: DashboardState): number {
  return Object.values(state.sessions).reduce((sum, s) => sum + toNumber(s.budget.cost_usd), 0);
}

export function pendingApprovals(state: DashboardState): Approval[] {
  return Object.values(state.approvals)
    .filter((approval) => approval.status === "PENDING_APPROVAL")
    .sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
}

export function recentDecisions(state: DashboardState, limit = 10): Approval[] {
  return Object.values(state.approvals)
    .filter((approval) => approval.status !== "PENDING_APPROVAL")
    .sort((a, b) => changedAt(b) - changedAt(a))
    .slice(0, limit);
}

function changedAt(approval: Approval): number {
  return Date.parse(
    approval.status_changed_at ?? approval.executed_at ?? approval.decided_at ?? approval.created_at,
  );
}

export function utilisation(budget: BudgetSnapshot): number {
  const ratio = (used: number, cap: number) => (cap > 0 ? used / cap : 0);
  return Math.max(
    ratio(budget.tool_calls, budget.max_tool_calls),
    ratio(budget.total_tokens, budget.max_total_tokens),
    ratio(toNumber(budget.cost_usd), toNumber(budget.max_cost_usd)),
  );
}

export type BudgetState = "ok" | "warning" | "exhausted";

export function budgetState(state: DashboardState, status: SessionStatus): BudgetState {
  if (status.budget.exhausted_limit !== null) {
    return "exhausted";
  }
  const warned = state.warned[status.session.session_id] === true;
  return warned || utilisation(status.budget) >= WARN_AT_RATIO ? "warning" : "ok";
}

export function sessionsByActivity(state: DashboardState): SessionStatus[] {
  return Object.values(state.sessions).sort(
    (a, b) => Date.parse(b.session.last_seen) - Date.parse(a.session.last_seen),
  );
}

/** USD per minute over the last five minutes of samples; null until there is a span. */
export function burnPerMinute(state: DashboardState, now: number): number | null {
  const window = state.spend.filter((sample) => sample.at >= now - BURN_WINDOW_MS);
  const first = window[0];
  const last = window.at(-1);
  if (!first || !last || last.at - first.at < 10_000) {
    return null;
  }
  const minutes = (last.at - first.at) / 60_000;
  return Math.max(0, (last.usd - first.usd) / minutes);
}

export interface Kpis {
  pending: number;
  activeSessions: number;
  spendUsd: number;
  burnPerMinute: number | null;
  openBreakers: number;
  openAlerts: number;
}

export function kpis(state: DashboardState, now: number): Kpis {
  const sessions = Object.values(state.sessions);
  return {
    pending: pendingApprovals(state).length,
    activeSessions: sessions.filter((s) => now - Date.parse(s.session.last_seen) <= ACTIVE_WINDOW_MS).length,
    spendUsd: totalSpend(state),
    burnPerMinute: burnPerMinute(state, now),
    openBreakers: sessions.filter((s) => s.circuit_breaker.open).length,
    openAlerts: state.alerts.filter((alert) => !alert.resolved).length,
  };
}
