import { describe, expect, it } from "vitest";

import { parseConfig } from "@/lib/config";
import { formatCount, formatRelative, formatToolName, formatUsd } from "@/lib/format";
import {
  approvalListQuery,
  decisionSchema,
  isApprovalId,
  isSameOrigin,
  isSessionId,
  signInSchema,
} from "@/lib/validation";

describe("route parameters", () => {
  it("accepts only approval UUIDs, so no other gateway path can be reached", () => {
    expect(isApprovalId("0aebb3a5-8455-47f1-8dd5-e9c0cb5746c5")).toBe(true);
    for (const bad of ["../policy", "0aebb3a5-8455-47f1-8dd5-e9c0cb5746c5/decide", "", "x"]) {
      expect(isApprovalId(bad)).toBe(false);
    }
  });

  it("accepts gateway session ids and nothing path-like", () => {
    expect(isSessionId("t-e3d6231005344561")).toBe(true);
    for (const bad of ["../x", "a/b", "-x", "a b", "a".repeat(129)]) {
      expect(isSessionId(bad)).toBe(false);
    }
  });

  it("bounds list queries", () => {
    expect(approvalListQuery.parse({ status: "PENDING_APPROVAL", limit: "20" })).toEqual({
      status: "PENDING_APPROVAL",
      limit: 20,
    });
    expect(approvalListQuery.safeParse({ limit: "100000" }).success).toBe(false);
    expect(approvalListQuery.safeParse({ status: "DELETED" }).success).toBe(false);
  });
});

describe("decisions", () => {
  it("requires a reason to reject, not to approve", () => {
    expect(decisionSchema.safeParse({ decision: "approve" }).success).toBe(true);
    expect(decisionSchema.safeParse({ decision: "reject" }).success).toBe(false);
    expect(decisionSchema.safeParse({ decision: "reject", reason: "   " }).success).toBe(false);
    expect(decisionSchema.parse({ decision: "reject", reason: " too high " })).toEqual({
      decision: "reject",
      reason: "too high",
    });
    expect(decisionSchema.safeParse({ decision: "execute" }).success).toBe(false);
  });
});

describe("sign-in", () => {
  it("allows only console roles for development sign-in", () => {
    expect(signInSchema.safeParse({ mode: "dev", subject: "ops-alice", role: "operator" }).success).toBe(true);
    expect(signInSchema.safeParse({ mode: "dev", subject: "ops-alice", role: "admin" }).success).toBe(false);
    expect(signInSchema.safeParse({ mode: "dev", subject: "{evil}", role: "operator" }).success).toBe(false);
  });

  it("accepts only JWT-shaped tokens", () => {
    expect(signInSchema.safeParse({ mode: "token", token: "aaaaaaaa.bbbbbbbb.cccccccc" }).success).toBe(true);
    expect(signInSchema.safeParse({ mode: "token", token: "Bearer aaaaaaaa.bbbb.cccc" }).success).toBe(false);
  });
});

describe("same-origin check", () => {
  const request = (headers: Record<string, string>) =>
    new Request("http://localhost:3000/api/approvals/x/decide", { method: "POST", headers });

  it("accepts same-origin requests and refuses cross-site ones", () => {
    expect(isSameOrigin(request({ origin: "http://localhost:3000", host: "localhost:3000" }))).toBe(true);
    expect(isSameOrigin(request({ origin: "https://evil.example", host: "localhost:3000" }))).toBe(false);
    expect(isSameOrigin(request({ "sec-fetch-site": "cross-site", host: "localhost:3000" }))).toBe(false);
    expect(isSameOrigin(request({ host: "localhost:3000" }))).toBe(true); // non-browser client
  });
});

describe("configuration", () => {
  const base = { SESSION_SECRET: "x".repeat(32) };

  it("defaults to a safe configuration", () => {
    const config = parseConfig(base);
    expect(config.devSignIn).toBe(false);
    expect(config.gatewayUrl).toBe("http://localhost:8000");
  });

  it("refuses weak or inconsistent settings", () => {
    expect(() => parseConfig({ SESSION_SECRET: "short" })).toThrow(/SESSION_SECRET/);
    expect(() => parseConfig({ ...base, DEV_SIGNIN: "true" })).toThrow(/GATEWAY_JWT_SECRET/);
    expect(() => parseConfig({ ...base, GATEWAY_URL: "not a url" })).toThrow(/GATEWAY_URL/);
  });
});

describe("formatting", () => {
  it("shows sub-dollar spend with four decimals", () => {
    expect(formatUsd("0.017107")).toBe("$0.0171");
    expect(formatUsd("0.0042")).toBe("$0.0042");
    expect(formatUsd("0.25")).toBe("$0.2500");
    expect(formatUsd("12.5")).toBe("$12.50");
    expect(formatUsd("0")).toBe("$0.00");
  });

  it("compacts large counts only", () => {
    expect(formatCount(1_284)).toBe("1,284");
    expect(formatCount(12_900)).toBe("12.9K");
  });

  it("describes times relative to now", () => {
    const now = Date.parse("2026-11-25T10:00:00Z");
    expect(formatRelative("2026-11-25T09:55:00Z", now)).toBe("5m ago");
    expect(formatRelative("2026-11-25T10:30:00Z", now)).toBe("in 30m");
    expect(formatRelative(null, now)).toBe("-");
  });

  it("splits qualified tool names", () => {
    expect(formatToolName("crm-ops__update_credit_limit")).toEqual({ server: "crm-ops", tool: "update_credit_limit" });
    expect(formatToolName("plain")).toEqual({ server: "", tool: "plain" });
  });
});
