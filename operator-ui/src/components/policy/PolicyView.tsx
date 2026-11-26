"use client";

import { motion } from "motion/react";
import { useState } from "react";

import { cx } from "@/lib/cx";
import { formatToolName, formatUsd, toNumber } from "@/lib/format";
import type { PolicyAccess, PolicySummary } from "@/lib/types";

import { useConsole } from "../console/ConsoleProvider";
import { Chip, RiskChip } from "../ui/Chip";
import { IconCheck, IconHold, IconKey, IconPolicy, IconServer } from "../ui/Icons";
import { PageHeader, Panel } from "../ui/Layout";
import { CopyButton, EmptyState } from "../ui/Misc";

const ACCESS_LABEL: Record<PolicyAccess, string> = {
  allow: "Allowed",
  approval: "Allowed after human approval",
  deny: "Denied",
};

export function PolicyView() {
  const { policy, operator } = useConsole();
  const readable = operator.actions.includes("policy:read");

  return (
    <>
      <PageHeader
        eyebrow={
          <>
            <IconPolicy size={12} /> Read-only · enforced now
          </>
        }
        title="Policy"
        description="What each role may do, exactly as the gateway enforces it. The policy file in Git is the only source of truth: change it there and redeploy; a broken file stops the gateway rather than loosening it."
        actions={
          policy && (
            <span className="mono inline-flex h-9 items-center gap-2 rounded-[10px] bg-panel pr-1.5 pl-3 text-[11px] text-ink-2 shadow-[var(--shadow-panel)]">
              {policy.name} · {policy.digest.replace("sha256:", "").slice(0, 12)}
              <CopyButton value={policy.digest} label="Copy policy digest" />
            </span>
          )
        }
      />
      {!readable ? (
        <div className="panel">
          <EmptyState icon={<IconKey size={18} />} title="Your role cannot read the policy">
            The gateway grants policy:read to operators and auditors.
          </EmptyState>
        </div>
      ) : !policy ? (
        <div className="grid gap-4">
          <span className="skeleton h-28 w-full" />
          <span className="skeleton h-72 w-full" />
        </div>
      ) : (
        <PolicyBody policy={policy} />
      )}
    </>
  );
}

