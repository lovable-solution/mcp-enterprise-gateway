"use client";

import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { cx } from "@/lib/cx";
import { formatCount, formatUsd } from "@/lib/format";
import { shortId } from "@/lib/present";
import { type Bucket, type Described, type TrafficKind, bucketize, describeAll } from "@/lib/traffic";
import type { GatewayEvent } from "@/lib/types";

import { useConsole } from "../console/ConsoleProvider";
import { Button } from "../ui/Button";
import { IconActivity, IconPause, IconPlay, IconSearch } from "../ui/Icons";
import { PageHeader, Panel } from "../ui/Layout";
import { EmptyState } from "../ui/Misc";
import { useWidth } from "../ui/useWidth";
import { LiveStatus } from "../shell/Sidebar";
import { KindGlyph } from "./KindGlyph";

type Filter = "all" | "calls" | "model" | "approvals" | "denied" | "loops" | "budget";

const FILTERS: readonly { value: Filter; label: string; kinds: readonly TrafficKind[] }[] = [
  { value: "all", label: "All", kinds: [] },
  { value: "calls", label: "Tool calls", kinds: ["allowed"] },
  { value: "approvals", label: "Approvals", kinds: ["held", "approved", "executed", "rejected", "expired", "failed"] },
  { value: "denied", label: "Denied", kinds: ["denied"] },
  { value: "loops", label: "Loops", kinds: ["breaker", "breaker_reset"] },
  { value: "budget", label: "Budget", kinds: ["budget"] },
  { value: "model", label: "Model", kinds: ["model"] },
];

const WINDOW_MS = 15 * 60_000;
const BUCKET_MS = 30_000;
const MAX_ROWS = 200;

export function ActivityView() {
  const { state, now } = useConsole();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [frozen, setFrozen] = useState<readonly GatewayEvent[] | null>(null);

  const source = frozen ?? state.events;
  const described = useMemo(() => describeAll(source), [source]);
  const buckets = useMemo(() => bucketize(state.events, now, WINDOW_MS, BUCKET_MS), [state.events, now]);
  const kinds = FILTERS.find((entry) => entry.value === filter)?.kinds ?? [];
  const needle = query.trim().toLowerCase();
  const rows = described
    .filter((entry) => kinds.length === 0 || kinds.includes(entry.kind))
    .filter(
      (entry) =>
        !needle ||
        `${entry.title} ${entry.detail} ${entry.event.session_id ?? ""} ${entry.tool ?? ""}`.toLowerCase().includes(needle),
    )
    .slice(0, MAX_ROWS);
  const newer = frozen ? state.events.length - frozen.length : 0;

  return (
    <>
      <PageHeader
        eyebrow={
          <>
            <IconActivity size={12} /> Event stream
          </>
        }
        title="Activity"
        description="Every gateway event for your tenant from the last 15 minutes, then live: tool calls, model usage, holds and decisions, denials, loops and budget alerts."
        actions={
          <>
            <LiveStatus />
            <Button
              variant={frozen ? "primary" : "secondary"}
              size="sm"
              onClick={() => setFrozen(frozen ? null : state.events)}
              aria-pressed={frozen !== null}
            >
              {frozen ? <IconPlay size={13} /> : <IconPause size={13} />}
              {frozen ? "Resume" : "Pause"}
            </Button>
          </>
        }
      />

      <Panel
        title="Last 15 minutes"
        meta={<span className="eyebrow hidden sm:inline">30-second buckets</span>}
        actions={
          <div className="flex flex-wrap items-center gap-3 text-[12px] text-ink-2">
            <LegendSwatch tone="accent" label="Tool calls" />
            <LegendSwatch tone="warning" label="Held" />
            <LegendSwatch tone="critical" label="Denied" />
            <LegendSwatch tone="muted" label="Model" />
          </div>
        }
      >
        <Histogram buckets={buckets} />
      </Panel>

      <div className="mt-5 mb-3 flex flex-wrap items-center gap-2">
        {FILTERS.map((entry) => (
          <button
            key={entry.value}
            type="button"
            onClick={() => setFilter(entry.value)}
            aria-pressed={filter === entry.value}
            className={cx(
              "relative h-8 rounded-full px-3.5 text-[12.5px] font-medium transition-colors",
              filter === entry.value ? "text-on-accent" : "bg-panel text-ink-2 shadow-[var(--shadow-panel)] hover:text-ink",
            )}
          >
            {filter === entry.value && (
              <motion.span
                layoutId="activity-filter"
                className="absolute inset-0 rounded-full bg-accent"
                transition={{ type: "spring", stiffness: 500, damping: 38 }}
              />
            )}
            <span className="relative">{entry.label}</span>
          </button>
        ))}
        <label className="ml-auto flex h-9 w-full items-center gap-2 rounded-[10px] bg-panel px-3 shadow-[var(--shadow-panel)] sm:w-72">
          <IconSearch size={14} className="text-muted" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Tool, agent or session"
            className="h-full flex-1 bg-transparent text-[13.5px] text-ink outline-none placeholder:text-muted"
            aria-label="Search events"
          />
        </label>
      </div>

      <section className="panel relative overflow-hidden" aria-label="Event log">
        <AnimatePresence>
          {frozen && newer > 0 && (
            <motion.button
              type="button"
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              onClick={() => setFrozen(null)}
              className="absolute top-2 left-1/2 z-10 -translate-x-1/2 rounded-full bg-accent px-3 py-1 text-[12px] font-medium text-on-accent shadow-[var(--shadow-pop)]"
            >
              {newer} new {newer === 1 ? "event" : "events"} · resume
            </motion.button>
          )}
        </AnimatePresence>
        <div className="hidden grid-cols-[5.5rem_minmax(0,2fr)_minmax(0,1fr)_6rem] gap-4 border-b border-line px-5 py-2.5 md:grid">
          {["Time", "Event", "Session", "Cost"].map((heading) => (
            <span key={heading} className={cx("eyebrow", heading === "Cost" && "text-right")}>
              {heading}
            </span>
          ))}
        </div>
        {rows.length === 0 ? (
          <EmptyState icon={<IconActivity size={18} />} title={state.events.length === 0 ? "Listening for events" : "No events match"}>
            {state.events.length === 0
              ? "Nothing happened in the last 15 minutes. Events stream in live; run an agent to see its calls arrive."
              : "Change the filter or the search to see more."}
          </EmptyState>
        ) : (
          <ol className="divide-y divide-line" aria-live={frozen ? "off" : "polite"}>
            <AnimatePresence initial={false}>
              {rows.map((entry) => (
                <EventRow key={entry.event.id ?? `${entry.event.type}:${entry.event.occurred_at}`} entry={entry} />
              ))}
            </AnimatePresence>
          </ol>
        )}
        {described.length > MAX_ROWS && (
          <p className="border-t border-line px-5 py-2.5 text-[12px] text-muted">
            Showing the newest {MAX_ROWS} of {formatCount(described.length)} events.
          </p>
        )}
      </section>
    </>
  );
}

