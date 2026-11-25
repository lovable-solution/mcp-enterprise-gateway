import type { Operator } from "@/lib/types";

export type LiveState = "connecting" | "live" | "reconnecting" | "offline";

const LIVE: Record<LiveState, { color: string; label: string }> = {
  connecting: { color: "var(--muted)", label: "Connecting" },
  live: { color: "var(--good)", label: "Live" },
  reconnecting: { color: "var(--warning)", label: "Reconnecting" },
  offline: { color: "var(--critical)", label: "Offline, retrying" },
};

export function Header({
  operator,
  live,
  onSignOut,
}: {
  operator: Operator;
  live: LiveState;
  onSignOut: () => void;
}) {
  const status = LIVE[live];
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 py-5">
      <div>
        <p className="text-xs text-muted">MCP Enterprise Gateway · tenant {operator.tenant}</p>
        <h1 className="text-xl font-semibold">Operator console</h1>
      </div>
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <span className="inline-flex items-center gap-2 text-ink-2" role="status">
          <span
            aria-hidden="true"
            className={`h-2 w-2 rounded-full ${live === "live" ? "animate-pulse" : ""}`}
            style={{ background: status.color }}
          />
          {status.label}
        </span>
        <span className="text-ink-2">
          <span className="font-medium text-ink">{operator.subject}</span> · {operator.role}
        </span>
        <button
          type="button"
          onClick={onSignOut}
          className="rounded-md border border-hairline px-3 py-1.5 text-sm hover:bg-raised"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}
