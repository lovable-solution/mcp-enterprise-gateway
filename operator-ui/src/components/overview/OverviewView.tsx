"use client";

import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { type ReactNode, useMemo, useState } from "react";

import type { Alert } from "@/lib/dashboard";
import { utilisation } from "@/lib/dashboard";
import { cx } from "@/lib/cx";
import { formatCount, formatRelative, formatSpan, formatToolName, formatUsd, toNumber } from "@/lib/format";
import { argumentLine, formatCountdown } from "@/lib/present";
import { bucketize, countTraffic } from "@/lib/traffic";

import { useConsole } from "../console/ConsoleProvider";
import { AnimatedNumber, type NumberFormat } from "../ui/AnimatedNumber";
import { Chip, RiskChip, type Tone } from "../ui/Chip";
import { CountdownRing, countdownTone } from "../ui/CountdownRing";
import {
  IconAlert,
  IconApprovals,
  IconBell,
  IconCheck,
  IconCoins,
  IconLoop,
  IconSessions,
  IconStop,
} from "../ui/Icons";
import { Count, PageHeader, Panel } from "../ui/Layout";
import { Meter } from "../ui/Meter";
import { Avatar, EmptyState, MoreLink } from "../ui/Misc";
import { Sparkline } from "../ui/Sparkline";
import { TrafficGate } from "./TrafficGate";

const WINDOW_MS = 10 * 60_000;
const SPARK_WINDOW_MS = 15 * 60_000;
const MINUTE = 60_000;

export function OverviewView() {
  const { operator, state, now, pending, sessions, policy, stateOf, kpis, ready, live } = useConsole();
  const traffic = useMemo(() => countTraffic(state.events, now, WINDOW_MS), [state.events, now]);
  const buckets = useMemo(() => bucketize(state.events, now, SPARK_WINDOW_MS, MINUTE), [state.events, now]);
  const callsPerMinute = buckets.map((bucket) => bucket.allowed + bucket.held + bucket.denied);
  const deniedPerMinute = buckets.map((bucket) => bucket.denied);
  const spend = state.spend.map((sample) => sample.usd);
  const next = pending.reduce<(typeof pending)[number] | undefined>(
    (soonest, approval) => (!soonest || approval.expires_at < soonest.expires_at ? approval : soonest),
    undefined,
  );
  const oldest = pending[0];
  const denials15 = deniedPerMinute.reduce((sum, value) => sum + value, 0);

  return (
    <>
      <PageHeader
        eyebrow={
          <>
            <span>Tenant {operator.tenant}</span>
            <span className="text-line-strong">/</span>
            <span>{new Date(now).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}</span>
          </>
        }
        title="Overview"
        description="Every tool call your agents make passes the gateway. Watch it live, decide what is held, and stop what loops."
      />

      <Panel
        title="Live traffic"
        meta={<span className="eyebrow hidden sm:inline">last 10 min</span>}
        actions={
          <div className="hidden flex-wrap items-center gap-x-4 gap-y-1 md:flex">
            <Legend glyph={<GlyphDot tone="accent" />} label="Allowed" value={traffic.allowed} />
            <Legend glyph={<GlyphDiamond />} label="Held now" value={pending.length} />
            <Legend glyph={<GlyphCross />} label="Denied" value={traffic.denied} />
            <Legend glyph={<GlyphRing />} label="Model calls" value={traffic.model} />
          </div>
        }
        className="overflow-hidden"
      >
        <TrafficGate
          sessions={sessions}
          events={state.events}
          pending={pending}
          policy={policy}
          now={now}
          stateOf={stateOf}
        />
        <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-line px-4 py-2.5 md:hidden">
          <Legend glyph={<GlyphDot tone="accent" />} label="Allowed" value={traffic.allowed} />
          <Legend glyph={<GlyphDiamond />} label="Held" value={pending.length} />
          <Legend glyph={<GlyphCross />} label="Denied" value={traffic.denied} />
        </div>
      </Panel>

      <section aria-label="Key figures" className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label="Held for review"
          icon={<IconApprovals size={15} />}
          value={kpis.pending}
          tone={kpis.pending > 0 ? "warning" : undefined}
          loading={!ready}
          aside={
            next ? (
              <div className="flex items-center gap-2.5">
                <CountdownRing createdAt={next.created_at} expiresAt={next.expires_at} now={now} size={38} stroke={3} />
              </div>
            ) : undefined
          }
          note={
            next && oldest
              ? `Next expiry in ${formatCountdown(Date.parse(next.expires_at) - now)} · oldest waiting ${formatSpan(
                  Math.max(0, (now - Date.parse(oldest.created_at)) / 1000),
                )}`
              : "Queue clear"
          }
        />
        <KpiCard
          label="Active agents"
          icon={<IconSessions size={15} />}
          value={kpis.activeSessions}
          loading={!ready}
          spark={{ values: callsPerMinute, tone: "accent", label: "Tool calls per minute, last 15 minutes" }}
          note={`${formatCount(callsPerMinute.reduce((sum, value) => sum + value, 0))} calls in 15 min · ${formatCount(sessions.length)} sessions in 24 h`}
        />
        <KpiCard
          label="Spend, recent sessions"
          icon={<IconCoins size={15} />}
          value={kpis.spendUsd}
          format="usd"
          loading={!ready}
          spark={{ values: spend, tone: "accent", label: "Total session spend, last 10 minutes" }}
          note={
            kpis.burnPerMinute === null || kpis.burnPerMinute === 0
              ? "No spend in the last 5 min"
              : `Burning ${formatUsd(kpis.burnPerMinute)}/min over 5 min`
          }
        />
        <KpiCard
          label="Open alerts"
          icon={<IconBell size={15} />}
          value={kpis.openAlerts}
          tone={kpis.openBreakers > 0 ? "critical" : kpis.openAlerts > 0 ? "serious" : undefined}
          loading={!ready}
          spark={{ values: deniedPerMinute, tone: "critical", label: "Denied calls per minute, last 15 minutes" }}
          note={`${formatCount(kpis.openBreakers)} ${kpis.openBreakers === 1 ? "breaker" : "breakers"} open · ${formatCount(denials15)} denied in 15 min`}
        />
      </section>

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <DecisionQueue />
        <AlertsFeed />
      </div>

      <SessionsPreview />

      <p className="mt-8 flex items-center justify-center gap-2 text-[11.5px] text-muted">
        <span className={cx("size-1.5 rounded-full", live === "live" ? "bg-good" : "bg-muted")} />
        {live === "live" ? "Streaming gateway events" : "Waiting for the event stream"} · snapshot refreshes every 30 s
      </p>
    </>
  );
}

