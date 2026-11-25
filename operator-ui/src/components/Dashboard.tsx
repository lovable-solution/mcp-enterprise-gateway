"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import {
  budgetState,
  initialState,
  kpis as computeKpis,
  pendingApprovals,
  recentDecisions,
  reduce,
  sessionsByActivity,
} from "@/lib/dashboard";
import type { Approval, GatewayEvent, Operator, SessionStatus } from "@/lib/types";
import { GATEWAY_EVENT_TYPES } from "@/lib/types";

import { AlertsPanel, EventFeed } from "./AlertsPanel";
import { ApprovalDetail, ApprovalList, type DecideResult, RecentDecisions } from "./ApprovalQueue";
import { Header, type LiveState } from "./Header";
import { KpiRow } from "./KpiRow";
import { SessionsTable } from "./SessionsTable";

const SAFETY_REFRESH_MS = 30_000;

async function problemText(response: Response): Promise<string> {
  const body = (await response.json().catch(() => null)) as { detail?: string; title?: string } | null;
  return body?.detail ?? body?.title ?? `request failed (${response.status})`;
}

function parseEvent(raw: string, lastEventId: string): GatewayEvent | null {
  try {
    const event = JSON.parse(raw) as GatewayEvent;
    return { ...event, id: lastEventId || event.id };
  } catch {
    return null;
  }
}

