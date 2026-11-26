"use client";

import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useState } from "react";

import { cx } from "@/lib/cx";

import { type ToastTone, useConsole } from "../console/ConsoleProvider";
import { CommandPalette } from "./CommandPalette";
import { IconAlert, IconArrowRight, IconCheck, IconClock, IconCross, IconMenu, IconPlay } from "../ui/Icons";
import { LogoMark, Wordmark } from "../ui/Logo";
import { LiveStatus, SidebarContent } from "./Sidebar";

const EASE = [0.2, 0.8, 0.2, 1] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [drawer, setDrawer] = useState(false);
  const [palette, setPalette] = useState(false);
  const openPalette = useCallback(() => {
    setDrawer(false);
    setPalette(true);
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-[248px] shrink-0 border-r border-line bg-rail lg:block">
        <div className="sticky top-0 h-screen">
          <SidebarContent onSearch={openPalette} />
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <MobileBar onMenu={() => setDrawer(true)} />
        <GatewayBanner />
        <main id="main" className="min-w-0 flex-1">
          <motion.div
            key={pathname}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.32, ease: EASE }}
            className="mx-auto w-full max-w-[1480px] px-4 py-6 sm:px-6 lg:px-9 lg:py-8"
          >
            {children}
          </motion.div>
        </main>
      </div>

      <AnimatePresence>
        {drawer && (
          <>
            <motion.div
              key="scrim"
              className="fixed inset-0 z-40 bg-[var(--scrim)] lg:hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setDrawer(false)}
            />
            <motion.aside
              key="drawer"
              className="fixed inset-y-0 left-0 z-50 w-[280px] border-r border-line bg-rail lg:hidden"
              initial={{ x: -300 }}
              animate={{ x: 0 }}
              exit={{ x: -300 }}
              transition={{ type: "spring", stiffness: 420, damping: 40 }}
            >
              <SidebarContent onNavigate={() => setDrawer(false)} onSearch={openPalette} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      <CommandPalette open={palette} onClose={() => setPalette(false)} />
      <Toaster />
    </div>
  );
}

function MobileBar({ onMenu }: { onMenu: () => void }) {
  const { pending } = useConsole();
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-rail/90 px-4 backdrop-blur lg:hidden">
      <button
        type="button"
        onClick={onMenu}
        className="inline-flex size-9 items-center justify-center rounded-lg text-ink-2 hover:bg-panel-3"
        aria-label="Open navigation"
      >
        <IconMenu size={18} />
      </button>
      <LogoMark size={24} />
      <Wordmark className="text-[14px]" />
      <span className="ml-auto flex items-center gap-3">
        {pending.length > 0 && (
          <Link
            href="/approvals"
            className="mono inline-flex h-6 items-center gap-1.5 rounded-md bg-warning/15 px-2 text-[10.5px] text-warning-ink"
          >
            <IconClock size={12} /> {pending.length} held
          </Link>
        )}
        <LiveStatus compact />
      </span>
    </header>
  );
}

function GatewayBanner() {
  const { error } = useConsole();
  return (
    <AnimatePresence initial={false}>
      {error && (
        <motion.div
          role="alert"
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          className="overflow-hidden"
        >
          <div className="flex items-center gap-2.5 border-b border-critical/25 bg-critical/10 px-6 py-2.5 text-[13px] text-ink lg:px-9">
            <IconAlert size={15} className="text-critical" />
            <span>
              <span className="font-semibold">The gateway is not answering:</span> {error}. Showing the last known state.
            </span>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

const TOAST_ICON: Record<ToastTone, ReactNode> = {
  accent: <IconPlay size={14} />,
  good: <IconCheck size={14} />,
  warning: <IconClock size={14} />,
  critical: <IconCross size={14} />,
};

function Toaster() {
  const { toasts, dismissToast } = useConsole();
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed right-4 bottom-4 z-[60] flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2"
    >
      <AnimatePresence initial={false}>
        {toasts.map((toast) => (
          <motion.div
            key={toast.id}
            layout
            initial={{ opacity: 0, y: 16, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, x: 40, transition: { duration: 0.18 } }}
            transition={{ type: "spring", stiffness: 420, damping: 34 }}
            className="pointer-events-auto relative overflow-hidden rounded-xl bg-panel p-3.5 pr-9 shadow-[var(--shadow-pop)]"
          >
            <span
              aria-hidden="true"
              className="absolute inset-y-0 left-0 w-[3px]"
              style={{ background: `var(--${toast.tone})` }}
            />
            <div className="flex gap-3">
              <span
                className={cx("mt-px inline-flex size-6 shrink-0 items-center justify-center rounded-md")}
                style={{
                  color: `var(--${toast.tone})`,
                  background: `color-mix(in oklab, var(--${toast.tone}) 14%, transparent)`,
                }}
              >
                {TOAST_ICON[toast.tone]}
              </span>
              <div className="min-w-0">
                <p className="text-[13.5px] font-semibold text-ink">{toast.title}</p>
                {toast.detail && <p className="mt-0.5 text-[12.5px] text-ink-2">{toast.detail}</p>}
                {toast.href && (
                  <Link
                    href={toast.href}
                    onClick={() => dismissToast(toast.id)}
                    className="mt-2 inline-flex items-center gap-1 text-[12.5px] font-medium text-accent hover:text-accent-hover"
                  >
                    Review <IconArrowRight size={12} />
                  </Link>
                )}
              </div>
            </div>
            <button
              type="button"
              onClick={() => dismissToast(toast.id)}
              aria-label="Dismiss"
              className="absolute top-2.5 right-2.5 inline-flex size-6 items-center justify-center rounded-md text-muted hover:bg-panel-3 hover:text-ink"
            >
              <IconCross size={12} />
            </button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
