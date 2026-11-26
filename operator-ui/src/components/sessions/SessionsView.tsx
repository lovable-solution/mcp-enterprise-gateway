"use client";

import { AnimatePresence, motion } from "motion/react";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { type BudgetState, utilisation } from "@/lib/dashboard";
import { cx } from "@/lib/cx";
import { formatCount, formatPercent, formatRelative, formatUsd, toNumber } from "@/lib/format";
import { shortId } from "@/lib/present";
import { describeAll } from "@/lib/traffic";
import type { SessionStatus } from "@/lib/types";

import { useConsole } from "../console/ConsoleProvider";
import { Chip, type Tone } from "../ui/Chip";
import { IconChevronDown, IconLoop, IconSearch, IconSessions, IconStop } from "../ui/Icons";
import { Count, PageHeader } from "../ui/Layout";
import { Meter } from "../ui/Meter";
import { Avatar, CopyButton, EmptyState } from "../ui/Misc";
import { KindGlyph } from "../activity/KindGlyph";

type Filter = "all" | "active" | "attention";

const STATE_TONE: Record<BudgetState, Tone> = { ok: "accent", warning: "warning", exhausted: "critical" };
const ACTIVE_MS = 15 * 60_000;

export function SessionsView() {
  const params = useSearchParams();
  const { sessions, now, stateOf, ready } = useConsole();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const requested = params.get("id");
  const [open, setOpen] = useState<string | null>(requested);

  useEffect(() => {
    if (requested) {
      setOpen(requested);
      setFilter("all");
    }
  }, [requested]);

  const counts = useMemo(
    () => ({
      all: sessions.length,
      active: sessions.filter((s) => now - Date.parse(s.session.last_seen) <= ACTIVE_MS).length,
      attention: sessions.filter((s) => s.circuit_breaker.open || stateOf(s) !== "ok").length,
    }),
    [sessions, now, stateOf],
  );

  const needle = query.trim().toLowerCase();
  const visible = sessions.filter((status) => {
    if (filter === "active" && now - Date.parse(status.session.last_seen) > ACTIVE_MS) {
      return false;
    }
    if (filter === "attention" && !(status.circuit_breaker.open || stateOf(status) !== "ok")) {
      return false;
    }
    if (!needle) {
      return true;
    }
    const { subject, role, session_id } = status.session;
    return `${subject} ${role} ${session_id}`.toLowerCase().includes(needle);
  });

  return (
    <>
      <PageHeader
        eyebrow={
          <>
            <IconSessions size={12} /> Budgets and loops
          </>
        }
        title="Sessions"
        description="Every agent session from the last 24 hours, with what it has spent against its caps. A session whose breaker is open cannot call tools until it cools down or you reset it."
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div role="tablist" aria-label="Filter sessions" className="flex rounded-[10px] bg-panel p-1 shadow-[var(--shadow-panel)]">
          {(
            [
              ["all", "All"],
              ["active", "Active"],
              ["attention", "Needs attention"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={filter === value}
              onClick={() => setFilter(value)}
              className={cx(
                "relative flex h-8 items-center gap-2 rounded-lg px-3 text-[13px] font-medium transition-colors",
                filter === value ? "text-ink" : "text-muted hover:text-ink-2",
              )}
            >
              {filter === value && (
                <motion.span
                  layoutId="session-filter"
                  className="absolute inset-0 rounded-lg bg-panel-3"
                  transition={{ type: "spring", stiffness: 500, damping: 38 }}
                />
              )}
              <span className="relative">{label}</span>
              <span className="relative">
                <Count tone={value === "attention" && counts.attention > 0 ? "critical" : "muted"}>{counts[value]}</Count>
              </span>
            </button>
          ))}
        </div>
        <label className="ml-auto flex h-10 w-full items-center gap-2 rounded-[10px] bg-panel px-3 shadow-[var(--shadow-panel)] sm:w-72">
          <IconSearch size={14} className="text-muted" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Agent, role or session id"
            className="h-full flex-1 bg-transparent text-[13.5px] text-ink outline-none placeholder:text-muted"
            aria-label="Search sessions"
          />
        </label>
      </div>

      <section className="panel overflow-hidden" aria-label="Agent sessions">
        <div className="hidden grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.4fr)_9rem_1.5rem] gap-4 border-b border-line px-5 py-2.5 lg:grid">
          {["Agent", "Last active", "Tool calls", "Tokens", "Spend against cap", "Breaker", ""].map((heading, index) => (
            <span key={index} className="eyebrow">
              {heading}
            </span>
          ))}
        </div>
        {!ready ? (
          <div className="flex flex-col gap-4 p-5">
            {[0, 1, 2].map((row) => (
              <span key={row} className="skeleton h-12 w-full" />
            ))}
          </div>
        ) : visible.length === 0 ? (
          <EmptyState icon={<IconSessions size={18} />} title={sessions.length === 0 ? "No agent sessions yet" : "No sessions match"}>
            {sessions.length === 0
              ? "Sessions appear when an agent makes its first call through the gateway. Ledgers last 24 hours."
              : "Change the filter or the search to see more."}
          </EmptyState>
        ) : (
          <ul className="divide-y divide-line">
            {visible.map((status) => (
              <SessionRow
                key={status.session.session_id}
                status={status}
                budgetState={stateOf(status)}
                now={now}
                open={open === status.session.session_id}
                focus={requested === status.session.session_id}
                onToggle={() =>
                  setOpen((current) => (current === status.session.session_id ? null : status.session.session_id))
                }
              />
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

function SessionRow({
  status,
  budgetState,
  now,
  open,
  focus,
  onToggle,
}: {
  status: SessionStatus;
  budgetState: BudgetState;
  now: number;
  open: boolean;
  focus: boolean;
  onToggle: () => void;
}) {
  const { canReset, resetBreaker } = useConsole();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const row = useRef<HTMLLIElement>(null);
  const { session, budget, circuit_breaker: breaker } = status;
  const tone = STATE_TONE[budgetState];
  const recent = now - Date.parse(session.last_seen) < 60_000;

  useEffect(() => {
    if (focus) {
      row.current?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [focus]);

  async function reset() {
    setBusy(true);
    setError(await resetBreaker(session.session_id));
    setBusy(false);
  }

  return (
    <li ref={row} className={cx("transition-colors", open && "bg-panel-2/60")}>
      <div
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={onToggle}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onToggle();
          }
        }}
        className="grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-3 px-5 py-3.5 hover:bg-panel-2/60 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.4fr)_9rem_1.5rem]"
      >
        <div className="flex min-w-0 items-center gap-3">
          <Avatar subject={session.subject} size={32} />
          <div className="min-w-0">
            <p className="flex items-center gap-2 truncate text-[13.5px] font-semibold text-ink">
              {session.subject}
              <Chip tone="muted" className="h-[18px] px-1.5 text-[10.5px]">
                {session.role}
              </Chip>
            </p>
            <p className="mono mt-0.5 flex items-center gap-1 truncate text-[10.5px] text-muted" title={session.session_id}>
              {shortId(session.session_id, 18)}
            </p>
          </div>
        </div>

        <p className="hidden items-center gap-2 text-[12.5px] text-ink-2 lg:flex">
          {recent && <span className="pulse-dot size-1.5 rounded-full bg-good text-good" aria-hidden="true" />}
          {formatRelative(session.last_seen, now)}
        </p>

        <UsageCell value={budget.tool_calls} max={budget.max_tool_calls} label="tool calls" />
        <UsageCell value={budget.total_tokens} max={budget.max_total_tokens} label="tokens" />

        <div className="hidden min-w-0 lg:block">
          <div className="mb-1.5 flex items-baseline justify-between gap-2 text-[12.5px]">
            <span className="figures font-medium text-ink">{formatUsd(budget.cost_usd)}</span>
            <span className="figures text-muted">of {formatUsd(budget.max_cost_usd)}</span>
          </div>
          <Meter
            value={toNumber(budget.cost_usd)}
            max={toNumber(budget.max_cost_usd)}
            tone={tone}
            label={`Spend ${formatUsd(budget.cost_usd)} of ${formatUsd(budget.max_cost_usd)}`}
          />
        </div>

        <div className="flex items-center justify-end gap-2 lg:justify-start" onClick={(event) => event.stopPropagation()}>
          {breaker.open ? (
            <>
              <Chip tone="critical" icon={<IconLoop size={12} />}>
                Open
              </Chip>
              {canReset && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void reset()}
                  className="inline-flex h-7 items-center rounded-md bg-panel-2 px-2.5 text-[12px] font-medium text-ink shadow-[0_0_0_1px_var(--line-strong)] hover:bg-panel-3 disabled:opacity-60"
                >
                  {busy ? "Resetting…" : "Reset"}
                </button>
              )}
            </>
          ) : budgetState === "exhausted" ? (
            <Chip tone="critical" icon={<IconStop size={12} />}>
              Budget spent
            </Chip>
          ) : budgetState === "warning" ? (
            <Chip tone="warning">{formatPercent(utilisation(budget))} used</Chip>
          ) : (
            <span className="text-[12px] text-muted">Closed</span>
          )}
        </div>

        <IconChevronDown
          size={15}
          className={cx("hidden text-muted transition-transform duration-200 lg:block", open && "rotate-180")}
        />
      </div>
      {error && (
        <p role="alert" className="px-5 pb-3 text-[12.5px] text-critical-ink">
          {error}
        </p>
      )}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.2, 0.8, 0.2, 1] }}
            className="overflow-hidden"
          >
            <SessionDetail status={status} />
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  );
}

function UsageCell({ value, max, label }: { value: number; max: number; label: string }) {
  const share = max > 0 ? value / max : 0;
  return (
    <div className="hidden min-w-0 lg:block">
      <p className="figures mb-1.5 text-[12.5px] text-ink">
        {formatCount(value)} <span className="text-muted">/ {formatCount(max)}</span>
      </p>
      <Meter value={value} max={max} tone={share >= 1 ? "critical" : share >= 0.8 ? "warning" : "accent"} label={`${formatCount(value)} of ${formatCount(max)} ${label}`} />
    </div>
  );
}

function SessionDetail({ status }: { status: SessionStatus }) {
  const { state, now } = useConsole();
  const { session, budget, circuit_breaker: breaker } = status;
  const llm = toNumber(budget.llm_cost_usd);
  const tools = toNumber(budget.tool_cost_usd);
  const total = llm + tools || 1;
  const events = useMemo(
    () => describeAll(state.events.filter((event) => event.session_id === session.session_id)).slice(0, 8),
    [state.events, session.session_id],
  );
  const tokens = [
    ["Input", budget.input_tokens],
    ["Output", budget.output_tokens],
    ["Cache writes", budget.cache_write_tokens],
    ["Cache reads", budget.cache_read_tokens],
  ] as const;
  const maxTokens = Math.max(...tokens.map(([, value]) => value), 1);
  const trip = breaker.trip as { tool?: string; identical_calls?: number; window_seconds?: number } | null;

  return (
    <div className="grid gap-6 border-t border-line px-5 py-5 md:grid-cols-3">
      <div>
        <p className="eyebrow mb-3">Where the money went</p>
        <div className="flex h-2 overflow-hidden rounded-full bg-panel-3">
          <motion.div
            className="h-full bg-accent"
            initial={{ width: 0 }}
            animate={{ width: `${(llm / total) * 100}%` }}
            transition={{ type: "spring", stiffness: 120, damping: 22 }}
          />
          <motion.div
            className="h-full border-l-2 border-panel bg-good"
            initial={{ width: 0 }}
            animate={{ width: `${(tools / total) * 100}%` }}
            transition={{ type: "spring", stiffness: 120, damping: 22, delay: 0.05 }}
          />
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-3 text-[12.5px]">
          <div>
            <dt className="flex items-center gap-1.5 text-muted">
              <span className="size-2 rounded-full bg-accent" /> Model
            </dt>
            <dd className="figures mt-0.5 font-semibold text-ink">{formatUsd(llm)}</dd>
          </div>
          <div>
            <dt className="flex items-center gap-1.5 text-muted">
              <span className="size-2 rounded-full bg-good" /> Tools
            </dt>
            <dd className="figures mt-0.5 font-semibold text-ink">{formatUsd(tools)}</dd>
          </div>
        </dl>
        <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-line pt-3 text-[12.5px]">
          <div>
            <dt className="text-muted">First seen</dt>
            <dd className="mt-0.5 text-ink">{formatRelative(session.first_seen, now)}</dd>
          </div>
          <div>
            <dt className="text-muted">Session id</dt>
            <dd className="mono mt-0.5 flex items-center gap-1 text-[10.5px] text-ink">
              {shortId(session.session_id, 12)}
              <CopyButton value={session.session_id} label="Copy session id" />
            </dd>
          </div>
        </dl>
      </div>

      <div>
        <p className="eyebrow mb-3">Tokens</p>
        <ul className="flex flex-col gap-2.5">
          {tokens.map(([label, value]) => (
            <li key={label} className="grid grid-cols-[6.5rem_minmax(0,1fr)_4.5rem] items-center gap-3 text-[12.5px]">
              <span className="text-ink-2">{label}</span>
              <span className="h-1.5 overflow-hidden rounded-full bg-panel-3">
                <motion.span
                  className="block h-full rounded-full bg-accent/70"
                  initial={{ width: 0 }}
                  animate={{ width: `${(value / maxTokens) * 100}%` }}
                  transition={{ type: "spring", stiffness: 120, damping: 22 }}
                />
              </span>
              <span className="figures text-right text-ink">{formatCount(value)}</span>
            </li>
          ))}
        </ul>
        {breaker.open && (
          <div className="mt-4 rounded-lg bg-critical/10 p-3 text-[12.5px] text-ink">
            <p className="flex items-center gap-1.5 font-semibold">
              <IconLoop size={13} className="text-critical" /> Loop stopped
            </p>
            <p className="mt-1 text-ink-2">
              {trip?.tool ? `${trip.tool.split("__").at(-1)} repeated` : "An identical call repeated"}
              {trip?.identical_calls ? ` ${trip.identical_calls} times` : ""}
              {trip?.window_seconds ? ` in ${trip.window_seconds}s` : ""}.
              {breaker.retry_after_seconds ? ` Cools down in about ${breaker.retry_after_seconds}s.` : ""}
            </p>
          </div>
        )}
      </div>

      <div className="min-w-0">
        <p className="eyebrow mb-3">Recent events</p>
        {events.length === 0 ? (
          <p className="text-[12.5px] text-muted">No events for this session in the last 15 minutes.</p>
        ) : (
          <ol className="flex flex-col gap-2.5">
            {events.map((entry) => (
              <li key={entry.event.id ?? `${entry.event.type}:${entry.event.occurred_at}`} className="flex items-start gap-2.5 text-[12.5px]">
                <KindGlyph kind={entry.kind} size={20} />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-ink">{entry.title}</p>
                  <p className="mono text-[10px] text-muted">
                    {new Date(entry.event.occurred_at).toLocaleTimeString("en-GB")}
                    {entry.costDelta ? ` · +${formatUsd(entry.costDelta)}` : ""}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
