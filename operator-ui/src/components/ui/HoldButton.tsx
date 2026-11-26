"use client";

import { type AnimationPlaybackControls, animate, motion, useMotionValue, useTransform } from "motion/react";
import { type ReactNode, useRef } from "react";

import { cx } from "@/lib/cx";

import { buttonClass } from "./Button";

/** A primary action that runs only after a deliberate press-and-hold (pointer, Space or
 * Enter). Used where one stray click would change a real system. */
export function HoldButton({
  onConfirm,
  children,
  holdMs = 900,
  disabled = false,
  className,
}: {
  onConfirm: () => void;
  children: ReactNode;
  holdMs?: number;
  disabled?: boolean;
  className?: string;
}) {
  const progress = useMotionValue(0);
  const width = useTransform(progress, (value) => `${value * 100}%`);
  const running = useRef<AnimationPlaybackControls | null>(null);
  const confirmed = useRef(false);

  function start() {
    if (disabled || running.current) {
      return;
    }
    confirmed.current = false;
    running.current = animate(progress, 1, {
      duration: (holdMs / 1000) * (1 - progress.get()),
      ease: "linear",
      onComplete: () => {
        running.current = null;
        confirmed.current = true;
        onConfirm();
        animate(progress, 0, { duration: 0.35, delay: 0.25 });
      },
    });
  }

  function cancel() {
    if (confirmed.current) {
      return;
    }
    running.current?.stop();
    running.current = null;
    animate(progress, 0, { duration: 0.22, ease: [0.4, 0, 0.2, 1] });
  }

  return (
    <button
      type="button"
      disabled={disabled}
      onPointerDown={(event) => {
        if (event.button === 0) {
          event.currentTarget.setPointerCapture(event.pointerId);
          start();
        }
      }}
      onPointerUp={cancel}
      onPointerCancel={cancel}
      onKeyDown={(event) => {
        if (event.key === " " || event.key === "Enter") {
          event.preventDefault();
          if (!event.repeat) {
            start();
          }
        }
      }}
      onKeyUp={(event) => {
        if (event.key === " " || event.key === "Enter") {
          event.preventDefault();
          cancel();
        }
      }}
      onBlur={cancel}
      onContextMenu={(event) => event.preventDefault()}
      className={cx(buttonClass("primary", "md"), "relative overflow-hidden touch-none", className)}
    >
      <motion.span aria-hidden="true" className="absolute inset-y-0 left-0 bg-white/25" style={{ width }} />
      <span className="relative inline-flex items-center gap-2">{children}</span>
    </button>
  );
}
