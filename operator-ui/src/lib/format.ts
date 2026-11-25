// Display formatting. Money arrives from the gateway as exact decimal strings.

const usdSmall = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
});
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat("en-US");

export function toNumber(value: string | number | null | undefined): number {
  const parsed = typeof value === "number" ? value : Number.parseFloat(value ?? "");
  return Number.isFinite(parsed) ? parsed : 0;
}

/** $0.0171 under a dollar (agent sessions usually cost cents), else $12.34. */
export function formatUsd(value: string | number | null | undefined): string {
  const amount = toNumber(value);
  return amount !== 0 && Math.abs(amount) < 1 ? usdSmall.format(amount) : usd.format(amount);
}

/** 1,284 / 12.9K / 4.2M */
export function formatCount(value: number): string {
  return Math.abs(value) < 10_000 ? whole.format(value) : compact.format(value);
}

export function formatPercent(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}

/** "42s ago", "5m ago", "3h ago"; "in 12m" for future instants. */
export function formatRelative(iso: string | null | undefined, now: number): string {
  if (!iso) {
    return "-";
  }
  const at = Date.parse(iso);
  if (Number.isNaN(at)) {
    return "-";
  }
  const seconds = Math.round((at - now) / 1000);
  const span = formatSpan(Math.abs(seconds));
  if (Math.abs(seconds) < 5) {
    return "just now";
  }
  return seconds < 0 ? `${span} ago` : `in ${span}`;
}

export function formatSpan(seconds: number): string {
  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }
  if (seconds < 3600) {
    return `${Math.round(seconds / 60)}m`;
  }
  if (seconds < 86_400) {
    return `${Math.round(seconds / 3600)}h`;
  }
  return `${Math.round(seconds / 86_400)}d`;
}

export function formatToolName(name: string): { server: string; tool: string } {
  const [server, ...rest] = name.split("__");
  return rest.length > 0 ? { server: server ?? "", tool: rest.join("__") } : { server: "", tool: name };
}
