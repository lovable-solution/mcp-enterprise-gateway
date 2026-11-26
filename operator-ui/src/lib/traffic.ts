// Live traffic: what each gateway event means as a call through the gate. Pure functions,
// unit-tested; the gate animation, the activity log and the histograms all read from here.

import { formatToolName, toNumber } from "./format";
import type { GatewayEvent } from "./types";

export type TrafficKind =
  | "allowed"
  | "model"
  | "held"
  | "approved"
  | "executed"
  | "rejected"
  | "expired"
  | "failed"
  | "denied"
  | "breaker"
  | "breaker_reset"
  | "budget"
  | "other";

export function classify(event: GatewayEvent): TrafficKind {
  switch (event.type) {
    case "session.activity":
      return typeof event.data.model === "string" ? "model" : "allowed";
    case "approval.requested":
      return "held";
    case "approval.approved":
      return "approved";
    case "approval.executed":
      return "executed";
    case "approval.rejected":
      return "rejected";
    case "approval.expired":
      return "expired";
    case "approval.failed":
      return "failed";
    case "tool_call.denied":
      return "denied";
    case "circuit_breaker.tripped":
      return "breaker";
    case "circuit_breaker.reset":
      return "breaker_reset";
    case "budget.warning":
    case "budget.exhausted":
      return "budget";
    default:
      return "other";
  }
}

/** The four outcomes the gate shows, plus everything else. */
export type Outcome = "allowed" | "held" | "denied" | "model" | "other";

export function outcomeOf(kind: TrafficKind): Outcome {
  switch (kind) {
    case "allowed":
    case "held":
    case "denied":
    case "model":
      return kind;
    case "breaker":
      return "denied";
    default:
      return "other";
  }
}

export function toolOf(event: GatewayEvent): string | null {
  return typeof event.data.tool === "string" && event.data.tool ? event.data.tool : null;
}

/** The upstream server a tool belongs to ("crm-ops" for "crm-ops__search_accounts"). */
export function serverOf(tool: string | null): string | null {
  if (!tool) {
    return null;
  }
  const { server } = formatToolName(tool);
  return server || null;
}

export interface TrafficCounts {
  allowed: number;
  held: number;
  denied: number;
  model: number;
}

/** Calls through the gate in the last `windowMs`, by outcome. */
export function countTraffic(events: readonly GatewayEvent[], now: number, windowMs: number): TrafficCounts {
  const counts: TrafficCounts = { allowed: 0, held: 0, denied: 0, model: 0 };
  for (const event of events) {
    if (now - Date.parse(event.occurred_at) > windowMs) {
      continue;
    }
    const outcome = outcomeOf(classify(event));
    if (outcome !== "other") {
      counts[outcome] += 1;
    }
  }
  return counts;
}

export interface Bucket extends TrafficCounts {
  start: number;
  other: number;
}

/** Events per time bucket over the last `windowMs`, oldest bucket first. */
export function bucketize(
  events: readonly GatewayEvent[],
  now: number,
  windowMs: number,
  bucketMs: number,
): Bucket[] {
  const count = Math.max(1, Math.round(windowMs / bucketMs));
  const first = now - count * bucketMs;
  const buckets: Bucket[] = Array.from({ length: count }, (_, index) => ({
    start: first + index * bucketMs,
    allowed: 0,
    held: 0,
    denied: 0,
    model: 0,
    other: 0,
  }));
  for (const event of events) {
    const at = Date.parse(event.occurred_at);
    const index = Math.floor((at - first) / bucketMs);
    const bucket = buckets[index];
    if (bucket && at <= now) {
      bucket[outcomeOf(classify(event))] += 1;
    }
  }
  return buckets;
}

export interface Described {
  event: GatewayEvent;
  kind: TrafficKind;
  title: string;
  detail: string;
  tool: string | null;
  /** USD the event added to its session (session.activity only, when the previous
   * snapshot of that session is in view). */
  costDelta: number | null;
}

function text(value: unknown): string {
  return value === undefined || value === null ? "" : String(value);
}

function shortTool(tool: string | null): string {
  return tool ? formatToolName(tool).tool : "tool call";
}

export function describe(event: GatewayEvent): Omit<Described, "costDelta"> {
  const data = event.data;
  const kind = classify(event);
  const tool = toolOf(event);
  const name = shortTool(tool);
  const subject = text(data.subject);
  const requester = (data.requester ?? {}) as { subject?: string; role?: string };
  const base = { event, kind, tool };
  switch (kind) {
    case "allowed":
      return { ...base, title: name, detail: [subject, serverOf(tool)].filter(Boolean).join(" · ") };
    case "model":
      return { ...base, title: text(data.model), detail: `${subject ? `${subject} · ` : ""}model usage` };
    case "held":
      return {
        ...base,
        title: `${name} held for review`,
        detail: requester.subject ? `requested by ${requester.subject}${requester.role ? ` (${requester.role})` : ""}` : "",
      };
    case "approved":
      return { ...base, title: `${name} approved`, detail: data.decided_by ? `by ${text(data.decided_by)}` : "" };
    case "executed":
      return { ...base, title: `${name} executed`, detail: "the approved payload ran once" };
    case "rejected":
      return {
        ...base,
        title: `${name} rejected`,
        detail: [data.decided_by ? `by ${text(data.decided_by)}` : "", text(data.decision_reason)]
          .filter(Boolean)
          .join(": "),
      };
    case "expired":
      return { ...base, title: `${name} expired`, detail: "nobody decided in time; it never ran" };
    case "failed":
      return { ...base, title: `${name} failed`, detail: text(data.error) };
    case "denied":
      return {
        ...base,
        title: `${name} denied`,
        detail: [subject && data.role ? `${subject} (${text(data.role)})` : subject, text(data.reason)]
          .filter(Boolean)
          .join(": "),
      };
    case "breaker":
      return {
        ...base,
        title: `Loop stopped on ${name}`,
        detail: `${text(data.identical_calls) || "repeated"} identical calls in ${text(data.window_seconds) || "?"}s`,
      };
    case "breaker_reset":
      return { ...base, title: "Circuit breaker reset", detail: subject };
    case "budget":
      return event.type === "budget.exhausted"
        ? {
            ...base,
            title: "Budget exhausted",
            detail: `${subject ? `${subject} hit ` : ""}${text(data.limit).replace("max_", "the ").replaceAll("_", " ")} cap`,
          }
        : { ...base, title: "Budget warning", detail: `${subject ? `${subject} · ` : ""}most of the session budget used` };
    default:
      return { ...base, title: event.type, detail: "" };
  }
}

/** Describes events (newest first, as the console stores them) and works out what each
 * activity event cost, from consecutive budget snapshots of the same session. */
export function describeAll(events: readonly GatewayEvent[]): Described[] {
  const lastCost = new Map<string, number>();
  const deltas = new Map<GatewayEvent, number>();
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (!event || event.type !== "session.activity" || event.session_id === null) {
      continue;
    }
    const budget = event.data.budget as { cost_usd?: string } | undefined;
    if (budget?.cost_usd === undefined) {
      continue;
    }
    const cost = toNumber(budget.cost_usd);
    const previous = lastCost.get(event.session_id);
    if (previous !== undefined) {
      deltas.set(event, Math.max(0, cost - previous));
    }
    lastCost.set(event.session_id, cost);
  }
  return events.map((event) => ({ ...describe(event), costDelta: deltas.get(event) ?? null }));
}
