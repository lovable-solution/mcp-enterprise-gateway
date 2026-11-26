import { cx } from "@/lib/cx";
import { formatCountdown, remainingFraction, urgencyOf } from "@/lib/present";

import type { Tone } from "./Chip";

const URGENCY_TONE: Record<ReturnType<typeof urgencyOf>, Tone> = {
  calm: "accent",
  soon: "warning",
  urgent: "critical",
};

export function countdownTone(createdAt: string, expiresAt: string, now: number): Tone {
  return URGENCY_TONE[urgencyOf(remainingFraction(createdAt, expiresAt, now))];
}

/** Time left before a held call expires (and never runs), as a ring that drains. */
export function CountdownRing({
  createdAt,
  expiresAt,
  now,
  size = 30,
  stroke = 2.5,
  className,
}: {
  createdAt: string;
  expiresAt: string;
  now: number;
  size?: number;
  stroke?: number;
  className?: string;
}) {
  const fraction = remainingFraction(createdAt, expiresAt, now);
  const tone = URGENCY_TONE[urgencyOf(fraction)];
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const left = formatCountdown(Date.parse(expiresAt) - now);
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={`Expires in ${left}`}
      className={cx("shrink-0 -rotate-90", className)}
    >
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--line-strong)" strokeWidth={stroke} />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke={`var(--${tone})`}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - fraction)}
        style={{ transition: "stroke-dashoffset 1s linear, stroke 300ms ease" }}
      />
    </svg>
  );
}
