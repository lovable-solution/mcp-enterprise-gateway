"use client";

import { motion } from "motion/react";

import { cx } from "@/lib/cx";

import type { Tone } from "./Chip";

/** A value against its cap. The figure beside it carries the number, so nothing depends on
 * the bar's colour or on hover. */
export function Meter({
  value,
  max,
  tone = "accent",
  label,
  className,
}: {
  value: number;
  max: number;
  tone?: Tone;
  label: string;
  className?: string;
}) {
  const share = max > 0 ? Math.min(1, value / max) : 0;
  const width = `${Math.max(share * 100, value > 0 ? 2.5 : 0)}%`;
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      className={cx("relative h-1.5 overflow-hidden rounded-full bg-panel-3", className)}
    >
      <motion.div
        className="absolute inset-y-0 left-0 rounded-full"
        style={{ background: `var(--${tone})` }}
        initial={{ width: 0 }}
        animate={{ width }}
        transition={{ type: "spring", stiffness: 120, damping: 22 }}
      />
    </div>
  );
}
