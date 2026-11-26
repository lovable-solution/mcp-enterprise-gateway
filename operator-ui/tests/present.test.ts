import { describe, expect, it } from "vitest";

import {
  agentReason,
  argumentLine,
  formatCountdown,
  humanizeKey,
  initials,
  presentArguments,
  presentResult,
  presentValue,
  remainingFraction,
  shortId,
  urgencyOf,
} from "@/lib/present";

const CREDIT_ARGS = {
  reason: "Q4 peak season: raise from $500,000 to $600,000.",
  account_id: "ACC-1001",
  new_limit_usd: 600000,
};

describe("humanizeKey", () => {
  it("turns snake case into a label and lifts the currency into a unit", () => {
    expect(humanizeKey("new_limit_usd")).toBe("New limit (USD)");
    expect(humanizeKey("account_id")).toBe("Account ID");
    expect(humanizeKey("query")).toBe("Query");
  });
});

describe("presentValue", () => {
  it("formats money keys as whole dollars, other numbers with separators", () => {
    expect(presentValue("new_limit_usd", 600000)).toEqual({ text: "$600,000", kind: "money" });
    expect(presentValue("amount", 12.5)).toEqual({ text: "$12.50", kind: "money" });
    expect(presentValue("limit", 25)).toEqual({ text: "25", kind: "number" });
  });

  it("recognises ids by key or by shape", () => {
    expect(presentValue("account_id", "x1").kind).toBe("id");
    expect(presentValue("target", "ACC-1001").kind).toBe("id");
    expect(presentValue("query", "alder").kind).toBe("text");
  });

  it("shows ISO timestamps as dates, and leaves date-like text alone", () => {
    const changed = presentValue("changed_at", "2026-11-26T12:44:16.864001Z");
    expect(changed.kind).toBe("time");
    expect(changed.text).toMatch(/2026/);
    expect(changed.text).not.toContain("T12:44");
    expect(presentValue("note", "2026-11-26").kind).toBe("text");
    expect(presentValue("note", "2026-13-45T99:99Z").kind).toBe("text");
  });

  it("keeps empty values and objects readable", () => {
    expect(presentValue("note", "")).toEqual({ text: "—", kind: "empty" });
    expect(presentValue("flag", true)).toEqual({ text: "Yes", kind: "bool" });
    expect(presentValue("filters", { a: 1 })).toEqual({ text: '{"a":1}', kind: "json" });
  });
});

describe("arguments", () => {
  it("separates the agent's reason from the fields a reviewer checks", () => {
    expect(agentReason(CREDIT_ARGS)).toBe("Q4 peak season: raise from $500,000 to $600,000.");
    expect(presentArguments(CREDIT_ARGS).map((argument) => argument.key)).toEqual(["account_id", "new_limit_usd"]);
    expect(agentReason({ reason: "   " })).toBeNull();
  });

  it("summarises a call on one line", () => {
    expect(argumentLine(CREDIT_ARGS)).toBe("ACC-1001 · $600,000");
    expect(argumentLine({})).toBe("");
  });
});

describe("approval countdown", () => {
  const created = "2026-11-26T12:40:42Z";
  const expires = "2026-11-26T13:10:42Z";

  it("drains from 1 to 0 over the approval window", () => {
    expect(remainingFraction(created, expires, Date.parse(created))).toBe(1);
    expect(remainingFraction(created, expires, Date.parse("2026-11-26T12:55:42Z"))).toBeCloseTo(0.5);
    expect(remainingFraction(created, expires, Date.parse("2026-11-26T14:00:00Z"))).toBe(0);
    expect(remainingFraction(created, "not a date", 0)).toBe(0);
  });

  it("grades urgency", () => {
    expect(urgencyOf(0.8)).toBe("calm");
    expect(urgencyOf(0.3)).toBe("soon");
    expect(urgencyOf(0.1)).toBe("urgent");
  });

  it("formats time left", () => {
    expect(formatCountdown(45_000)).toBe("45s");
    expect(formatCountdown(12 * 60_000 + 5_000)).toBe("12m 05s");
    expect(formatCountdown(65 * 60_000)).toBe("1h 05m");
    expect(formatCountdown(-1)).toBe("0s");
  });
});

describe("people and ids", () => {
  it("makes initials from subjects", () => {
    expect(initials("ops-alice")).toBe("OA");
    expect(initials("m.chen")).toBe("MC");
    expect(initials("root")).toBe("RO");
  });

  it("shortens long ids only", () => {
    expect(shortId("t-0916bba9c2e8496f", 8)).toBe("t-0916bb…");
    expect(shortId("abc", 8)).toBe("abc");
  });
});

describe("presentResult", () => {
  it("reads JSON text content as fields", () => {
    const result = presentResult({
      content: [{ type: "text", text: '{"change_id":"CLC-C864D0DCDE29","new_limit_usd":600000}' }],
    });
    expect(result.fields.map((field) => [field.label, field.text])).toEqual([
      ["Change ID", "CLC-C864D0DCDE29"],
      ["New limit (USD)", "$600,000"],
    ]);
    expect(result.isError).toBe(false);
  });

  it("prefers structured content and keeps plain text as text", () => {
    expect(presentResult({ structuredContent: { ok: true } }).fields[0]?.text).toBe("Yes");
    expect(presentResult({ content: [{ type: "text", text: "done" }], isError: true })).toEqual({
      fields: [],
      text: "done",
      isError: true,
    });
    expect(presentResult(null)).toEqual({ fields: [], text: null, isError: false });
  });
});
