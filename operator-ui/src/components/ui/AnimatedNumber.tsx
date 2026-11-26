"use client";

import { animate, motion, useMotionValue, useReducedMotion, useTransform } from "motion/react";
import { useEffect } from "react";

import { formatCount, formatUsd } from "@/lib/format";

const FORMATS = {
  count: (value: number) => formatCount(Math.round(value)),
  usd: (value: number) => formatUsd(value),
} as const;

export type NumberFormat = keyof typeof FORMATS;

/** A figure that rolls to each new value. Screen readers get the final value only. */
export function AnimatedNumber({
  value,
  format = "count",
  className,
}: {
  value: number;
  format?: NumberFormat;
  className?: string;
}) {
  const reduce = useReducedMotion();
  const current = useMotionValue(0);
  const text = useTransform(current, FORMATS[format]);

  useEffect(() => {
    if (reduce) {
      current.jump(value);
      return;
    }
    const controls = animate(current, value, { duration: 0.9, ease: [0.16, 1, 0.3, 1] });
    return () => controls.stop();
  }, [value, reduce, current]);

  return (
    <span className={className}>
      <motion.span aria-hidden="true">{text}</motion.span>
      <span className="sr-only">{FORMATS[format](value)}</span>
    </span>
  );
}
