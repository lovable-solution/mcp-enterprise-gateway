import type { ReactNode } from "react";

import { cx } from "@/lib/cx";
import type { ApprovalStatus } from "@/lib/types";

import { IconAlert, IconCheck, IconClock, IconCross, IconPlay, IconStop } from "./Icons";

export type Tone = "accent" | "good" | "warning" | "serious" | "critical" | "muted";

const WASH: Record<Tone, string> = {
  accent: "bg-accent/10",
  good: "bg-good/10",
  warning: "bg-warning/15",
  serious: "bg-serious/15",
  critical: "bg-critical/10",
  muted: "bg-panel-3",
};

export const INK: Record<Tone, string> = {
  accent: "text-accent",
  good: "text-good",
  warning: "text-warning",
  serious: "text-serious",
  critical: "text-critical",
  muted: "text-muted",
};

/** A state label: the colour sits in the glyph and a light wash; the text stays in ink. */
export function Chip({
  tone,
  icon,
  children,
  className,
}: {
  tone: Tone;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cx(
        "inline-flex h-[22px] shrink-0 items-center gap-1.5 rounded-md px-2 text-[11.5px] font-medium whitespace-nowrap text-ink",
        WASH[tone],
        className,
      )}
    >
      {icon && <span className={cx("inline-flex", INK[tone])}>{icon}</span>}
      {children}
    </span>
  );
}

const APPROVAL: Record<ApprovalStatus, { tone: Tone; label: string; icon: ReactNode }> = {
  PENDING_APPROVAL: { tone: "warning", label: "Held", icon: <IconClock size={13} /> },
  APPROVED: { tone: "accent", label: "Approved, running", icon: <IconPlay size={13} /> },
  EXECUTING: { tone: "accent", label: "Running", icon: <IconPlay size={13} /> },
  EXECUTED: { tone: "good", label: "Executed", icon: <IconCheck size={13} /> },
  REJECTED: { tone: "critical", label: "Rejected", icon: <IconCross size={13} /> },
  EXPIRED: { tone: "muted", label: "Expired", icon: <IconStop size={13} /> },
  FAILED: { tone: "serious", label: "Failed", icon: <IconAlert size={13} /> },
};

export function approvalTone(status: ApprovalStatus): Tone {
  return APPROVAL[status].tone;
}

export function ApprovalChip({ status }: { status: ApprovalStatus }) {
  const { tone, label, icon } = APPROVAL[status];
  return (
    <Chip tone={tone} icon={icon}>
      {label}
    </Chip>
  );
}

export function riskTone(risk: string): Tone {
  if (risk === "critical") {
    return "critical";
  }
  if (risk === "high") {
    return "serious";
  }
  return risk === "medium" ? "warning" : "muted";
}

export function RiskChip({ risk }: { risk: string }) {
  return (
    <Chip tone={riskTone(risk)} icon={<IconAlert size={13} />}>
      {risk ? `${risk} risk` : "unrated"}
    </Chip>
  );
}
