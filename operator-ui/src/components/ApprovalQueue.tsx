"use client";

import { useState } from "react";

import { formatRelative, formatToolName } from "@/lib/format";
import type { Approval } from "@/lib/types";

import { JsonView } from "./JsonView";
import { ApprovalBadge, RiskBadge } from "./StatusBadge";

export type DecideResult = { ok: true; approval: Approval } | { ok: false; message: string };

function ToolName({ name }: { name: string }) {
  const { server, tool } = formatToolName(name);
  return (
    <span>
      <span className="font-semibold text-ink">{tool}</span>
      {server && <span className="text-muted"> · {server}</span>}
    </span>
  );
}

function argumentSummary(args: Record<string, unknown>): string {
  return Object.entries(args)
    .filter(([key]) => key !== "reason")
    .slice(0, 3)
    .map(([key, value]) => `${key}: ${typeof value === "number" ? value.toLocaleString("en-US") : String(value)}`)
    .join(" · ");
}

export function ApprovalList({
  approvals,
  selectedId,
  onSelect,
  now,
}: {
  approvals: Approval[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  now: number;
}) {
  if (approvals.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-hairline px-4 py-8 text-center text-sm text-ink-2">
        Nothing is waiting for review. Held calls appear here the moment an agent makes them.
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-2" aria-label="Held calls, oldest first">
      {approvals.map((approval) => {
        const selected = approval.id === selectedId;
        return (
          <li key={approval.id}>
            <button
              type="button"
              onClick={() => onSelect(approval.id)}
              aria-current={selected}
              className={`w-full rounded-lg border px-3 py-2.5 text-left transition-colors ${
                selected ? "border-accent bg-raised" : "border-hairline bg-surface hover:bg-raised"
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <ToolName name={approval.tool_name} />
                <RiskBadge risk={approval.risk} />
              </div>
              <p className="mt-1 truncate text-sm text-ink-2">{argumentSummary(approval.arguments)}</p>
              <p className="mt-1 text-xs text-muted">
                {approval.requester_subject} ({approval.requester_role}) · waiting{" "}
                {formatRelative(approval.created_at, now).replace(" ago", "")} · expires{" "}
                {formatRelative(approval.expires_at, now)}
              </p>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-0.5 break-all text-sm text-ink">{children}</dd>
    </div>
  );
}

export function ApprovalDetail({
  approval,
  canDecide,
  onDecide,
  now,
}: {
  approval: Approval;
  canDecide: boolean;
  onDecide: (decision: "approve" | "reject", reason: string) => Promise<DecideResult>;
  now: number;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const pending = approval.status === "PENDING_APPROVAL";

  async function decide(decision: "approve" | "reject") {
    if (decision === "reject" && !reason.trim()) {
      setMessage("A rejection needs a reason: the agent is told why.");
      return;
    }
    setBusy(decision);
    setMessage(null);
    const result = await onDecide(decision, reason.trim());
    setBusy(null);
    if (!result.ok) {
      setMessage(result.message);
    } else {
      setReason("");
    }
  }

  const result = approval.result as { structuredContent?: unknown; content?: unknown; isError?: boolean } | null | undefined;
  return (
    <article className="rounded-xl border border-hairline bg-surface p-4" aria-label="Approval detail">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-base">
            <ToolName name={approval.tool_name} />
          </h3>
          <p className="mt-0.5 font-mono text-xs text-muted">{approval.id}</p>
        </div>
        <div className="flex gap-1.5">
          <RiskBadge risk={approval.risk} />
          <ApprovalBadge status={approval.status} />
        </div>
      </header>

      <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Meta label="Requested by">
          {approval.requester_subject} ({approval.requester_role})
        </Meta>
        <Meta label="Agent session">
          <span className="font-mono text-xs">{approval.session_id}</span>
        </Meta>
        <Meta label="Requested">{formatRelative(approval.created_at, now)}</Meta>
        {pending ? (
          <Meta label="Expires">{formatRelative(approval.expires_at, now)}</Meta>
        ) : (
          <Meta label="Decided by">
            {approval.decided_by_subject ?? "-"}
            {approval.decision_reason ? `: ${approval.decision_reason}` : ""}
          </Meta>
        )}
        <Meta label="Approver roles">{approval.approver_roles.join(", ")}</Meta>
        {approval.trace_id && (
          <Meta label="Trace">
            <span className="font-mono text-xs">{approval.trace_id}</span>
          </Meta>
        )}
      </dl>

      <div className="mt-4">
        <JsonView value={approval.arguments} label="Arguments, exactly as they will run" />
      </div>

      {approval.status === "EXECUTED" && result && (
        <div className="mt-3">
          <JsonView
            value={result.structuredContent ?? result.content}
            label={result.isError ? "Upstream result (tool error)" : "Upstream result"}
          />
        </div>
      )}
      {approval.error && (
        <p className="mt-3 rounded-md px-3 py-2 text-sm" style={{ background: "color-mix(in oklab, var(--serious) 16%, transparent)" }}>
          {approval.error}
        </p>
      )}

      {pending && canDecide && (
        <div className="mt-4 border-t border-hairline pt-4">
          <label htmlFor="decision-reason" className="text-sm font-medium">
            Reason <span className="font-normal text-muted">(required to reject; recorded in the audit trail)</span>
          </label>
          <textarea
            id="decision-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            maxLength={1000}
            className="mt-1 h-16 w-full rounded-md border border-hairline bg-page px-3 py-2 text-sm"
          />
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => decide("approve")}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              {busy === "approve" ? "Approving and running..." : "Approve and run"}
            </button>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => decide("reject")}
              className="rounded-md border border-hairline px-4 py-2 text-sm font-medium text-ink hover:bg-raised disabled:opacity-60"
            >
              {busy === "reject" ? "Rejecting..." : "Reject"}
            </button>
          </div>
        </div>
      )}
      {pending && !canDecide && (
        <p className="mt-4 text-sm text-ink-2">Your role can review this call but not decide it.</p>
      )}
      {message && (
        <p role="alert" className="mt-3 text-sm text-ink" style={{ color: "var(--ink)" }}>
          {message}
        </p>
      )}

      {approval.audit && approval.audit.length > 0 && (
        <section className="mt-4 border-t border-hairline pt-3">
          <h4 className="text-xs font-medium uppercase tracking-wide text-muted">Audit trail</h4>
          <ol className="mt-2 space-y-1 text-sm">
            {approval.audit.map((entry) => (
              <li key={entry.id} className="flex flex-wrap gap-x-2 text-ink-2">
                <span className="figures text-muted">{new Date(entry.at).toLocaleTimeString("en-GB")}</span>
                <span className="text-ink">{entry.to_status.replace("_", " ").toLowerCase()}</span>
                {entry.actor_subject && <span>by {entry.actor_subject}</span>}
              </li>
            ))}
          </ol>
        </section>
      )}
    </article>
  );
}

export function RecentDecisions({
  approvals,
  onSelect,
  now,
}: {
  approvals: Approval[];
  onSelect: (id: string) => void;
  now: number;
}) {
  if (approvals.length === 0) {
    return null;
  }
  return (
    <section aria-label="Recent decisions" className="mt-4">
      <h3 className="text-sm font-medium text-ink-2">Recently decided</h3>
      <ul className="mt-2 divide-y divide-hairline rounded-lg border border-hairline bg-surface">
        {approvals.map((approval) => (
          <li key={approval.id}>
            <button
              type="button"
              onClick={() => onSelect(approval.id)}
              className="flex w-full flex-wrap items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-raised"
            >
              <ToolName name={approval.tool_name} />
              <span className="flex items-center gap-2 text-xs text-muted">
                {approval.decided_by_subject ?? ""}{" "}
                {formatRelative(approval.status_changed_at ?? approval.decided_at ?? approval.created_at, now)}
                <ApprovalBadge status={approval.status} />
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