function LegendSwatch({ tone, label }: { tone: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden="true" className="size-2.5 rounded-[3px]" style={{ background: `var(--${tone})` }} />
      {label}
    </span>
  );
}

function EventRow({ entry }: { entry: Described }) {
  const { event } = entry;
  return (
    <motion.li
      layout="position"
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="relative grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-5 py-3 md:grid-cols-[5.5rem_minmax(0,2fr)_minmax(0,1fr)_6rem]"
    >
      {/* New rows arrive with a brief wash that fades out. */}
      <motion.span
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 bg-accent/10"
        initial={{ opacity: 1 }}
        animate={{ opacity: 0 }}
        transition={{ duration: 1.8, ease: "easeOut" }}
      />
      <span className="mono figures order-2 text-[10.5px] text-muted md:order-none">
        {new Date(event.occurred_at).toLocaleTimeString("en-GB")}
      </span>
      <div className="order-1 flex min-w-0 items-center gap-3 md:order-none">
        <KindGlyph kind={entry.kind} />
        <div className="min-w-0">
          <p className="truncate text-[13.5px] font-medium text-ink">{entry.title}</p>
          {entry.detail && <p className="truncate text-[12px] text-ink-2">{entry.detail}</p>}
        </div>
      </div>
      <span className="mono hidden truncate text-[10.5px] text-muted md:block" title={event.session_id ?? undefined}>
        {event.session_id ? (
          <Link href={`/sessions?id=${encodeURIComponent(event.session_id)}`} className="hover:text-accent">
            {shortId(event.session_id, 14)}
          </Link>
        ) : (
          "—"
        )}
      </span>
      <span className="figures hidden text-right text-[12.5px] text-ink-2 md:block">
        {entry.costDelta ? `+${formatUsd(entry.costDelta)}` : ""}
      </span>
    </motion.li>
  );
}

