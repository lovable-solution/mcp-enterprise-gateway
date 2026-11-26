import { describe, expect, it } from "vitest";

import { bucketize, classify, countTraffic, describe as describeEvent, describeAll, serverOf } from "@/lib/traffic";
import type { GatewayEvent, GatewayEventType } from "@/lib/types";

const T0 = Date.parse("2026-11-26T12:00:00Z");

function event(type: GatewayEventType, data: Record<string, unknown> = {}, at = T0, sessionId: string | null = "s-1"): GatewayEvent {
  return { id: `${type}:${at}:${Math.random()}`, type, tenant_id: "acme", session_id: sessionId, occurred_at: new Date(at).toISOString(), data };
}

describe("classify", () => {
  it("tells tool calls from model usage", () => {
    expect(classify(event("session.activity", { tool: "crm-ops__search_accounts" }))).toBe("allowed");
    expect(classify(event("session.activity", { model: "claude-opus-5-5" }))).toBe("model");
  });

  it("maps every other event type", () => {
    expect(classify(event("approval.requested"))).toBe("held");
    expect(classify(event("approval.executed"))).toBe("executed");
    expect(classify(event("approval.expired"))).toBe("expired");
    expect(classify(event("tool_call.denied"))).toBe("denied");
    expect(classify(event("circuit_breaker.tripped"))).toBe("breaker");
    expect(classify(event("circuit_breaker.reset"))).toBe("breaker_reset");
    expect(classify(event("budget.exhausted"))).toBe("budget");
  });
});

describe("serverOf", () => {
  it("takes the server from a qualified tool name", () => {
    expect(serverOf("crm-ops__update_credit_limit")).toBe("crm-ops");
    expect(serverOf("plain")).toBeNull();
    expect(serverOf(null)).toBeNull();
  });
});

describe("countTraffic and bucketize", () => {
  const events = [
    event("session.activity", { tool: "crm-ops__search_accounts" }, T0 - 30_000),
    event("session.activity", { tool: "crm-ops__search_accounts" }, T0 - 90_000),
    event("approval.requested", {}, T0 - 100_000),
    event("tool_call.denied", {}, T0 - 20_000),
    event("circuit_breaker.tripped", {}, T0 - 10_000),
    event("session.activity", { model: "claude-opus-5-5" }, T0 - 5_000),
    event("session.activity", { tool: "crm-ops__search_accounts" }, T0 - 20 * 60_000),
  ];

  it("counts outcomes inside the window only", () => {
    expect(countTraffic(events, T0, 10 * 60_000)).toEqual({ allowed: 2, held: 1, denied: 2, model: 1 });
  });

  it("buckets events oldest first", () => {
    const buckets = bucketize(events, T0, 2 * 60_000, 60_000);
    expect(buckets).toHaveLength(2);
    expect(buckets[0]).toMatchObject({ allowed: 1, held: 1 });
    expect(buckets[1]).toMatchObject({ allowed: 1, denied: 2, model: 1 });
    expect(buckets[0]?.start).toBe(T0 - 2 * 60_000);
  });
});

describe("describe", () => {
  it("writes a title and detail per event", () => {
    expect(describeEvent(event("approval.requested", { tool: "crm-ops__update_credit_limit", requester: { subject: "crm-copilot", role: "admin" } }))).toMatchObject({
      title: "update_credit_limit held for review",
      detail: "requested by crm-copilot (admin)",
    });
    expect(describeEvent(event("tool_call.denied", { tool: "crm-ops__update_credit_limit", subject: "recon", role: "analyst", reason: "not allowed" }))).toMatchObject({
      title: "update_credit_limit denied",
      detail: "recon (analyst): not allowed",
    });
    expect(describeEvent(event("budget.exhausted", { subject: "recon", limit: "max_cost_usd" })).detail).toBe("recon hit the cost usd cap");
  });

  it("works out what each activity event cost from consecutive snapshots", () => {
    const newestFirst = [
      event("session.activity", { tool: "crm-ops__get_account_metrics", budget: { cost_usd: "0.017129" } }, T0),
      event("session.activity", { tool: "crm-ops__search_accounts", budget: { cost_usd: "0.015129" } }, T0 - 1_000, "s-2"),
      event("session.activity", { model: "claude-opus-5-5", budget: { cost_usd: "0.015129" } }, T0 - 2_000),
      event("session.activity", { tool: "crm-ops__search_accounts", budget: { cost_usd: "0.010000" } }, T0 - 3_000),
    ];
    const deltas = describeAll(newestFirst).map((entry) => entry.costDelta);
    expect(deltas[0]).toBeCloseTo(0.002);
    expect(deltas[1]).toBeNull(); // first snapshot of s-2 in view
    expect(deltas[2]).toBeCloseTo(0.005129);
    expect(deltas[3]).toBeNull();
  });
});
