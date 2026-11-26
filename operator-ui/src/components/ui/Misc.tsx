"use client";

import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { type ReactNode, useState } from "react";

import { cx } from "@/lib/cx";
import { initials } from "@/lib/present";

import { IconArrowRight, IconCheck, IconCopy } from "./Icons";

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1400);
        } catch {
          // Clipboard needs a secure context; the value is still selectable on screen.
        }
      }}
      className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted transition-colors hover:bg-panel-3 hover:text-ink"
      aria-label={copied ? "Copied" : label}
      title={copied ? "Copied" : label}
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={copied ? "done" : "copy"}
          initial={{ scale: 0.5, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.5, opacity: 0 }}
          transition={{ duration: 0.14 }}
          className={cx("inline-flex", copied && "text-good")}
        >
          {copied ? <IconCheck size={13} /> : <IconCopy size={13} />}
        </motion.span>
      </AnimatePresence>
    </button>
  );
}

export function EmptyState({
  icon,
  title,
  children,
  className,
}: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("flex flex-col items-center justify-center px-6 py-10 text-center", className)}>
      <div className="relative mb-4 flex size-11 items-center justify-center rounded-xl bg-panel-2 text-ink-2 shadow-[0_0_0_1px_var(--line-strong)]">
        {icon}
        <span className="absolute -inset-1 rounded-[14px] border border-accent/30 [animation:breathe_3.2s_ease-in-out_infinite]" />
      </div>
      <p className="text-[14px] font-semibold text-ink">{title}</p>
      {children && <div className="mt-1.5 max-w-sm text-[13px] leading-relaxed text-ink-2">{children}</div>}
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="mono inline-flex h-5 min-w-5 items-center justify-center rounded-[5px] border border-line-strong bg-panel-2 px-1 text-[10px] text-ink-2">
      {children}
    </kbd>
  );
}

export function Avatar({ subject, size = 28, ring = false }: { subject: string; size?: number; ring?: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cx(
        "mono inline-flex shrink-0 items-center justify-center rounded-full bg-panel-3 font-medium text-ink-2",
        ring && "shadow-[0_0_0_2px_var(--panel),0_0_0_3.5px_var(--accent)]",
      )}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.36) }}
    >
      {initials(subject)}
    </span>
  );
}

/** "Open approvals ->" style link in panel headers and footers. */
export function MoreLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link
      href={href}
      className="group inline-flex items-center gap-1 text-[12.5px] font-medium text-ink-2 transition-colors hover:text-ink"
    >
      {children}
      <IconArrowRight size={13} className="transition-transform duration-200 group-hover:translate-x-0.5" />
    </Link>
  );
}

/** A small live dot; `live` adds a ripple. */
export function Dot({ tone, live = false, className }: { tone: string; live?: boolean; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cx("inline-block size-1.5 shrink-0 rounded-full", live && "pulse-dot", className)}
      style={{ background: `var(--${tone})`, color: `var(--${tone})` }}
    />
  );
}