function PolicyBody({ policy }: { policy: PolicySummary }) {
  const [focusRole, setFocusRole] = useState<string | null>(null);
  const roles = policy.roles;
  const upstreams = Object.entries(policy.upstreams);

  return (
    <>
      <section aria-label="Tool servers" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {upstreams.map(([name, health], index) => {
          const tools = policy.tools.filter((tool) => formatToolName(tool.name).server === name);
          const healthy = health === "ok";
          return (
            <motion.div
              key={name}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.05 }}
              className="panel flex items-center gap-3.5 p-4"
            >
              <span className="inline-flex size-10 items-center justify-center rounded-[10px] bg-panel-2 text-ink-2 shadow-[0_0_0_1px_var(--line-strong)]">
                <IconServer size={17} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[14px] font-semibold text-ink">{name}</p>
                <p className="mono mt-0.5 truncate text-[10.5px] text-muted">
                  {tools.length} registered {tools.length === 1 ? "tool" : "tools"} ·{" "}
                  {tools.filter((tool) => tool.available).length} offered now
                </p>
              </div>
              <Chip tone={healthy ? "good" : health === "pending" ? "muted" : "critical"}>
                <span className={cx("size-1.5 rounded-full", healthy ? "bg-good" : "bg-critical")} />
                {healthy ? "Healthy" : health === "pending" ? "Pending" : "Unreachable"}
              </Chip>
            </motion.div>
          );
        })}
      </section>

      <Panel
        title="Who may call what"
        meta={<span className="eyebrow hidden sm:inline">default deny</span>}
        actions={
          <div className="hidden items-center gap-4 text-[12px] text-ink-2 md:flex">
            <span className="flex items-center gap-1.5">
              <AccessCell access="allow" compact /> Allowed
            </span>
            <span className="flex items-center gap-1.5">
              <AccessCell access="approval" compact /> After approval
            </span>
            <span className="flex items-center gap-1.5">
              <AccessCell access="deny" compact /> Denied
            </span>
          </div>
        }
        className="mt-5"
        bodyClassName="overflow-x-auto scrollbar-thin"
      >
        <table className="w-full min-w-[760px] border-separate border-spacing-0 text-left text-[13px]">
          <caption className="sr-only">Effective access of each role to each registered tool</caption>
          <thead>
            <tr>
              <th scope="col" className="border-b border-line px-5 py-3">
                <span className="eyebrow">Tool</span>
              </th>
              {roles.map((role) => (
                <th
                  key={role.name}
                  scope="col"
                  onMouseEnter={() => setFocusRole(role.name)}
                  onMouseLeave={() => setFocusRole(null)}
                  className={cx(
                    "border-b border-line px-3 py-3 text-center transition-colors",
                    focusRole === role.name && "bg-panel-2",
                  )}
                  title={role.description}
                >
                  <span className="text-[12.5px] font-semibold text-ink">{role.name}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {policy.tools.map((tool, row) => {
              const { tool: name, server } = formatToolName(tool.name);
              const cost = toNumber(tool.cost_usd_per_call);
              return (
                <tr key={tool.name} className="group">
                  <th scope="row" className="border-b border-line px-5 py-3.5 font-normal group-last:border-b-0">
                    <div className="flex items-center gap-2">
                      <span
                        className={cx("size-1.5 shrink-0 rounded-full", tool.available ? "bg-good" : "bg-muted")}
                        title={tool.available ? "Offered by its server now" : "Not offered by its server now"}
                      />
                      <span className="font-semibold text-ink">{name}</span>
                      <span className="mono text-[10px] text-muted">{server}</span>
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5 pl-3.5">
                      <Chip tone={tool.access === "write" ? "accent" : "muted"} className="h-5 text-[10.5px]">
                        {tool.access}
                      </Chip>
                      <RiskChip risk={tool.risk} />
                      {cost > 0 && <span className="mono text-[10px] text-muted">{formatUsd(cost)}/call</span>}
                      {tool.requires_approval && (
                        <span className="text-[11px] text-ink-2">decided by {tool.approver_roles.join(", ")}</span>
                      )}
                    </div>
                  </th>
                  {roles.map((role, column) => {
                    const access = policy.matrix[role.name]?.[tool.name] ?? "deny";
                    return (
                      <td
                        key={role.name}
                        onMouseEnter={() => setFocusRole(role.name)}
                        onMouseLeave={() => setFocusRole(null)}
                        className={cx(
                          "border-b border-line px-3 py-3.5 text-center transition-colors group-last:border-b-0",
                          focusRole === role.name && "bg-panel-2",
                        )}
                      >
                        <motion.span
                          className="inline-flex"
                          initial={{ opacity: 0, scale: 0.6 }}
                          animate={{ opacity: 1, scale: 1 }}
                          transition={{ delay: 0.05 + row * 0.04 + column * 0.03, type: "spring", stiffness: 420, damping: 26 }}
                        >
                          <AccessCell access={access} />
                        </motion.span>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </Panel>

      <section aria-label="Roles" className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {roles.map((role) => {
          const row = policy.matrix[role.name] ?? {};
          const allowed = Object.values(row).filter((access) => access === "allow").length;
          const gated = Object.values(row).filter((access) => access === "approval").length;
          return (
            <div key={role.name} className="panel flex flex-col p-4">
              <p className="text-[14px] font-semibold text-ink">{role.name}</p>
              <p className="mt-1 flex-1 text-[12.5px] leading-relaxed text-ink-2">{role.description}</p>
              <dl className="mt-3 grid grid-cols-2 gap-2 border-t border-line pt-3 text-[12px]">
                <div>
                  <dt className="text-muted">Tools</dt>
                  <dd className="figures mt-0.5 font-semibold text-ink">
                    {allowed}
                    {gated > 0 && <span className="font-normal text-ink-2"> + {gated} gated</span>}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted">Console actions</dt>
                  <dd className="figures mt-0.5 font-semibold text-ink">{role.actions.length}</dd>
                </div>
              </dl>
              {role.actions.length > 0 && (
                <ul className="mt-3 flex flex-wrap gap-1">
                  {role.actions.map((action) => (
                    <li key={action} className="mono rounded-[5px] bg-panel-3 px-1.5 py-0.5 text-[9.5px] text-ink-2">
                      {action}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </section>
    </>
  );
}

function AccessCell({ access, compact = false }: { access: PolicyAccess; compact?: boolean }) {
  const size = compact ? "size-4" : "size-7";
  if (access === "allow") {
    return (
      <span role="img" aria-label={ACCESS_LABEL.allow} title={ACCESS_LABEL.allow} className={cx(size, "inline-flex items-center justify-center rounded-full bg-accent/15 text-accent")}>
        <IconCheck size={compact ? 10 : 14} />
      </span>
    );
  }
  if (access === "approval") {
    return (
      <span role="img" aria-label={ACCESS_LABEL.approval} title={ACCESS_LABEL.approval} className={cx(size, "inline-flex items-center justify-center rounded-full bg-warning/20 text-warning")}>
        <IconHold size={compact ? 10 : 14} />
      </span>
    );
  }
  return (
    <span role="img" aria-label={ACCESS_LABEL.deny} title={ACCESS_LABEL.deny} className={cx(size, "inline-flex items-center justify-center text-muted")}>
      <span className={cx("h-px rounded-full bg-current", compact ? "w-2" : "w-3")} />
    </span>
  );
}
