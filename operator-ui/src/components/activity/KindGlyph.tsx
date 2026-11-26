import type { ReactNode } from "react";

import type { TrafficKind } from "@/lib/traffic";

import type { Tone } from "../ui/Chip";
import {
  IconActivity,
  IconAlert,
  IconCheck,
  IconClock,
  IconCoins,
  IconCross,
  IconHold,
  IconLoop,
  IconModel,
  IconPass,
  IconStop,
} from "../ui/Icons";

export const KIND: Record<TrafficKind, { tone: Tone; icon: (size: number) => ReactNode; label: string }> = {
  allowed: { tone: "accent", icon: (s) => <IconPass size={s} />, label: "Tool call" },
  model: { tone: "muted", icon: (s) => <IconModel size={s} />, label: "Model usage" },
  held: { tone: "warning", icon: (s) => <IconHold size={s} />, label: "Held" },
  approved: { tone: "accent", icon: (s) => <IconCheck size={s} />, label: "Approved" },
  executed: { tone: "good", icon: (s) => <IconCheck size={s} />, label: "Executed" },
  rejected: { tone: "critical", icon: (s) => <IconCross size={s} />, label: "Rejected" },
  expired: { tone: "muted", icon: (s) => <IconClock size={s} />, label: "Expired" },
  failed: { tone: "serious", icon: (s) => <IconAlert size={s} />, label: "Failed" },
  denied: { tone: "critical", icon: (s) => <IconStop size={s} />, label: "Denied" },
  breaker: { tone: "critical", icon: (s) => <IconLoop size={s} />, label: "Loop stopped" },
  breaker_reset: { tone: "good", icon: (s) => <IconLoop size={s} />, label: "Breaker reset" },
  budget: { tone: "warning", icon: (s) => <IconCoins size={s} />, label: "Budget" },
  other: { tone: "muted", icon: (s) => <IconActivity size={s} />, label: "Event" },
};

/** The kind of an event as a small tinted tile; the label travels with it as a tooltip and
 * for screen readers. */
export function KindGlyph({ kind, size = 26 }: { kind: TrafficKind; size?: number }) {
  const meta = KIND[kind];
  return (
    <span
      role="img"
      aria-label={meta.label}
      title={meta.label}
      className="inline-flex shrink-0 items-center justify-center rounded-md"
      style={{
        width: size,
        height: size,
        color: `var(--${meta.tone === "muted" ? "ink-2" : meta.tone})`,
        background: `color-mix(in oklab, var(--${meta.tone}) 13%, transparent)`,
      }}
    >
      {meta.icon(Math.round(size * 0.52))}
    </span>
  );
}
