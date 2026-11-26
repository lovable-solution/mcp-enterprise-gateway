"use client";

import { AnimatePresence, motion } from "motion/react";
import { useRouter, useSearchParams } from "next/navigation";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";

import { cx } from "@/lib/cx";
import { formatRelative, formatSpan, formatToolName } from "@/lib/format";
import { argumentLine } from "@/lib/present";
import type { Approval } from "@/lib/types";

import { useConsole } from "../console/ConsoleProvider";
import { ApprovalChip, RiskChip } from "../ui/Chip";
import { CountdownRing } from "../ui/CountdownRing";
import { IconApprovals, IconCheck, IconClock } from "../ui/Icons";
import { Count, PageHeader } from "../ui/Layout";
import { EmptyState, Kbd } from "../ui/Misc";
import { ApprovalDetail } from "./ApprovalDetail";

type Tab = "pending" | "decided";

export function ApprovalsView() {
  const params = useSearchParams();
  const router = useRouter();
  const { pending, decided, state, now, ready, loadApproval, canDecide } = useConsole();
  const requested = params.get("id");
  const [tab, setTab] = useState<Tab>("pending");
  const [selectedId, setSelectedId] = useState<string | null>(requested);
  const listRef = useRef<HTMLUListElement>(null);

  // Follow a deep link (from the overview, a toast or the command palette) once per id, so
  // later refreshes never override a tab the operator picked.
  const followed = useRef<string | null>(null);
  const fetched = useRef<string | null>(null);
  useEffect(() => {
    if (!requested || followed.current === requested) {
      return;
    }
    const known = state.approvals[requested];
    if (!known) {
      if (ready && fetched.current !== requested) {
        fetched.current = requested;
        loadApproval(requested); // older than the lists the console loads
      }
      return;
    }
    followed.current = requested;
    setSelectedId(requested);
    setTab(known.status === "PENDING_APPROVAL" ? "pending" : "decided");
  }, [requested, state.approvals, ready, loadApproval]);

  const list = tab === "pending" ? pending : decided;
  const activeId = selectedId && state.approvals[selectedId] ? selectedId : (list[0]?.id ?? null);
  const selected = activeId ? state.approvals[activeId] : undefined;

  useEffect(() => {
    if (activeId && state.approvals[activeId]?.audit === undefined) {
      loadApproval(activeId);
    }
  }, [activeId, state.approvals, loadApproval]);

  function select(id: string) {
    setSelectedId(id);
    router.replace(`/approvals?id=${id}`, { scroll: false });
  }

  function onListKey(event: KeyboardEvent<HTMLUListElement>) {
    const index = list.findIndex((approval) => approval.id === activeId);
    const step = event.key === "ArrowDown" || event.key === "j" ? 1 : event.key === "ArrowUp" || event.key === "k" ? -1 : 0;
    if (step === 0 || list.length === 0) {
      return;
    }
    event.preventDefault();
    const next = list[Math.min(list.length - 1, Math.max(0, index + step))];
    if (next) {
      select(next.id);
      listRef.current?.querySelector<HTMLElement>(`[data-id="${next.id}"]`)?.focus();
    }
  }

  return (
    <>
      <PageHeader
        eyebrow={
          <>
            <IconApprovals size={12} /> Human in the loop
          </>
        }
        title="Approvals"
        description={
          canDecide
            ? "High-risk calls wait here until someone other than the requester decides. Approving runs the stored payload exactly once."
            : "High-risk calls wait here for an approver. Your role can review them and their audit trail, but not decide."
        }
      />

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(320px,400px)_minmax(0,1fr)]">
        <section className="panel overflow-hidden lg:sticky lg:top-6" aria-label="Approval requests">
          <div className="flex items-center gap-1 border-b border-line p-1.5" role="tablist" aria-label="Approval lists">
            {(["pending", "decided"] as const).map((option) => (
              <button
                key={option}
                type="button"
                role="tab"
                aria-selected={tab === option}
                onClick={() => setTab(option)}
                className={cx(
                  "relative flex h-8 flex-1 items-center justify-center gap-2 rounded-lg text-[13px] font-medium transition-colors",
                  tab === option ? "text-ink" : "text-muted hover:text-ink-2",
                )}
              >
                {tab === option && (
                  <motion.span
                    layoutId="approval-tab"
                    className="absolute inset-0 rounded-lg bg-panel-3"
                    transition={{ type: "spring", stiffness: 500, damping: 38 }}
                  />
                )}
                <span className="relative">{option === "pending" ? "Held" : "Decided"}</span>
                <span className="relative">
                  <Count tone={option === "pending" && pending.length > 0 ? "warning" : "muted"}>
                    {option === "pending" ? pending.length : decided.length}
                  </Count>
                </span>
              </button>
            ))}
          </div>

          {!ready ? (
            <div className="flex flex-col gap-4 p-4">
              {[0, 1, 2, 3].map((row) => (
                <span key={row} className="skeleton h-14 w-full" />
              ))}
            </div>
          ) : list.length === 0 ? (
            tab === "pending" ? (
              <EmptyState icon={<IconCheck size={18} />} title="Nothing is held">
                When an agent calls a tool that needs approval, it waits here and you get a notification.
              </EmptyState>
            ) : (
              <EmptyState icon={<IconClock size={18} />} title="No decisions yet">
                Approved, rejected and expired calls from the last 50 appear here.
              </EmptyState>
            )
          ) : (
            <ul
              ref={listRef}
              onKeyDown={onListKey}
              className="scrollbar-thin max-h-[calc(100vh-280px)] min-h-40 divide-y divide-line overflow-y-auto"
            >
              <AnimatePresence initial={false}>
                {list.map((approval) => (
                  <motion.li
                    key={approval.id}
                    layout="position"
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    exit={{ opacity: 0, height: 0 }}
                    transition={{ duration: 0.26, ease: [0.2, 0.8, 0.2, 1] }}
                  >
                    <ApprovalRow approval={approval} selected={approval.id === activeId} now={now} onSelect={select} />
                  </motion.li>
                ))}
              </AnimatePresence>
            </ul>
          )}
          <div className="flex items-center gap-2 border-t border-line px-4 py-2 text-[11.5px] text-muted">
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd> move through the list
          </div>
        </section>

        <AnimatePresence mode="wait" initial={false}>
          {selected ? (
            <motion.div
              key={selected.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.22, ease: [0.2, 0.8, 0.2, 1] }}
              className="min-w-0"
            >
              <ApprovalDetail approval={selected} />
            </motion.div>
          ) : (
            <motion.div key="empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="panel">
              <EmptyState icon={<IconApprovals size={18} />} title="Select a call">
                Pick a held call to see exactly what it will do, why the agent asked, and who may decide.
              </EmptyState>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </>
  );
}

