import type { Kpis } from "@/lib/dashboard";
import { formatCount, formatSpan, formatUsd } from "@/lib/format";

function Tile({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="rounded-xl border border-hairline bg-surface px-4 py-3">
      <p className="text-sm text-ink-2">{label}</p>
      <p className="mt-1 text-3xl font-semibold tracking-tight">{value}</p>
      <p className="mt-1 text-xs text-muted">{note}</p>
    </div>
  );
}

export function KpiRow({
  kpis,
  oldestPendingSeconds,
  knownSessions,
}: {
  kpis: Kpis;
  oldestPendingSeconds: number | null;
  knownSessions: number;
}) {
  return (
    <section aria-label="Key figures" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Tile
        label="Pending approvals"
        value={formatCount(kpis.pending)}
        note={
          oldestPendingSeconds === null ? "Queue clear" : `Oldest waiting ${formatSpan(oldestPendingSeconds)}`
        }
      />
      <Tile
        label="Active sessions"
        value={formatCount(kpis.activeSessions)}
        note={`Last 15 min · ${formatCount(knownSessions)} in the last 24 h`}
      />
      <Tile
        label="Spend, recent sessions"
        value={formatUsd(kpis.spendUsd)}
        note={
          kpis.burnPerMinute === null || kpis.burnPerMinute === 0
            ? "No spend in the last 5 min"
            : `Burning ${formatUsd(kpis.burnPerMinute)}/min over 5 min`
        }
      />
      <Tile
        label="Open alerts"
        value={formatCount(kpis.openAlerts)}
        note={`${formatCount(kpis.openBreakers)} circuit ${kpis.openBreakers === 1 ? "breaker" : "breakers"} open`}
      />
    </section>
  );
}
