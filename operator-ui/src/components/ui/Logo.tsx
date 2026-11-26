import { useId } from "react";

import { cx } from "@/lib/cx";

/** The mark: two gate pillars with a token passing between them. While the console is
 * live, the token keeps crossing (CSS only; it rests in the gate under reduced motion). */
export function LogoMark({ size = 28, live = false, className }: { size?: number; live?: boolean; className?: string }) {
  const sheen = `logo-sheen-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 28 28"
      aria-hidden="true"
      focusable="false"
      className={cx("shrink-0", className)}
    >
      <rect x="0.5" y="0.5" width="27" height="27" rx="8" fill="var(--accent)" />
      <rect x="0.5" y="0.5" width="27" height="27" rx="8" fill={`url(#${sheen})`} opacity="0.5" />
      <defs>
        <linearGradient id={sheen} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#fff" stopOpacity="0.35" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d="M10 7.5v13M18 7.5v13" stroke="var(--on-accent)" strokeWidth="2.4" strokeLinecap="round" />
      <circle cx="14" cy="14" r="2.1" fill="var(--on-accent)" className={live ? "logo-token" : undefined} />
    </svg>
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cx("display text-[15px] leading-none tracking-[-0.02em] text-ink", className)}>
      MCP Gateway
    </span>
  );
}
