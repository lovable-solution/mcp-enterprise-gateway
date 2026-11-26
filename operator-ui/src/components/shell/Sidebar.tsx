"use client";

import { motion } from "motion/react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ComponentType, useSyncExternalStore } from "react";

import { cx } from "@/lib/cx";
import { THEME_CHOICES, type ThemeChoice, applyTheme, readTheme } from "@/lib/theme";

import { type LiveState, useConsole } from "../console/ConsoleProvider";
import {
  IconActivity,
  IconApprovals,
  IconMonitor,
  IconMoon,
  IconOverview,
  IconPolicy,
  type IconProps,
  IconSearch,
  IconSessions,
  IconSignOut,
  IconSun,
  IconTenant,
} from "../ui/Icons";
import { LogoMark, Wordmark } from "../ui/Logo";
import { Avatar, Kbd } from "../ui/Misc";

export interface NavItem {
  href: string;
  label: string;
  icon: ComponentType<IconProps>;
}

export const NAV: readonly NavItem[] = [
  { href: "/", label: "Overview", icon: IconOverview },
  { href: "/approvals", label: "Approvals", icon: IconApprovals },
  { href: "/sessions", label: "Sessions", icon: IconSessions },
  { href: "/activity", label: "Activity", icon: IconActivity },
  { href: "/policy", label: "Policy", icon: IconPolicy },
];

export function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

const LIVE: Record<LiveState, { label: string; tone: string }> = {
  connecting: { label: "Connecting", tone: "muted" },
  live: { label: "Live", tone: "good" },
  reconnecting: { label: "Reconnecting", tone: "warning" },
  offline: { label: "Offline, retrying", tone: "critical" },
};

/** Three bars that pulse while the event stream is open. */
export function SignalBars({ state }: { state: LiveState }) {
  const tone = LIVE[state].tone;
  return (
    <span aria-hidden="true" className="inline-flex h-3 items-end gap-[2px]">
      {[0, 1, 2].map((bar) => (
        <span
          key={bar}
          className="w-[3px] origin-bottom rounded-[1px]"
          style={{
            height: `${5 + bar * 3.5}px`,
            background: `var(--${tone})`,
            animation: state === "live" ? `signal 1.4s ease-in-out ${bar * 0.18}s infinite` : undefined,
            opacity: state === "connecting" ? 0.5 : 1,
          }}
        />
      ))}
    </span>
  );
}

export function LiveStatus({ compact = false }: { compact?: boolean }) {
  const { live } = useConsole();
  return (
    <span className="inline-flex items-center gap-2 text-[12.5px] text-ink-2" role="status">
      <SignalBars state={live} />
      {!compact && LIVE[live].label}
      {compact && <span className="sr-only">{LIVE[live].label}</span>}
    </span>
  );
}

const subscribeTheme = (onChange: () => void) => {
  window.addEventListener("console-theme", onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener("console-theme", onChange);
    window.removeEventListener("storage", onChange);
  };
};

const THEME_ICON: Record<ThemeChoice, ComponentType<IconProps>> = {
  system: IconMonitor,
  light: IconSun,
  dark: IconMoon,
};

export function ThemeSwitch() {
  const theme = useSyncExternalStore<ThemeChoice>(subscribeTheme, readTheme, () => "system");
  return (
    <div role="radiogroup" aria-label="Theme" className="flex rounded-lg bg-panel-3/70 p-0.5">
      {THEME_CHOICES.map((choice) => {
        const Icon = THEME_ICON[choice];
        const selected = theme === choice;
        return (
          <button
            key={choice}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={`${choice[0]?.toUpperCase()}${choice.slice(1)} theme`}
            title={`${choice[0]?.toUpperCase()}${choice.slice(1)} theme`}
            onClick={() => applyTheme(choice)}
            className={cx(
              "relative flex h-6 w-7 items-center justify-center rounded-md transition-colors",
              selected ? "text-ink" : "text-muted hover:text-ink-2",
            )}
          >
            {selected && (
              <motion.span
                layoutId="theme-active"
                className="absolute inset-0 rounded-md bg-panel shadow-[var(--shadow-panel)]"
                transition={{ type: "spring", stiffness: 520, damping: 38 }}
              />
            )}
            <Icon size={13} className="relative" />
          </button>
        );
      })}
    </div>
  );
}

