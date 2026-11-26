"use client";

import { motion } from "motion/react";
import { useId } from "react";

import { cx } from "@/lib/cx";

import type { Tone } from "./Chip";
import { useWidth } from "./useWidth";

/** One series, no axes: the shape of the last few minutes beside a headline figure. The
 * line draws in once; later values just move it. */
export function Sparkline({
  values,
  tone = "accent",
  height = 36,
  label,
  className,
}: {
  values: readonly number[];
  tone?: Tone;
  height?: number;
  label: string;
  className?: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const gradient = `spark-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const data = values.length >= 2 ? values : [values[0] ?? 0, values[0] ?? 0];
  const max = Math.max(...data);
  const min = Math.min(0, ...data);
  const span = max - min || 1;
  const pad = 3;
  const points = data.map((value, index) => ({
    x: (index / (data.length - 1)) * Math.max(width, 1),
    y: height - pad - ((value - min) / span) * (height - pad * 2),
  }));
  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const area = `${line} L${Math.max(width, 1)},${height} L0,${height} Z`;
  const last = points[points.length - 1] ?? { x: 0, y: height };
  const colour = `var(--${tone})`;

  return (
    <div ref={ref} className={cx("relative", className)} style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} className="block overflow-visible">
          <defs>
            <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colour} stopOpacity="0.26" />
              <stop offset="100%" stopColor={colour} stopOpacity="0" />
            </linearGradient>
          </defs>
          <motion.path
            d={area}
            fill={`url(#${gradient})`}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.8, delay: 0.35 }}
          />
          <motion.path
            d={line}
            fill="none"
            stroke={colour}
            strokeWidth={1.6}
            strokeLinecap="round"
            strokeLinejoin="round"
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 1.1, ease: [0.16, 1, 0.3, 1] }}
          />
          <circle cx={last.x} cy={last.y} r={2.5} fill={colour} />
          <circle cx={last.x} cy={last.y} r={5.5} fill={colour} opacity={0.18} />
        </svg>
      )}
    </div>
  );
}