function ApprovalRow({
  approval,
  selected,
  now,
  onSelect,
}: {
  approval: Approval;
  selected: boolean;
  now: number;
  onSelect: (id: string) => void;
}) {
  const { tool, server } = formatToolName(approval.tool_name);
  const pending = approval.status === "PENDING_APPROVAL";
  return (
    <button
      type="button"
      data-id={approval.id}
      onClick={() => onSelect(approval.id)}
      aria-current={selected ? "true" : undefined}
      className={cx(
        "relative flex w-full gap-3.5 px-4 py-3.5 text-left transition-colors",
        selected ? "bg-panel-2" : "hover:bg-panel-2/60",
      )}
    >
      {selected && (
        <motion.span
          layoutId="approval-selected"
          className="absolute inset-y-2 left-0 w-[3px] rounded-r-full bg-accent"
          transition={{ type: "spring", stiffness: 500, damping: 40 }}
        />
      )}
      {pending ? (
        <CountdownRing createdAt={approval.created_at} expiresAt={approval.expires_at} now={now} size={28} />
      ) : (
        <span className="mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-panel-3 text-muted">
          <IconClock size={13} />
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <p className="flex min-w-0 items-baseline gap-2">
            <span className="truncate text-[13.5px] font-semibold text-ink">{tool}</span>
            {server && <span className="mono truncate text-[10px] text-muted">{server}</span>}
          </p>
          {pending ? <RiskChip risk={approval.risk} /> : <ApprovalChip status={approval.status} />}
        </div>
        <p className="mt-1 truncate text-[12.5px] text-ink-2">{argumentLine(approval.arguments) || "no arguments"}</p>
        <p className="mt-1 truncate text-[11.5px] text-muted">
          {pending
            ? `${approval.requester_subject} · waiting ${formatSpan(Math.max(0, (now - Date.parse(approval.created_at)) / 1000))}`
            : `${approval.decided_by_subject ?? approval.requester_subject} · ${formatRelative(
                approval.status_changed_at ?? approval.decided_at ?? approval.created_at,
                now,
              )}`}
        </p>
      </div>
    </button>
  );
}
