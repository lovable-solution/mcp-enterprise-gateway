"use client";

import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { type ReactNode, useState } from "react";

import { cx } from "@/lib/cx";
import { formatSpan, formatToolName } from "@/lib/format";
import {
  type PresentedArgument,
  agentReason,
  formatCountdown,
  presentArguments,
  presentResult,
  remainingFraction,
  shortId,
} from "@/lib/present";
import type { Approval, ApprovalStatus, AuditEntry } from "@/lib/types";

import { useConsole } from "../console/ConsoleProvider";
import { Button } from "../ui/Button";
import { ApprovalChip, Chip, RiskChip, type Tone, approvalTone } from "../ui/Chip";
import { countdownTone } from "../ui/CountdownRing";
import { HoldButton } from "../ui/HoldButton";
import { IconAlert, IconCheck, IconClock, IconCross, IconServer, IconSessions } from "../ui/Icons";
import { JsonView } from "../ui/JsonView";
import { Avatar, CopyButton } from "../ui/Misc";

const HOLD_RISKS = new Set(["high", "critical"]);

function time(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
}

export function ApprovalDetail({ approval }: { approval: Approval }) {
  const { now, canDecide } = useConsole();
  const { tool, server } = formatToolName(approval.tool_name);
  const pending = approval.status === "PENDING_APPROVAL";
  const reason = agentReason(approval.arguments);
  const fields = presentArguments(approval.arguments);

  return (
    <article className="panel overflow-hidden" aria-label={`Held call ${tool}`}>
      <header className="relative border-b border-line px-5 pt-5 pb-4 sm:px-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="eyebrow flex items-center gap-2">
              <IconServer size={12} /> {server || "tool"}
            </p>
            <h2 className="display mt-2 truncate text-[24px] leading-tight text-ink">{tool}</h2>
            <p className="mono mt-1.5 flex items-center gap-1 text-[10.5px] text-muted">
              {approval.id}
              <CopyButton value={approval.id} label="Copy approval id" />
            </p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <RiskChip risk={approval.risk} />
            <ApprovalChip status={approval.status} />
          </div>
        </div>
        {pending && <ExpiryBar approval={approval} now={now} />}
      </header>

      <div className="grid gap-6 px-5 py-5 sm:px-6">
        <Outcome approval={approval} />

        <section aria-label="Requested change">
          <h3 className="eyebrow mb-2.5">Requested change</h3>
          {fields.length === 0 ? (
            <p className="text-[13px] text-ink-2">The call has no arguments.</p>
          ) : (
            <dl className="grid gap-px overflow-hidden rounded-[10px] bg-line shadow-[0_0_0_1px_var(--line)] sm:grid-cols-2">
              {fields.map((field) => (
                <Field key={field.key} field={field} />
              ))}
            </dl>
          )}
        </section>

        {reason && (
          <section aria-label="The agent's reasoning">
            <h3 className="eyebrow mb-2.5">Why the agent asked</h3>
            <blockquote className="relative rounded-[10px] bg-panel-2 py-3.5 pr-4 pl-5 text-[13.5px] leading-relaxed text-ink">
              <span aria-hidden="true" className="absolute inset-y-3 left-0 w-[3px] rounded-r-full bg-accent/60" />
              {reason}
            </blockquote>
          </section>
        )}

        <section aria-label="Context">
          <h3 className="eyebrow mb-2.5">Context</h3>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 md:grid-cols-3">
            <Fact label="Requested by">
              <span className="flex items-center gap-2">
                <Avatar subject={approval.requester_subject} size={22} />
                <span className="truncate">
                  {approval.requester_subject} <span className="text-muted">· {approval.requester_role}</span>
                </span>
              </span>
            </Fact>
            <Fact label="Agent session">
              <Link
                href={`/sessions?id=${encodeURIComponent(approval.session_id)}`}
                className="mono inline-flex items-center gap-1.5 text-[11.5px] text-accent hover:text-accent-hover"
                title={approval.session_id}
              >
                <IconSessions size={12} /> {shortId(approval.session_id, 14)}
              </Link>
            </Fact>
            <Fact label="Requested at">
              {time(approval.created_at)}{" "}
              <span className="text-muted">· {formatSpan(Math.max(0, (now - Date.parse(approval.created_at)) / 1000))} ago</span>
            </Fact>
            <Fact label="Who may decide">
              <span className="flex flex-wrap gap-1">
                {approval.approver_roles.map((role) => (
                  <Chip key={role} tone="muted">
                    {role}
                  </Chip>
                ))}
              </span>
            </Fact>
            {approval.policy_rule && (
              <Fact label="Policy rule">
                <span className="mono text-[11px]">{approval.policy_rule}</span>
              </Fact>
            )}
            {approval.trace_id && (
              <Fact label="Trace">
                <span className="mono flex items-center gap-1 text-[11px]">
                  {shortId(approval.trace_id, 16)}
                  <CopyButton value={approval.trace_id} label="Copy trace id" />
                </span>
              </Fact>
            )}
          </dl>
        </section>

        <JsonView
          value={approval.arguments}
          label="Exact payload"
          footer={approval.arguments_digest ? `digest ${approval.arguments_digest}` : undefined}
        />

        {approval.status === "EXECUTED" && <ResultView result={approval.result} />}

        <AuditTimeline entries={approval.audit} createdAt={approval.created_at} />
      </div>

      {pending && canDecide && <DecisionBar approval={approval} />}
      {pending && !canDecide && (
        <p className="border-t border-line bg-panel-2 px-6 py-4 text-[13px] text-ink-2">
          Your role can review this call but not decide it.
        </p>
      )}
    </article>
  );
}