const STACK: readonly (keyof Omit<Bucket, "start">)[] = ["allowed", "held", "denied", "model"];
const STACK_TONE: Record<string, string> = { allowed: "accent", held: "warning", denied: "critical", model: "muted" };

function Histogram({ buckets }: { buckets: Bucket[] }) {
  const [ref, measured] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const width = Math.max(measured - 32, 1);
  const height = 150;
  const top = 12;
  const bottom = 22;
  const max = Math.max(1, ...buckets.map((bucket) => STACK.reduce((sum, key) => sum + bucket[key], 0)));
  const inner = width / Math.max(buckets.length, 1);
  const scale = (value: number) => (value / max) * (height - top - bottom);
  const total = buckets.reduce((sum, bucket) => sum + STACK.reduce((acc, key) => acc + bucket[key], 0), 0);
  const hovered = hover !== null ? buckets[hover] : undefined;

  return (
    <div ref={ref} className="relative px-4 pt-3 pb-1" onMouseLeave={() => setHover(null)}>
      {measured > 0 && (
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`${total} events in the last 15 minutes, in 30-second buckets.`}
          className="block"
        >
          {[0.5, 1].map((fraction) => (
            <line
              key={fraction}
              x1={0}
              x2={width}
              y1={height - bottom - (height - top - bottom) * fraction}
              y2={height - bottom - (height - top - bottom) * fraction}
              stroke="var(--line)"
              strokeDasharray="2 4"
            />
          ))}
          <text x={0} y={top - 2} className="mono" fontSize={9.5} fill="var(--muted)">
            {max}
          </text>
          <line x1={0} x2={width} y1={height - bottom} y2={height - bottom} stroke="var(--line-strong)" />
          {buckets.map((bucket, index) => {
            const x = index * inner + (inner - Math.max(2, inner - 3)) / 2;
            let y = height - bottom;
            const segments = STACK.filter((key) => bucket[key] > 0).map((key) => {
              const h = scale(bucket[key]);
              y -= h;
              return { key, y, h };
            });
            return (
              <g key={bucket.start} onMouseEnter={() => setHover(index)} opacity={hover === null || hover === index ? 1 : 0.45}>
                <rect x={index * inner} y={top} width={inner} height={height - top - bottom} fill="transparent" />
                {segments.map((segment, layer) => (
                  <motion.rect
                    key={segment.key}
                    x={x}
                    width={Math.max(2, inner - 3)}
                    rx={layer === segments.length - 1 ? 2 : 0}
                    fill={`var(--${STACK_TONE[segment.key]})`}
                    initial={{ y: height - bottom, height: 0 }}
                    animate={{ y: segment.y + (layer === 0 ? 0 : 1), height: Math.max(0, segment.h - (layer === 0 ? 0 : 1)) }}
                    transition={{ type: "spring", stiffness: 160, damping: 24 }}
                  />
                ))}
              </g>
            );
          })}
          {[0, Math.floor(buckets.length / 2), buckets.length - 1].map((index) => {
            const bucket = buckets[index];
            if (!bucket) {
              return null;
            }
            const label = index === buckets.length - 1 ? "now" : `−${Math.round((buckets.length - index) * 0.5)} min`;
            return (
              <text
                key={index}
                x={index * inner + inner / 2}
                y={height - 6}
                textAnchor={index === 0 ? "start" : index === buckets.length - 1 ? "end" : "middle"}
                className="mono"
                fontSize={9.5}
                fill="var(--muted)"
              >
                {label}
              </text>
            );
          })}
        </svg>
      )}
      {hovered && hover !== null && (
        <div
          className="pointer-events-none absolute top-2 z-10 w-44 -translate-x-1/2 rounded-lg bg-panel px-3 py-2 text-[12px] shadow-[var(--shadow-pop)]"
          style={{ left: Math.min(Math.max(16 + hover * inner + inner / 2, 90), measured - 90) }}
        >
          <p className="mono text-[10px] text-muted">
            {new Date(hovered.start).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
          </p>
          {STACK.map((key) => (
            <p key={key} className="mt-1 flex items-center justify-between gap-2 text-ink-2">
              <span className="flex items-center gap-1.5">
                <span className="size-2 rounded-[2px]" style={{ background: `var(--${STACK_TONE[key]})` }} />
                {key === "allowed" ? "Tool calls" : key === "model" ? "Model" : key === "held" ? "Held" : "Denied"}
              </span>
              <span className="figures font-medium text-ink">{hovered[key]}</span>
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