function Legend({ glyph, label, value }: { glyph: ReactNode; label: string; value: number }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[12px] text-ink-2">
      {glyph}
      {label}
      <span className="mono text-[11px] font-medium text-ink">{formatCount(value)}</span>
    </span>
  );
}

export function GlyphDot({ tone }: { tone: Tone }) {
  return <span aria-hidden="true" className="size-2 rounded-full" style={{ background: `var(--${tone})` }} />;
}

export function GlyphDiamond() {
  return <span aria-hidden="true" className="size-[7px] rotate-45 rounded-[1px] bg-warning" />;
}

export function GlyphCross() {
  return (
    <svg aria-hidden="true" width="10" height="10" viewBox="0 0 10 10" className="text-critical">
      <path d="M2 2l6 6M8 2L2 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export function GlyphRing() {
  return <span aria-hidden="true" className="size-2 rounded-full border-[1.5px] border-ink-2" />;
}

function KpiCard({
  label,
  icon,
  value,
  format = "count",
  note,
  spark,
  aside,
  tone,
  loading,
}: {
  label: string;
  icon: ReactNode;
  value: number;
  format?: NumberFormat;
  note: string;
  spark?: { values: number[]; tone: Tone; label: string };
  aside?: ReactNode;
  tone?: Tone;
  loading: boolean;
}) {
  return (
    <div className="panel relative overflow-hidden p-4">
      {tone && (
        <span
          aria-hidden="true"
          className="absolute inset-x-0 top-0 h-[2px]"
          style={{ background: `linear-gradient(90deg, var(--${tone}), transparent 85%)` }}
        />
      )}
      <div className="flex items-center justify-between">
        <p className="eyebrow">{label}</p>
        <span className={cx("inline-flex", tone ? "" : "text-muted")} style={tone ? { color: `var(--${tone})` } : undefined}>
          {icon}
        </span>
      </div>
      <div className="mt-4 flex items-end justify-between gap-4">
        {loading ? (
          <span className="skeleton h-9 w-20" />
        ) : (
          <AnimatedNumber value={value} format={format} className="display figures text-[34px] leading-none text-ink" />
        )}
        {spark && <Sparkline values={spark.values} tone={spark.tone} label={spark.label} className="w-[46%] max-w-[150px]" />}
        {aside}
      </div>
      <p className="mt-3 truncate text-[12px] text-ink-2" title={note}>
        {note}
      </p>
    </div>
  );
}

function DecisionQueue() {
  const { pending, decided, now, ready } = useConsole();
  const last = decided[0];
  return (
    <Panel
      title="Needs a decision"
      meta={pending.length > 0 ? <Count tone="warning">{pending.length}</Count> : undefined}
      actions={<MoreLink href="/approvals">Open approvals</MoreLink>}
    >
      {!ready ? (
        <SkeletonRows />
      ) : pending.length === 0 ? (
        <EmptyState icon={<IconCheck size={18} />} title="Queue clear">
          {last ? (
            <>
              Last decision {formatRelative(last.status_changed_at ?? last.decided_at ?? last.created_at, now)}:{" "}
              <span className="font-medium text-ink">{formatToolName(last.tool_name).tool}</span>{" "}
              {last.status.toLowerCase().replace("_", " ")}
              {last.decided_by_subject ? ` by ${last.decided_by_subject}` : ""}.
            </>
          ) : (
            "Held calls appear here the moment an agent makes one."
          )}
        </EmptyState>
      ) : (
        <ul className="divide-y divide-line">
          <AnimatePresence initial={false}>
            {pending.slice(0, 5).map((approval) => {
              const { tool, server } = formatToolName(approval.tool_name);
              return (
                <motion.li
                  key={approval.id}
                  layout
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.28, ease: [0.2, 0.8, 0.2, 1] }}
                >
                  <Link
                    href={`/approvals?id=${approval.id}`}
                    className="group flex items-center gap-4 px-4 py-3.5 transition-colors hover:bg-panel-2"
                  >
                    <CountdownRing createdAt={approval.created_at} expiresAt={approval.expires_at} now={now} />
                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-2 text-[13.5px]">
                        <span className="truncate font-semibold text-ink">{tool}</span>
                        {server && <span className="mono truncate text-[10.5px] text-muted">{server}</span>}
                      </p>
                      <p className="mt-0.5 truncate text-[13px] text-ink-2">{argumentLine(approval.arguments) || "no arguments"}</p>
                      <p className="mt-1 truncate text-[11.5px] text-muted">
                        {approval.requester_subject} · {approval.requester_role} · waiting{" "}
                        {formatSpan(Math.max(0, (now - Date.parse(approval.created_at)) / 1000))}
                      </p>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1.5">
                      <RiskChip risk={approval.risk} />
                      <span
                        className="mono text-[10.5px]"
                        style={{ color: `var(--${countdownTone(approval.created_at, approval.expires_at, now)})` }}
                      >
                        {formatCountdown(Date.parse(approval.expires_at) - now)} left
                      </span>
                    </div>
                  </Link>
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      )}
    </Panel>
  );
}

const SEVERITY_TONE = { warning: "warning", serious: "serious", critical: "critical" } as const;

/** "Loop detected: crm-ops__get_account_metrics" reads as "...: get_account_metrics". */
function shortTools(text: string): string {
  return text.replace(/\b[a-z0-9][a-z0-9-]*__([a-z0-9_]+)/gi, "$1");
}

function AlertGlyph({ alert }: { alert: Alert }) {
  const tone: Tone = alert.resolved ? "muted" : SEVERITY_TONE[alert.severity];
  const icon = alert.kind === "breaker" ? <IconLoop size={14} /> : alert.kind === "budget_exhausted" ? <IconStop size={14} /> : <IconAlert size={14} />;
  return (
    <span
      className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg"
      style={{ color: `var(--${tone})`, background: `color-mix(in oklab, var(--${tone}) 13%, transparent)` }}
    >
      {icon}
    </span>
  );
}

function AlertsFeed() {
  const { state, now, canReset, resetBreaker, dismissAlert } = useConsole();
  const [busy, setBusy] = useState<string | null>(null);
  const alerts = [...state.alerts].sort((a, b) => Number(a.resolved) - Number(b.resolved)).slice(0, 6);
  const open = state.alerts.filter((alert) => !alert.resolved).length;
  return (
    <Panel
      title="Alerts"
      meta={open > 0 ? <Count tone="critical">{open}</Count> : undefined}
      actions={<MoreLink href="/activity">Activity</MoreLink>}
    >
      {alerts.length === 0 ? (
        <EmptyState icon={<IconBell size={18} />} title="All clear">
          Loops, exhausted budgets, denied calls and failed executions appear here as they happen.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-line">
          <AnimatePresence initial={false}>
            {alerts.map((alert) => (
              <motion.li
                key={alert.id}
                layout
                initial={{ opacity: 0, x: 12 }}
                animate={{ opacity: alert.resolved ? 0.55 : 1, x: 0 }}
                exit={{ opacity: 0, height: 0 }}
                className="flex gap-3 px-4 py-3"
              >
                <AlertGlyph alert={alert} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-[13.5px] font-semibold text-ink">{shortTools(alert.title)}</p>
                    {alert.resolved ? (
                      <Chip tone="muted">Resolved</Chip>
                    ) : (
                      <span className="mono shrink-0 text-[10.5px] text-muted">{formatRelative(alert.at, now)}</span>
                    )}
                  </div>
                  <p className="mt-0.5 text-[12.5px] text-ink-2">{shortTools(alert.detail)}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {alert.kind === "breaker" && !alert.resolved && canReset && alert.sessionId && (
                      <button
                        type="button"
                        disabled={busy === alert.id}
                        onClick={async () => {
                          setBusy(alert.id);
                          if (alert.sessionId) {
                            await resetBreaker(alert.sessionId);
                          }
                          setBusy(null);
                        }}
                        className="inline-flex h-7 items-center gap-1.5 rounded-md bg-panel-2 px-2.5 text-[12px] font-medium text-ink shadow-[0_0_0_1px_var(--line-strong)] hover:bg-panel-3 disabled:opacity-60"
                      >
                        <IconLoop size={12} /> {busy === alert.id ? "Resetting…" : "Reset breaker"}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => dismissAlert(alert.id)}
                      className="inline-flex h-7 items-center rounded-md px-2 text-[12px] text-muted hover:bg-panel-3 hover:text-ink"
                    >
                      Dismiss
                    </button>
                  </div>
                </div>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      )}
    </Panel>
  );
}

function SessionsPreview() {
  const { sessions, now, stateOf, ready } = useConsole();
  const top = sessions.slice(0, 5);
  return (
    <Panel
      title="Busiest sessions"
      meta={<span className="eyebrow hidden sm:inline">by last activity</span>}
      actions={<MoreLink href="/sessions">All sessions</MoreLink>}
      className="mt-5"
    >
      {!ready ? (
        <SkeletonRows />
      ) : top.length === 0 ? (
        <EmptyState icon={<IconSessions size={18} />} title="No agent sessions yet">
          Sessions appear when an agent makes its first call through the gateway.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-line">
          {top.map((status) => {
            const { session, budget, circuit_breaker: breaker } = status;
            const budgetState = stateOf(status);
            const tone: Tone = budgetState === "exhausted" ? "critical" : budgetState === "warning" ? "warning" : "accent";
            const active = now - Date.parse(session.last_seen) < 60_000;
            return (
              <li key={session.session_id}>
                <Link
                  href={`/sessions?id=${encodeURIComponent(session.session_id)}`}
                  className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2.5 px-4 py-3 transition-colors hover:bg-panel-2 md:grid-cols-[minmax(0,1.2fr)_minmax(0,1.5fr)_7rem_8rem]"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <Avatar subject={session.subject} size={30} />
                    <div className="min-w-0">
                      <p className="flex items-center gap-2 truncate text-[13.5px] font-semibold text-ink">
                        {session.subject}
                        {active && <span className="pulse-dot size-1.5 rounded-full bg-good text-good" aria-label="active now" />}
                      </p>
                      <p className="mono truncate text-[10.5px] text-muted">{session.role}</p>
                    </div>
                  </div>
                  <div className="order-3 col-span-2 min-w-0 md:order-none md:col-span-1">
                    <div className="mb-1.5 flex items-baseline justify-between gap-2 text-[12px]">
                      <span className="figures text-ink">{formatUsd(budget.cost_usd)}</span>
                      <span className="figures text-muted">of {formatUsd(budget.max_cost_usd)}</span>
                    </div>
                    <Meter
                      value={toNumber(budget.cost_usd)}
                      max={toNumber(budget.max_cost_usd)}
                      tone={tone}
                      label={`Spend ${formatUsd(budget.cost_usd)} of ${formatUsd(budget.max_cost_usd)}`}
                    />
                  </div>
                  <p className="figures hidden text-right text-[12.5px] text-ink-2 md:block">
                    {formatCount(budget.tool_calls)} <span className="text-muted">calls</span>
                    <span className="block text-[11px] text-muted">{Math.round(utilisation(budget) * 100)}% of budget</span>
                  </p>
                  <div className="order-2 flex justify-end md:order-none">
                    {breaker.open ? (
                      <Chip tone="critical" icon={<IconLoop size={12} />}>
                        Loop stopped
                      </Chip>
                    ) : (
                      <span className="mono text-[10.5px] text-muted">{formatRelative(session.last_seen, now)}</span>
                    )}
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

function SkeletonRows() {
  return (
    <div className="flex flex-col gap-4 p-4">
      {[0, 1, 2].map((row) => (
        <div key={row} className="flex items-center gap-4">
          <span className="skeleton size-8 rounded-full" />
          <div className="flex flex-1 flex-col gap-2">
            <span className="skeleton h-3 w-2/5" />
            <span className="skeleton h-3 w-3/5" />
          </div>
        </div>
      ))}
    </div>
  );
}