function ExpiryBar({ approval, now }: { approval: Approval; now: number }) {
  const fraction = remainingFraction(approval.created_at, approval.expires_at, now);
  const tone = countdownTone(approval.created_at, approval.expires_at, now);
  return (
    <div className="mt-4">
      <div className="mb-1.5 flex items-center justify-between text-[12px]">
        <span className="flex items-center gap-1.5 text-ink-2">
          <IconClock size={13} style={{ color: `var(--${tone})` }} />
          Expires in <span className="figures font-semibold text-ink">{formatCountdown(Date.parse(approval.expires_at) - now)}</span>
        </span>
        <span className="text-muted">If nobody decides, it never runs.</span>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-panel-3">
        <div
          className="h-full rounded-full"
          style={{ width: `${fraction * 100}%`, background: `var(--${tone})`, transition: "width 1s linear, background 300ms" }}
        />
      </div>
    </div>
  );
}

function Field({ field }: { field: PresentedArgument }) {
  return (
    <div className="bg-panel px-4 py-3">
      <dt className="text-[11.5px] text-muted">{field.label}</dt>
      <dd
        className={cx(
          "mt-1 break-words text-ink",
          field.kind === "money" && "display figures text-[20px] leading-tight",
          field.kind === "number" && "figures text-[15px] font-semibold",
          field.kind === "id" && "mono text-[12.5px]",
          (field.kind === "text" || field.kind === "bool" || field.kind === "empty") && "text-[13.5px]",
          field.kind === "time" && "figures text-[13.5px]",
          field.kind === "json" && "mono text-[11.5px]",
        )}
      >
        {field.text}
      </dd>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11.5px] text-muted">{label}</dt>
      <dd className="mt-1 text-[13px] text-ink">{children}</dd>
    </div>
  );
}

const OUTCOME: Partial<Record<ApprovalStatus, { title: string; icon: ReactNode }>> = {
  APPROVED: { title: "Approved: the gateway is running it", icon: <IconCheck size={16} /> },
  EXECUTING: { title: "Running the stored payload", icon: <IconClock size={16} /> },
  EXECUTED: { title: "Executed", icon: <IconCheck size={16} /> },
  REJECTED: { title: "Rejected", icon: <IconCross size={16} /> },
  EXPIRED: { title: "Expired without a decision", icon: <IconClock size={16} /> },
  FAILED: { title: "Failed after approval", icon: <IconAlert size={16} /> },
};

