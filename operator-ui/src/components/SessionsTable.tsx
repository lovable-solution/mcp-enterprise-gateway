"use client";

import { useState } from "react";

import { type BudgetState, utilisation } from "@/lib/dashboard";
import { formatCount, formatPercent, formatRelative, formatUsd, toNumber } from "@/lib/format";
import type { SessionStatus } from "@/lib/types";

import { AlertIcon, LoopIcon, StopIcon } from "./Icons";
import { Badge } from "./StatusBadge";

const FILL: Record<BudgetState, string> = {
  ok: "var(--accent)",
  warning: "var(--warning)",
  exhausted: "var(--critical)",
};

/** Spend against the session's dollar cap. The track is a lighter step of the fill's
 * own hue so the state reads across the whole bar; the figure beside it carries the
 * value, so nothing depends on colour or hover. */
function SpendMeter({ status, state }: { status: SessionStatus; state: BudgetState }) {
  const spent = toNumber(status.budget.cost_usd);
  const cap = toNumber(status.budget.max_cost_usd);
  const share = cap > 0 ? Math.min(1, spent / cap) : 0;
  const track = state === "ok" ? "var(--accent-track)" : `color-mix(in oklab, ${FILL[state]} 22%, var(--surface))`;
  return (
    <div className="flex min-w-64 items-center gap-3">
      <div
        className="h-2 flex-1 overflow-hidden rounded-full"
        style={{ background: track }}
        role="meter"
        aria-valuemin={0}
        aria-valuemax={cap}
        aria-valuenow={spent}
        aria-label={`Spend ${formatUsd(spent)} of ${formatUsd(cap)}`}
      >
        <div
          className="h-2 rounded-full transition-[width] duration-500"
          style={{ width: `${Math.max(share * 100, spent > 0 ? 2 : 0)}%`, background: FILL[state] }}
        />
      </div>
      <span className="figures w-36 shrink-0 whitespace-nowrap text-right text-sm text-ink">
        {formatUsd(spent)} <span className="text-muted">of {formatUsd(cap)}</span>
      </span>
    </div>
  );
}

function BudgetBadge({ state, status }: { state: BudgetState; status: SessionStatus }) {
  if (state === "exhausted") {
    return (
      <Badge tone="critical" icon={<StopIcon />}>
        Exhausted ({(status.budget.exhausted_limit ?? "").replace("max_", "").replaceAll("_", " ")})
      </Badge>
    );
  }
  if (state === "warning") {
    return (
      <Badge tone="warning" icon={<AlertIcon />}>
        {formatPercent(utilisation(status.budget))} used
      </Badge>
    );
  }
  return null;
}

export function SessionsTable({
  sessions,
  stateOf,
  canReset,
  onReset,
  now,
}: {
  sessions: SessionStatus[];
  stateOf: (status: SessionStatus) => BudgetState;
  canReset: boolean;
  onReset: (sessionId: string) => Promise<string | null>;
  now: number;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  async function reset(sessionId: string) {
    setBusy(sessionId);
    const error = await onReset(sessionId);
    setBusy(null);
    setErrors((current) => {
      const next = { ...current };
      if (error) {
        next[sessionId] = error;
      } else {
        delete next[sessionId];
      }
      return next;
    });
  }

  if (sessions.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-hairline px-4 py-8 text-center text-sm text-ink-2">
        No agent sessions in the last 24 hours.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-hairline bg-surface">
      <table className="w-full min-w-[56rem] text-left text-sm">
        <caption className="sr-only">Agent sessions with their budget use and circuit-breaker state</caption>
        <thead className="text-xs text-muted">
          <tr className="border-b border-hairline">
            <th scope="col" className="px-4 py-2 font-medium">Session</th>
            <th scope="col" className="px-4 py-2 font-medium">Last activity</th>
            <th scope="col" className="px-4 py-2 text-right font-medium">Tool calls</th>
            <th scope="col" className="px-4 py-2 text-right font-medium">Tokens</th>
            <th scope="col" className="px-4 py-2 font-medium">Spend against cap</th>
            <th scope="col" className="px-4 py-2 font-medium">Circuit breaker</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-hairline">
          {sessions.map((status) => {
            const { session, budget, circuit_breaker: breaker } = status;
            const state = stateOf(status);
            return (
              <tr key={session.session_id} className="align-middle">
                <td className="px-4 py-2.5">
                  <p className="font-mono text-xs text-ink" title={session.session_id}>
                    {session.session_id}
                  </p>
                  <p className="text-xs text-ink-2">
                    {session.subject} · {session.role}
                  </p>
                </td>
                <td className="px-4 py-2.5 text-ink-2">{formatRelative(session.last_seen, now)}</td>
                <td className="figures px-4 py-2.5 text-right">
                  {formatCount(budget.tool_calls)} <span className="text-muted">/ {formatCount(budget.max_tool_calls)}</span>
                </td>
                <td className="figures px-4 py-2.5 text-right">
                  {formatCount(budget.total_tokens)}{" "}
                  <span className="text-muted">/ {formatCount(budget.max_total_tokens)}</span>
                </td>
                <td className="px-4 py-2.5">
                  <SpendMeter status={status} state={state} />
                  <div className="mt-1 flex items-center gap-2">
                    <BudgetBadge state={state} status={status} />
                    <span className="figures text-xs text-muted">
                      model {formatUsd(budget.llm_cost_usd)} · tools {formatUsd(budget.tool_cost_usd)}
                    </span>
                  </div>
                </td>
                <td className="px-4 py-2.5">
                  {breaker.open ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone="critical" icon={<LoopIcon />}>
                        Open: loop detected
                      </Badge>
                      {canReset && (
                        <button
                          type="button"
                          disabled={busy === session.session_id}
                          onClick={() => reset(session.session_id)}
                          className="rounded-md border border-hairline px-2.5 py-1 text-xs font-medium hover:bg-raised disabled:opacity-60"
                        >
                          {busy === session.session_id ? "Resetting..." : "Reset"}
                        </button>
                      )}
                      {errors[session.session_id] && (
                        <span role="alert" className="text-xs text-ink-2">
                          {errors[session.session_id]}
                        </span>
                      )}
                    </div>
                  ) : (
                    <span className="text-xs text-muted">Closed</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