export function Dashboard({ operator }: { operator: Operator }) {
  const router = useRouter();
  const [state, dispatch] = useReducer(reduce, initialState);
  const [now, setNow] = useState(() => Date.now());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [live, setLive] = useState<LiveState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const canDecide = operator.actions.includes("approvals:decide");
  const canReset = operator.actions.includes("circuit_breakers:reset");

  const signedOut = useCallback(() => {
    router.replace("/signin");
    router.refresh();
  }, [router]);

  const getJson = useCallback(
    async <T,>(url: string): Promise<T | null> => {
      const response = await fetch(url, { cache: "no-store" });
      if (response.status === 401) {
        signedOut();
        return null;
      }
      if (!response.ok) {
        throw new Error(await problemText(response));
      }
      return (await response.json()) as T;
    },
    [signedOut],
  );

  const loadApprovals = useCallback(async () => {
    const [pending, recent] = await Promise.all([
      getJson<{ approvals: Approval[] }>("/api/approvals?status=PENDING_APPROVAL&limit=200"),
      getJson<{ approvals: Approval[] }>("/api/approvals?limit=50"),
    ]);
    dispatch({
      type: "approvals/loaded",
      approvals: [...(recent?.approvals ?? []), ...(pending?.approvals ?? [])],
    });
  }, [getJson]);

  const loadSessions = useCallback(async () => {
    const sessions = await getJson<{ sessions: SessionStatus[] }>("/api/sessions");
    if (sessions) {
      dispatch({ type: "sessions/loaded", sessions: sessions.sessions, at: Date.now() });
    }
  }, [getJson]);

  const refresh = useCallback(async () => {
    try {
      await Promise.all([loadApprovals(), loadSessions()]);
      setError(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "the gateway could not be reached");
    }
  }, [loadApprovals, loadSessions]);

  // The loaders change identity only with the router; the event stream must not reconnect
  // on every render, so it reads them through a ref.
  const loaders = useRef({ refresh, loadApprovals, loadSessions, signedOut });
  useEffect(() => {
    loaders.current = { refresh, loadApprovals, loadSessions, signedOut };
  }, [refresh, loadApprovals, loadSessions, signedOut]);

  useEffect(() => {
    void refresh();
    const safety = setInterval(() => void refresh(), SAFETY_REFRESH_MS);
    const clock = setInterval(() => setNow(Date.now()), 5_000);
    return () => {
      clearInterval(safety);
      clearInterval(clock);
    };
  }, [refresh]);

  useEffect(() => {
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let approvalsTimer: ReturnType<typeof setTimeout> | undefined;
    let sessionsTimer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    let stopped = false;

    const soon = (timer: ReturnType<typeof setTimeout> | undefined, run: () => Promise<void>) => {
      clearTimeout(timer);
      return setTimeout(() => void run().catch(() => undefined), 300);
    };

    const connect = () => {
      source = new EventSource("/api/events");
      source.onopen = () => {
        if (failures > 0) {
          void loaders.current.refresh(); // catch up on anything the outage hid
        }
        failures = 0;
        setLive("live");
      };
      source.onerror = () => {
        failures += 1;
        if (source?.readyState !== EventSource.CLOSED) {
          setLive("reconnecting"); // the browser retries with Last-Event-ID
          return;
        }
        // The console refused the stream: signed out, or the gateway is down.
        setLive("offline");
        source.close();
        void fetch("/api/session", { cache: "no-store" }).then((response) => {
          if (response.status === 401) {
            loaders.current.signedOut();
          }
        });
        if (!stopped) {
          retry = setTimeout(connect, Math.min(30_000, 1_000 * 2 ** failures));
        }
      };
      for (const type of GATEWAY_EVENT_TYPES) {
        source.addEventListener(type, (message: MessageEvent<string>) => {
          const event = parseEvent(message.data, message.lastEventId);
          if (event === null) {
            return;
          }
          dispatch({ type: "event", event, at: Date.now() });
          if (event.type.startsWith("approval.")) {
            approvalsTimer = soon(approvalsTimer, () => loaders.current.loadApprovals());
          }
          if (event.type.startsWith("circuit_breaker.")) {
            sessionsTimer = soon(sessionsTimer, () => loaders.current.loadSessions());
          }
        });
      }
    };
    connect();
    return () => {
      stopped = true;
      source?.close();
      clearTimeout(retry);
      clearTimeout(approvalsTimer);
      clearTimeout(sessionsTimer);
    };
  }, []);

  const pending = useMemo(() => pendingApprovals(state), [state]);
  const decided = useMemo(() => recentDecisions(state, 8), [state]);
  const sessions = useMemo(() => sessionsByActivity(state), [state]);
  const figures = useMemo(() => computeKpis(state, now), [state, now]);
  const oldest = pending[0];
  const oldestPendingSeconds = oldest ? Math.max(0, (now - Date.parse(oldest.created_at)) / 1000) : null;

  // Keep a held call under review; otherwise show the oldest one.
  const activeId = selectedId && state.approvals[selectedId] ? selectedId : (oldest?.id ?? null);
  const selected = activeId ? state.approvals[activeId] : undefined;

  const select = useCallback(
    (id: string) => {
      setSelectedId(id);
      void getJson<Approval>(`/api/approvals/${id}`)
        .then((approval) => approval && dispatch({ type: "approval/loaded", approval }))
        .catch(() => undefined);
    },
    [getJson],
  );

  // Load the audit trail of whichever call is on screen.
  useEffect(() => {
    if (activeId && state.approvals[activeId]?.audit === undefined) {
      void getJson<Approval>(`/api/approvals/${activeId}`)
        .then((approval) => approval && dispatch({ type: "approval/loaded", approval }))
        .catch(() => undefined);
    }
  }, [activeId, state.approvals, getJson]);

  const decide = useCallback(
    async (id: string, decision: "approve" | "reject", reason: string): Promise<DecideResult> => {
      try {
        const response = await fetch(`/api/approvals/${id}/decide`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(reason ? { decision, reason } : { decision }),
        });
        if (response.status === 401) {
          signedOut();
          return { ok: false, message: "Your session has ended. Sign in again." };
        }
        if (!response.ok) {
          return { ok: false, message: await problemText(response) };
        }
        const approval = (await response.json()) as Approval;
        dispatch({ type: "approval/loaded", approval });
        setSelectedId(approval.id);
        void loadSessions().catch(() => undefined);
        return { ok: true, approval };
      } catch {
        return { ok: false, message: "The console could not reach the gateway." };
      }
    },
    [signedOut, loadSessions],
  );

  const resetBreaker = useCallback(
    async (sessionId: string): Promise<string | null> => {
      try {
        const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/breaker`, {
          method: "POST",
        });
        if (!response.ok) {
          return problemText(response);
        }
        await loadSessions();
        return null;
      } catch {
        return "The console could not reach the gateway.";
      }
    },
    [loadSessions],
  );

  const signOut = useCallback(async () => {
    await fetch("/api/session", { method: "DELETE" }).catch(() => undefined);
    signedOut();
  }, [signedOut]);

  return (
    <div className="mx-auto max-w-[96rem] px-4 pb-12 sm:px-6">
      <Header operator={operator} live={live} onSignOut={signOut} />
      {error && (
        <p
          role="alert"
          className="mb-4 rounded-lg px-4 py-2.5 text-sm text-ink"
          style={{ background: "color-mix(in oklab, var(--critical) 14%, transparent)" }}
        >
          The gateway is not answering: {error}. Showing the last known state.
        </p>
      )}
      <KpiRow kpis={figures} oldestPendingSeconds={oldestPendingSeconds} knownSessions={sessions.length} />

      <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <section aria-labelledby="queue-heading">
          <h2 id="queue-heading" className="mb-3 text-base font-semibold">
            Approval queue{" "}
            <span className="font-normal text-muted" aria-live="polite">
              {pending.length} waiting
            </span>
          </h2>
          <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
            <div>
              <ApprovalList approvals={pending} selectedId={activeId} onSelect={select} now={now} />
              <RecentDecisions approvals={decided} onSelect={select} now={now} />
            </div>
            <div>
              {selected ? (
                <ApprovalDetail
                  key={selected.id}
                  approval={selected}
                  canDecide={canDecide}
                  onDecide={(decision, reason) => decide(selected.id, decision, reason)}
                  now={now}
                />
              ) : (
                <p className="rounded-xl border border-dashed border-hairline px-4 py-10 text-center text-sm text-ink-2">
                  Select a call to review its exact payload.
                </p>
              )}
            </div>
          </div>
        </section>

        <aside className="flex flex-col gap-6">
          <AlertsPanel
            alerts={state.alerts}
            canReset={canReset}
            onReset={resetBreaker}
            onDismiss={(id) => dispatch({ type: "alert/dismiss", id })}
            now={now}
          />
          <EventFeed events={state.events} />
        </aside>
      </div>

      <section aria-labelledby="sessions-heading" className="mt-8">
        <h2 id="sessions-heading" className="mb-3 text-base font-semibold">
          Agent sessions <span className="font-normal text-muted">token and cost burn, live</span>
        </h2>
        <SessionsTable
          sessions={sessions}
          stateOf={(status) => budgetState(state, status)}
          canReset={canReset}
          onReset={resetBreaker}
          now={now}
        />
      </section>
    </div>
  );
}