function Outcome({ approval }: { approval: Approval }) {
  const outcome = OUTCOME[approval.status];
  if (!outcome) {
    return null;
  }
  const tone: Tone = approvalTone(approval.status);
  const decidedAfter =
    approval.decided_at && approval.created_at
      ? formatSpan(Math.max(0, (Date.parse(approval.decided_at) - Date.parse(approval.created_at)) / 1000))
      : null;
  const lines = [
    approval.decided_by_subject
      ? `Decided by ${approval.decided_by_subject}${approval.decided_by_role ? ` (${approval.decided_by_role})` : ""}${
          decidedAfter ? `, ${decidedAfter} after the request` : ""
        }.`
      : null,
    approval.executed_at ? `Ran at ${time(approval.executed_at)}.` : null,
    approval.status === "EXPIRED" ? "Nobody decided in time, so the call never ran." : null,
  ].filter(Boolean);
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      className="flex gap-3.5 rounded-[10px] p-4"
      style={{ background: `color-mix(in oklab, var(--${tone}) 9%, transparent)`, boxShadow: `inset 0 0 0 1px color-mix(in oklab, var(--${tone}) 25%, transparent)` }}
    >
      <span
        className="inline-flex size-8 shrink-0 items-center justify-center rounded-full"
        style={{ background: `var(--${tone})`, color: "var(--panel)" }}
      >
        {outcome.icon}
      </span>
      <div className="min-w-0 text-[13px]">
        <p className="font-semibold text-ink">{outcome.title}</p>
        {lines.length > 0 && <p className="mt-0.5 text-ink-2">{lines.join(" ")}</p>}
        {approval.decision_reason && (
          <p className="mt-2 text-ink">
            <span className="text-muted">Reason: </span>
            {approval.decision_reason}
          </p>
        )}
        {approval.error && <p className="mt-2 text-ink">{approval.error}</p>}
      </div>
    </motion.div>
  );
}

function ResultView({ result }: { result: Approval["result"] }) {
  const { fields, text, isError } = presentResult(result);
  return (
    <section aria-label="What the tool returned">
      <h3 className="eyebrow mb-2.5">{isError ? "Tool error" : "What the tool returned"}</h3>
      {fields.length > 0 ? (
        <dl className="grid gap-px overflow-hidden rounded-[10px] bg-line shadow-[0_0_0_1px_var(--line)] sm:grid-cols-2">
          {fields.map((field) => (
            <Field key={field.key} field={field} />
          ))}
        </dl>
      ) : text ? (
        <pre className="mono scrollbar-thin max-h-60 overflow-auto rounded-[10px] bg-panel-2 p-3.5 text-[11.5px] whitespace-pre-wrap">
          {text}
        </pre>
      ) : (
        <p className="text-[13px] text-ink-2">The tool returned no content.</p>
      )}
    </section>
  );
}

const STEP_LABEL: Record<ApprovalStatus, string> = {
  PENDING_APPROVAL: "Held for review",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  EXPIRED: "Expired",
  EXECUTING: "Running",
  EXECUTED: "Executed",
  FAILED: "Failed",
};

