import type { ReactNode } from "react";

import type { ApprovalStatus } from "@/lib/types";

import { AlertIcon, CheckIcon, ClockIcon, CrossIcon, PlayIcon, StopIcon } from "./Icons";

type Tone = "warning" | "good" | "critical" | "serious" | "accent" | "muted";

const TONE_COLOR: Record<Tone, string> = {
  warning: "var(--warning)",
  good: "var(--good)",
  critical: "var(--critical)",
  serious: "var(--serious)",
  accent: "var(--accent)",
  muted: "var(--muted)",
};

/** A tinted pill: the colour sits in the icon and wash; the label stays in ink. */
export function Badge({ tone, icon, children }: { tone: Tone; icon: ReactNode; children: ReactNode }) {
  const color = TONE_COLOR[tone];
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium text-ink"
      style={{ background: `color-mix(in oklab, ${color} 16%, transparent)` }}
    >
      <span style={{ color }} className="inline-flex">
        {icon}
      </span>
      {children}
    </span>
  );
}

const APPROVAL: Record<ApprovalStatus, { tone: Tone; label: string; icon: ReactNode }> = {
  PENDING_APPROVAL: { tone: "warning", label: "Pending", icon: <ClockIcon /> },
  APPROVED: { tone: "accent", label: "Approved, running", icon: <PlayIcon /> },
  EXECUTING: { tone: "accent", label: "Running", icon: <PlayIcon /> },
  EXECUTED: { tone: "good", label: "Executed", icon: <CheckIcon /> },
  REJECTED: { tone: "critical", label: "Rejected", icon: <CrossIcon /> },
  EXPIRED: { tone: "muted", label: "Expired", icon: <StopIcon /> },
  FAILED: { tone: "serious", label: "Failed", icon: <AlertIcon /> },
};

export function ApprovalBadge({ status }: { status: ApprovalStatus }) {
  const { tone, label, icon } = APPROVAL[status];
  return (
    <Badge tone={tone} icon={icon}>
      {label}
    </Badge>
  );
}

export function RiskBadge({ risk }: { risk: string }) {
  const tone: Tone = risk === "high" ? "critical" : risk === "medium" ? "warning" : "muted";
  return (
    <Badge tone={tone} icon={<AlertIcon />}>
      {risk ? `${risk} risk` : "unrated"}
    </Badge>
  );
}
