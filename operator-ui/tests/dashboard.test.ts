import { describe, expect, it } from "vitest";

import {
  type DashboardState,
  budgetState,
  burnPerMinute,
  initialState,
  kpis,
  pendingApprovals,
  recentDecisions,
  reduce,
  utilisation,
} from "@/lib/dashboard";
import type { Approval, BudgetSnapshot, GatewayEvent, SessionStatus } from "@/lib/types";

const T0 = Date.parse("2026-11-25T10:00:00Z");

function budget(overrides: Partial<BudgetSnapshot> = {}): BudgetSnapshot {
  return {
    tool_calls: 2,
    total_tokens: 1_000,
    input_tokens: 800,
    output_tokens: 200,
    cache_write_tokens: 0,
    cache_read_tokens: 0,
    cost_usd: "0.010000",
    llm_cost_usd: "0.008000",
    tool_cost_usd: "0.002000",
    max_tool_calls: 50,
    max_total_tokens: 500_000,
    max_cost_usd: "5.00",
    exhausted_limit: null,
    ...overrides,
  };
}

function session(id: string, overrides: Partial<BudgetSnapshot> = {}, lastSeen = T0): SessionStatus {
  return {
    session: {
      session_id: id,
      subject: "crm-copilot",
      role: "admin",
      first_seen: new Date(T0 - 60_000).toISOString(),
      last_seen: new Date(lastSeen).toISOString(),
    },
    budget: budget(overrides),
    circuit_breaker: { open: false, retry_after_seconds: null, trip: null },
  };
}

let sequence = 0;
function event(type: GatewayEvent["type"], data: Record<string, unknown>, sessionId: string | null = "t-1"): GatewayEvent {
  sequence += 1;
  return {
    id: `${T0 + sequence}-0`,
    type,
    tenant_id: "acme",
    session_id: sessionId,
    occurred_at: new Date(T0 + sequence * 1000).toISOString(),
    data,
  };
}

const requested = (id: string) =>
  event("approval.requested", {
    approval_id: id,
    status: "PENDING_APPROVAL",
    tool: "crm-ops__update_credit_limit",
    arguments: { account_id: "ACC-1001", new_limit_usd: 600_000 },
    risk: "high",
    requester: { subject: "crm-copilot", role: "admin" },
    approver_roles: ["operator"],
    expires_at: new Date(T0 + 1_800_000).toISOString(),
  });

function apply(state: DashboardState, ...events: GatewayEvent[]): DashboardState {
  return events.reduce((current, item) => reduce(current, { type: "event", event: item, at: T0 }), state);
}

describe("approval events", () => {
  it("puts a requested call in the queue and moves it out once decided", () => {
    let state = apply(initialState, requested("a-1"), requested("a-2"));
    expect(pendingApprovals(state).map((a) => a.id)).toEqual(["a-1", "a-2"]); // oldest first
    expect(state.approvals["a-1"]?.requester_subject).toBe("crm-copilot");

    state = apply(
      state,
      event("approval.approved", { approval_id: "a-1", decided_by: "ops-alice" }),
      event("approval.executed", { approval_id: "a-1", decided_by: "ops-alice" }),
    );
    expect(pendingApprovals(state).map((a) => a.id)).toEqual(["a-2"]);
    expect(state.approvals["a-1"]).toMatchObject({ status: "EXECUTED", decided_by_subject: "ops-alice" });
    expect(recentDecisions(state).map((a) => a.id)).toEqual(["a-1"]);
  });

  it("raises an alert when an approved call fails", () => {
    const state = apply(
      initialState,
      requested("a-3"),
      event("approval.failed", { approval_id: "a-3", tool: "crm-ops__update_credit_limit", error: "crm-ops unreachable" }),
    );
    expect(state.approvals["a-3"]?.status).toBe("FAILED");
    expect(state.alerts[0]).toMatchObject({ kind: "approval_failed", severity: "serious", detail: "crm-ops unreachable" });
  });

  it("keeps the richer REST record when an event arrives for a loaded approval", () => {
    const loaded: Approval = {
      ...(pendingApprovals(apply(initialState, requested("a-4")))[0] as Approval),
      policy_name: "acme-default",
      audit: [],
    };
    let state = reduce(initialState, { type: "approvals/loaded", approvals: [loaded] });
    state = apply(state, requested("a-4"));
    expect(state.approvals["a-4"]?.policy_name).toBe("acme-default");
  });

  it("ignores events replayed after a reconnect", () => {
    const first = requested("a-5");
    const state = apply(initialState, first, first);
    expect(state.events).toHaveLength(1);
  });
});