function AuditTimeline({ entries, createdAt }: { entries: AuditEntry[] | undefined; createdAt: string }) {
  return (
    <section aria-label="Audit trail">
      <h3 className="eyebrow mb-3">Audit trail · append-only</h3>
      {entries === undefined ? (
        <div className="flex flex-col gap-3">
          <span className="skeleton h-4 w-1/2" />
          <span className="skeleton h-4 w-2/5" />
        </div>
      ) : entries.length === 0 ? (
        <p className="text-[13px] text-ink-2">Requested at {time(createdAt)}. No decisions yet.</p>
      ) : (
        <ol className="relative">
          <AnimatePresence initial={false}>
            {entries.map((entry, index) => {
              const tone = approvalTone(entry.to_status);
              const last = index === entries.length - 1;
              return (
                <motion.li
                  key={entry.id}
                  initial={{ opacity: 0, x: -6 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: index * 0.05 }}
                  className="relative flex gap-3.5 pb-4 last:pb-0"
                >
                  {!last && <span aria-hidden="true" className="absolute top-4 bottom-0 left-[5.5px] w-px bg-line-strong" />}
                  <span
                    aria-hidden="true"
                    className="relative mt-1 size-3 shrink-0 rounded-full"
                    style={{ background: `var(--${tone})`, boxShadow: "0 0 0 3px var(--panel)" }}
                  />
                  <div className="flex min-w-0 flex-1 flex-wrap items-baseline justify-between gap-x-3 text-[13px]">
                    <p className="text-ink">
                      <span className="font-semibold">{STEP_LABEL[entry.to_status]}</span>
                      {entry.actor_subject && (
                        <span className="text-ink-2">
                          {" "}
                          by {entry.actor_subject}
                          {entry.actor_role ? ` (${entry.actor_role})` : ""}
                        </span>
                      )}
                    </p>
                    <span className="mono figures text-[10.5px] text-muted">{time(entry.at)}</span>
                  </div>
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ol>
      )}
    </section>
  );
}

function DecisionBar({ approval }: { approval: Approval }) {
  const { decide } = useConsole();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const hold = HOLD_RISKS.has(approval.risk);

  async function run(decision: "approve" | "reject") {
    if (decision === "reject" && !reason.trim()) {
      setMessage("Add a reason to reject: the agent is told why, and it goes in the audit trail.");
      return;
    }
    setBusy(decision);
    setMessage(null);
    const result = await decide(approval.id, decision, reason.trim());
    setBusy(null);
    if (!result.ok) {
      setMessage(result.message);
    }
  }

  return (
    <div className="sticky bottom-0 border-t border-line bg-panel/95 px-5 py-4 backdrop-blur sm:px-6">
      <label htmlFor="decision-reason" className="flex items-baseline justify-between gap-2 text-[12.5px] font-medium text-ink">
        Note for the audit trail
        <span className="text-[11.5px] font-normal text-muted">required to reject · {reason.length}/1000</span>
      </label>
      <textarea
        id="decision-reason"
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        maxLength={1000}
        rows={2}
        placeholder="Why you approve or reject this call"
        className="mt-1.5 w-full resize-none rounded-lg bg-panel-2 px-3 py-2 text-[13.5px] text-ink shadow-[0_0_0_1px_var(--line-strong)] outline-none placeholder:text-muted focus:shadow-[0_0_0_1.5px_var(--accent)]"
      />
      <AnimatePresence>
        {message && (
          <motion.p
            role="alert"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="mt-2 flex items-center gap-1.5 text-[12.5px] text-critical-ink"
          >
            <IconAlert size={13} /> {message}
          </motion.p>
        )}
      </AnimatePresence>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <p className="text-[12px] text-muted">
          {hold ? "High risk: press and hold to approve. " : ""}Approving runs the payload above, once.
        </p>
        <div className="flex gap-2">
          <Button variant="danger" disabled={busy !== null} onClick={() => void run("reject")}>
            <IconCross size={14} /> {busy === "reject" ? "Rejecting…" : "Reject"}
          </Button>
          {hold ? (
            <HoldButton disabled={busy !== null} onConfirm={() => void run("approve")}>
              <IconCheck size={14} /> {busy === "approve" ? "Approving…" : "Hold to approve"}
            </HoldButton>
          ) : (
            <Button variant="primary" disabled={busy !== null} onClick={() => void run("approve")}>
              <IconCheck size={14} /> {busy === "approve" ? "Approving…" : "Approve and run"}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
