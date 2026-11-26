// Presentation: how tool arguments, people and deadlines read on screen. Pure functions,
// unit-tested; tool arguments are untrusted input and only ever become text.

const MONEY_WHOLE = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});
const MONEY_CENTS = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** The argument agents use to justify a held call; shown as a quote, not a field. */
export const REASON_KEY = "reason";

const ACRONYMS = new Set(["id", "url", "api", "ip", "sla", "sku", "po"]);
const MONEY_KEY = /(^|_)(usd|amount|price|cost|limit_usd)$/i;
const ID_KEY = /(^|_)id$/i;
const ID_VALUE = /^[A-Z]{2,6}-[A-Z0-9]{2,}$/;
const ISO_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$/;
const TIME_FORMAT = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

/** "new_limit_usd" -> "New limit (USD)"; "account_id" -> "Account ID". */
export function humanizeKey(key: string): string {
  const parts = key.split(/[_\-\s]+/).filter(Boolean);
  let unit: string | null = null;
  if (parts.length > 1 && parts.at(-1)?.toLowerCase() === "usd") {
    unit = "USD";
    parts.pop();
  }
  const words = parts.map((part, index) => {
    if (ACRONYMS.has(part.toLowerCase())) {
      return part.toUpperCase();
    }
    const lower = part.toLowerCase();
    return index === 0 ? lower.charAt(0).toUpperCase() + lower.slice(1) : lower;
  });
  const label = words.join(" ") || key;
  return unit ? `${label} (${unit})` : label;
}

export function formatMoney(amount: number): string {
  return Number.isInteger(amount) ? MONEY_WHOLE.format(amount) : MONEY_CENTS.format(amount);
}

export type ValueKind = "money" | "number" | "id" | "time" | "text" | "bool" | "json" | "empty";

export interface PresentedValue {
  text: string;
  kind: ValueKind;
}

export function presentValue(key: string, value: unknown): PresentedValue {
  if (value === null || value === undefined || value === "") {
    return { text: "—", kind: "empty" };
  }
  if (typeof value === "boolean") {
    return { text: value ? "Yes" : "No", kind: "bool" };
  }
  if (typeof value === "number") {
    return MONEY_KEY.test(key)
      ? { text: formatMoney(value), kind: "money" }
      : { text: value.toLocaleString("en-US"), kind: "number" };
  }
  if (typeof value === "string") {
    const at = ISO_TIME.test(value) ? Date.parse(value) : Number.NaN;
    if (Number.isFinite(at)) {
      return { text: TIME_FORMAT.format(at), kind: "time" };
    }
    return ID_KEY.test(key) || ID_VALUE.test(value) ? { text: value, kind: "id" } : { text: value, kind: "text" };
  }
  return { text: JSON.stringify(value), kind: "json" };
}

export interface PresentedArgument extends PresentedValue {
  key: string;
  label: string;
}

/** Every argument except the agent's reason, in the order the agent sent them. */
export function presentArguments(args: Record<string, unknown>): PresentedArgument[] {
  return Object.entries(args)
    .filter(([key]) => key !== REASON_KEY)
    .map(([key, value]) => ({ key, label: humanizeKey(key), ...presentValue(key, value) }));
}

/** The agent's own justification for the call, if it gave one. */
export function agentReason(args: Record<string, unknown>): string | null {
  const reason = args[REASON_KEY];
  return typeof reason === "string" && reason.trim() ? reason.trim() : null;
}

/** One line for lists, e.g. "ACC-1001 · $600,000". */
export function argumentLine(args: Record<string, unknown>, max = 3): string {
  return presentArguments(args)
    .filter((argument) => argument.kind !== "json" && argument.kind !== "empty")
    .slice(0, max)
    .map((argument) => argument.text)
    .join(" · ");
}

/** Share of the approval window still left, 0..1 (0 once expired). */
export function remainingFraction(createdAt: string, expiresAt: string, now: number): number {
  const start = Date.parse(createdAt);
  const end = Date.parse(expiresAt);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return 0;
  }
  return Math.min(1, Math.max(0, (end - now) / (end - start)));
}

export type Urgency = "calm" | "soon" | "urgent";

/** Calm above half the window, soon above a fifth, then urgent. */
export function urgencyOf(fraction: number): Urgency {
  if (fraction > 0.5) {
    return "calm";
  }
  return fraction > 0.2 ? "soon" : "urgent";
}

/** "1h 05m", "12m 30s", "45s"; "0s" once past. */
export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) {
    return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  }
  return `${seconds}s`;
}

/** "ops-alice" -> "OA", "crm-copilot" -> "CC", "m.chen" -> "MC", "root" -> "RO". */
export function initials(subject: string): string {
  const words = subject.split(/[^A-Za-z0-9]+/).filter(Boolean);
  if (words.length >= 2) {
    return `${words[0]?.charAt(0) ?? ""}${words[1]?.charAt(0) ?? ""}`.toUpperCase();
  }
  return (words[0] ?? subject).slice(0, 2).toUpperCase() || "?";
}

/** First characters of an opaque id, for tables; the full id stays in title/copy. */
export function shortId(id: string, size = 8): string {
  return id.length > size + 1 ? `${id.slice(0, size)}…` : id;
}

/** What an executed call returned: structured fields when the upstream sent an object,
 * else its text. */
export function presentResult(result: Record<string, unknown> | null | undefined): {
  fields: PresentedArgument[];
  text: string | null;
  isError: boolean;
} {
  const isError = result?.isError === true;
  if (!result) {
    return { fields: [], text: null, isError };
  }
  const structured = result.structuredContent;
  if (structured && typeof structured === "object" && !Array.isArray(structured)) {
    return { fields: fieldsOf(structured as Record<string, unknown>), text: null, isError };
  }
  const content = Array.isArray(result.content) ? (result.content as unknown[]) : [];
  const text = content
    .map((block) =>
      block && typeof block === "object" && typeof (block as { text?: unknown }).text === "string"
        ? (block as { text: string }).text
        : "",
    )
    .filter(Boolean)
    .join("\n");
  try {
    const parsed: unknown = JSON.parse(text);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return { fields: fieldsOf(parsed as Record<string, unknown>), text: null, isError };
    }
  } catch {
    // Not JSON: show the text as sent.
  }
  return { fields: [], text: text || null, isError };
}

function fieldsOf(record: Record<string, unknown>): PresentedArgument[] {
  return Object.entries(record).map(([key, value]) => ({ key, label: humanizeKey(key), ...presentValue(key, value) }));
}
