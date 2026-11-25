"use client";

import type { Alert } from "@/lib/dashboard";
import { formatRelative } from "@/lib/format";
import type { GatewayEvent } from "@/lib/types";

import { AlertIcon, LoopIcon, StopIcon } from "./Icons";
import { Badge } from "./StatusBadge";

const SEVERITY_LABEL = { warning: "Warning", serious: "Serious", critical: "Critical" } as const;

function SeverityBadge({ alert }: { alert: Alert }) {
  const icon = alert.kind === "breaker" ? <LoopIcon /> : alert.kind === "budget_exhausted" ? <StopIcon /> : <AlertIcon />;
  if (alert.resolved) {
    return (
      <Badge tone="muted" icon={icon}>
        Resolved
      </Badge>
    );
  }
  return (
    <Badge tone={alert.severity} icon={icon}>
      {SEVERITY_LABEL[alert.severity]}
    </Badge>
  );
}

export function AlertsPanel({
  alerts,
  canReset,
  onReset,
  onDismiss,
  now,
}: {
  alerts: Alert[];
  canReset: boolean;
  onReset: (sessionId: string) => Promise<string | null>;
  onDismiss: (id: string) => void;
  now: number;
}) {
  const ordered = [...alerts].sort((a, b) => Number(a.resolved) - Number(b.resolved));
  return (
    <section aria-label="Alerts" className="rounded-xl border border-hairline bg-surface">
      <h2 className="border-b border-hairline px-4 py-2.5 text-sm font-semibold">Alerts</h2>
      {ordered.length === 0 ? (
        <p className="px-4 py-6 text-sm text-ink-2">
          No alerts. Loops, exhausted budgets, denials and failed executions appear here.
        </p>
      ) : (
        <ul className="divide-y divide-hairline">
          {ordered.map((alert) => (
            <li key={alert.id} className={`px-4 py-3 ${alert.resolved ? "opacity-60" : ""}`}>
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-medium text-ink">{alert.title}</p>
                <SeverityBadge alert={alert} />
              </div>
              <p className="mt-0.5 text-sm text-ink-2">{alert.detail}</p>
              <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted">
                <span>{formatRelative(alert.at, now)}</span>
                {alert.sessionId && <span className="font-mono">{alert.sessionId}</span>}
                <span className="ml-auto flex gap-2">
                  {alert.kind === "breaker" && !alert.resolved && canReset && alert.sessionId && (
                    <button
                      type="button"
                      onClick={() => alert.sessionId && onReset(alert.sessionId)}
                      className="rounded-md border border-hairline px-2 py-0.5 font-medium text-ink hover:bg-raised"
                    >
                      Reset breaker
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => onDismiss(alert.id)}
                    className="rounded-md px-2 py-0.5 hover:bg-raised"
                  >
                    Dismiss
                  </button>
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function summary(event: GatewayEvent): string {
  const data = event.data;
  const tool = typeof data.tool === "string" ? data.tool.split("__").at(-1) : undefined;
  switch (event.type) {
    case "session.activity": {
      const budget = data.budget as { cost_usd?: string } | undefined;
      const what = typeof data.model === "string" ? data.model : (tool ?? "tool call");
      return `${what}${budget?.cost_usd ? ` · session $${budget.cost_usd}` : ""}`;
    }
    case "tool_call.denied":
      return `${tool ?? ""}: ${String(data.reason ?? "")}`;
    default:
      return tool ?? "";
  }
}

export function EventFeed({ events }: { events: GatewayEvent[] }) {
  return (
    <section aria-label="Live events" className="rounded-xl border border-hairline bg-surface">
      <h2 className="border-b border-hairline px-4 py-2.5 text-sm font-semibold">Live events</h2>
      {events.length === 0 ? (
        <p className="px-4 py-6 text-sm text-ink-2">Waiting for gateway events...</p>
      ) : (
        <ol className="max-h-96 divide-y divide-hairline overflow-y-auto" aria-live="polite">
          {events.slice(0, 40).map((event) => (
            <li key={event.id ?? `${event.type}:${event.occurred_at}`} className="px-4 py-2 text-xs">
              <div className="flex justify-between gap-2">
                <span className="font-medium text-ink">{event.type}</span>
                <span className="figures text-muted">{new Date(event.occurred_at).toLocaleTimeString("en-GB")}</span>
              </div>
              <p className="mt-0.5 truncate text-ink-2">
                {summary(event)}
                {event.session_id && <span className="font-mono text-muted"> · {event.session_id}</span>}
              </p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