describe("sessions and spend", () => {
  it("updates a session's budget from live activity", () => {
    let state = reduce(initialState, { type: "sessions/loaded", sessions: [session("t-1")], at: T0 });
    state = apply(
      state,
      event("session.activity", { subject: "crm-copilot", role: "admin", budget: budget({ cost_usd: "0.250000", tool_calls: 5 }) }),
    );
    expect(state.sessions["t-1"]?.budget.cost_usd).toBe("0.250000");
    expect(state.sessions["t-1"]?.budget.tool_calls).toBe(5);
  });

  it("adds sessions it first hears about from an event", () => {
    const state = apply(initialState, event("session.activity", { subject: "night-batch", role: "analyst", budget: budget() }, "t-new"));
    expect(state.sessions["t-new"]?.session).toMatchObject({ subject: "night-batch", role: "analyst" });
  });

  it("computes the burn rate over the last five minutes", () => {
    let state = reduce(initialState, { type: "sessions/loaded", sessions: [session("t-1", { cost_usd: "1.00" })], at: T0 });
    state = reduce(state, {
      type: "event",
      event: { ...event("session.activity", { budget: budget({ cost_usd: "1.50" }) }), occurred_at: new Date(T0 + 120_000).toISOString() },
      at: T0 + 120_000,
    });
    expect(burnPerMinute(state, T0 + 120_000)).toBeCloseTo(0.25); // $0.50 over 2 minutes
    expect(burnPerMinute(initialState, T0)).toBeNull();
  });

  it("keeps replayed history out of the burn rate and never lets an older snapshot win", () => {
    let state = reduce(initialState, {
      type: "sessions/loaded",
      sessions: [session("t-1", { cost_usd: "1.00" }, T0 + 60_000)],
      at: T0 + 60_000,
    });
    const history = { ...event("session.activity", { budget: budget({ cost_usd: "0.40" }) }), occurred_at: new Date(T0).toISOString() };
    state = reduce(state, { type: "event", event: history, at: T0 + 120_000 });
    expect(state.sessions["t-1"]?.budget.cost_usd).toBe("1.00"); // the newer snapshot stands
    expect(state.spend).toHaveLength(1); // history is not a spend sample "now"
    expect(state.events).toHaveLength(1); // but it is in the activity log
  });

  it("classifies budgets: ok, warning (by ratio or the gateway's warning), exhausted", () => {
    const ok = session("t-1", { cost_usd: "1.00" });
    const high = session("t-2", { cost_usd: "4.20" });
    const exhausted = session("t-3", { cost_usd: "5.00", exhausted_limit: "max_cost_usd" });
    let state = reduce(initialState, { type: "sessions/loaded", sessions: [ok, high, exhausted], at: T0 });
    expect(utilisation(high.budget)).toBeCloseTo(0.84);
    expect([ok, high, exhausted].map((s) => budgetState(state, s))).toEqual(["ok", "warning", "exhausted"]);

    state = apply(state, event("budget.warning", { subject: "crm-copilot", warn_at_ratio: 0.8, budget: ok.budget }, "t-1"));
    expect(budgetState(state, ok)).toBe("warning");
    expect(state.alerts[0]).toMatchObject({ kind: "budget_warning", severity: "warning" });
  });

  it("counts active sessions in the last 15 minutes and sums their spend", () => {
    const state = reduce(initialState, {
      type: "sessions/loaded",
      sessions: [session("t-1", { cost_usd: "0.10" }), session("t-old", { cost_usd: "0.40" }, T0 - 3_600_000)],
      at: T0,
    });
    const figures = kpis(state, T0 + 60_000);
    expect(figures.activeSessions).toBe(1);
    expect(figures.spendUsd).toBeCloseTo(0.5);
  });
});

describe("circuit breakers", () => {
  it("opens on a trip with a critical alert, and the reset resolves it", () => {
    let state = reduce(initialState, { type: "sessions/loaded", sessions: [session("t-1")], at: T0 });
    state = apply(
      state,
      event("circuit_breaker.tripped", {
        tool: "crm-ops__search_accounts",
        subject: "crm-copilot",
        identical_calls: 4,
        window_seconds: 60,
        retry_after_seconds: 300,
      }),
    );
    expect(state.sessions["t-1"]?.circuit_breaker.open).toBe(true);
    expect(kpis(state, T0).openBreakers).toBe(1);
    expect(state.alerts[0]).toMatchObject({ kind: "breaker", severity: "critical", resolved: false });

    state = apply(state, event("circuit_breaker.reset", { reset_by: "ops-alice" }));
    expect(state.sessions["t-1"]?.circuit_breaker.open).toBe(false);
    expect(state.alerts[0]?.resolved).toBe(true);
    expect(kpis(state, T0).openAlerts).toBe(0);
  });

  it("resolves a loop alert once a snapshot shows the breaker cooled down by itself", () => {
    const trip = event("circuit_breaker.tripped", { tool: "crm-ops__search_accounts", subject: "crm-copilot" });
    const trippedAt = Date.parse(trip.occurred_at);
    let state = apply(reduce(initialState, { type: "sessions/loaded", sessions: [session("t-1")], at: T0 }), trip);
    // A snapshot read around the trip itself proves nothing yet.
    state = reduce(state, { type: "sessions/loaded", sessions: [session("t-1")], at: trippedAt + 2_000 });
    expect(state.alerts[0]?.resolved).toBe(false);
    // Five minutes on, the breaker is closed: the alert is history.
    state = reduce(state, { type: "sessions/loaded", sessions: [session("t-1")], at: trippedAt + 300_000 });
    expect(state.alerts[0]?.resolved).toBe(true);
    expect(kpis(state, trippedAt + 300_000).openAlerts).toBe(0);
  });

  it("lets an operator dismiss alerts", () => {
    let state = apply(initialState, event("tool_call.denied", { tool: "crm-ops__purge_accounts", reason: "denied by rule", subject: "x", role: "analyst" }));
    const id = state.alerts[0]?.id ?? "";
    state = reduce(state, { type: "alert/dismiss", id });
    expect(state.alerts).toHaveLength(0);
  });
});