export function SidebarContent({ onNavigate, onSearch }: { onNavigate?: () => void; onSearch: () => void }) {
  const pathname = usePathname();
  const { operator, pending, kpis, live, policy, signOut } = useConsole();
  const badges: Record<string, { text: string; tone: "warning" | "muted" } | undefined> = {
    "/approvals": pending.length > 0 ? { text: String(pending.length), tone: "warning" } : undefined,
    "/sessions": kpis.activeSessions > 0 ? { text: String(kpis.activeSessions), tone: "muted" } : undefined,
  };
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 px-5 pt-5 pb-4">
        <LogoMark live={live === "live"} />
        <div className="min-w-0">
          <Wordmark />
          <p className="eyebrow mt-1.5">Operator console</p>
        </div>
      </div>

      <div className="px-3">
        <button
          type="button"
          onClick={onSearch}
          className="flex h-9 w-full items-center gap-2.5 rounded-lg bg-panel px-3 text-[13px] text-muted shadow-[var(--shadow-panel)] transition-colors hover:text-ink-2"
        >
          <IconSearch size={14} />
          <span className="flex-1 text-left">Search or jump to</span>
          <Kbd>⌘K</Kbd>
        </button>
      </div>

      <nav aria-label="Console" className="mt-4 px-3">
        <p className="eyebrow px-3 pb-2">Workspace</p>
        <ul className="flex flex-col gap-0.5">
          {NAV.map((item) => {
            const active = isActive(pathname, item.href);
            const badge = badges[item.href];
            const Icon = item.icon;
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  onClick={onNavigate}
                  aria-current={active ? "page" : undefined}
                  className={cx(
                    "group relative flex h-9 items-center gap-3 rounded-lg px-3 text-[13.5px] font-medium transition-colors",
                    active ? "text-ink" : "text-ink-2 hover:text-ink",
                  )}
                >
                  {active && (
                    <motion.span
                      layoutId="nav-active"
                      className="absolute inset-0 rounded-lg bg-panel shadow-[var(--shadow-panel)]"
                      transition={{ type: "spring", stiffness: 480, damping: 40 }}
                    />
                  )}
                  {!active && (
                    <span className="absolute inset-0 rounded-lg bg-panel-3/0 transition-colors group-hover:bg-panel-3/60" />
                  )}
                  <Icon size={16} className={cx("relative", active ? "text-accent" : "text-muted group-hover:text-ink-2")} />
                  <span className="relative">{item.label}</span>
                  {badge && (
                    <span
                      className={cx(
                        "mono relative ml-auto inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-[5px] px-1 text-[10px]",
                        badge.tone === "warning" ? "bg-warning/15 text-warning-ink" : "bg-panel-3 text-ink-2",
                      )}
                    >
                      {badge.text}
                    </span>
                  )}
                  {item.href === "/activity" && live === "live" && (
                    <span className="relative ml-auto size-1.5 rounded-full bg-good" aria-hidden="true" />
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="mt-auto flex flex-col gap-3 px-3 pb-4">
        <div className="rounded-xl bg-panel p-3 shadow-[var(--shadow-panel)]">
          <div className="flex items-center gap-2 text-[12.5px]">
            <IconTenant size={14} className="text-muted" />
            <span className="text-ink-2">Tenant</span>
            <span className="mono ml-auto text-[11px] text-ink">{operator.tenant}</span>
          </div>
          <div className="mt-2 flex items-center gap-2 text-[12.5px]">
            <IconPolicy size={14} className="text-muted" />
            <span className="text-ink-2">Policy</span>
            <span className="mono ml-auto truncate text-[11px] text-ink" title={policy?.digest}>
              {policy ? policy.name : "…"}
            </span>
          </div>
          <div className="mt-2.5 flex items-center justify-between border-t border-line pt-2.5">
            <LiveStatus />
            <span className="mono text-[10px] text-muted">SSE</span>
          </div>
        </div>

        <div className="flex items-center gap-2.5 px-1">
          <Avatar subject={operator.subject} size={30} ring />
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-semibold text-ink">{operator.subject}</p>
            <p className="mono truncate text-[10.5px] text-muted">{operator.role}</p>
          </div>
          <button
            type="button"
            onClick={() => void signOut()}
            className="inline-flex size-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-panel-3 hover:text-ink"
            aria-label="Sign out"
            title="Sign out"
          >
            <IconSignOut size={15} />
          </button>
        </div>
        <div className="flex items-center justify-between px-1">
          <span className="text-[12px] text-muted">Theme</span>
          <ThemeSwitch />
        </div>
      </div>
    </div>
  );
}
