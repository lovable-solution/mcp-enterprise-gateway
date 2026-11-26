"use client";

import { MotionConfig } from "motion/react";
import { useRouter } from "next/navigation";
import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";

import {
  type BudgetState,
  type DashboardState,
  type Kpis,
  budgetState,
  initialState,
  kpis as computeKpis,
  pendingApprovals,
  recentDecisions,
  reduce,
  sessionsByActivity,
} from "@/lib/dashboard";
import { formatToolName } from "@/lib/format";
import type { Approval, GatewayEvent, Operator, PolicySummary, SessionStatus } from "@/lib/types";
import { GATEWAY_EVENT_TYPES } from "@/lib/types";

// One live connection per console tab: the REST snapshot plus the gateway's event stream,
// folded by the pure reducer in lib/dashboard. Every view reads from here, so moving
// between pages never drops the stream or the state.

const SAFETY_REFRESH_MS = 30_000;
const POLICY_REFRESH_MS = 5 * 60_000;
const TOAST_MS = 6_000;
/** History a fresh console loads from the gateway's stream, so a reload is not blank. */
export const HISTORY_MS = 15 * 60_000;
/** Events older than this on arrival are history: no toast, no animation. */
export const FRESH_MS = 15_000;

export function isFresh(event: GatewayEvent, now = Date.now()): boolean {
  return now - Date.parse(event.occurred_at) < FRESH_MS;
}

export type LiveState = "connecting" | "live" | "reconnecting" | "offline";
export type DecideResult = { ok: true; approval: Approval } | { ok: false; message: string };
export type ToastTone = "accent" | "good" | "warning" | "critical";

export interface Toast {
  id: number;
  tone: ToastTone;
  title: string;
  detail?: string;
  href?: string;
}

export interface ConsoleValue {
  operator: Operator;
  canDecide: boolean;
  canReset: boolean;
  state: DashboardState;
  now: number;
  live: LiveState;
  error: string | null;
  ready: boolean;
  policy: PolicySummary | null;
  pending: Approval[];
  decided: Approval[];
  sessions: SessionStatus[];
  kpis: Kpis;
  stateOf: (status: SessionStatus) => BudgetState;
  loadApproval: (id: string) => void;
  decide: (id: string, decision: "approve" | "reject", reason: string) => Promise<DecideResult>;
  resetBreaker: (sessionId: string) => Promise<string | null>;
  dismissAlert: (id: string) => void;
  signOut: () => Promise<void>;
  toasts: Toast[];
  notify: (toast: Omit<Toast, "id">) => void;
  dismissToast: (id: number) => void;
}

const ConsoleContext = createContext<ConsoleValue | null>(null);

export function useConsole(): ConsoleValue {
  const value = useContext(ConsoleContext);
  if (value === null) {
    throw new Error("useConsole() needs a <ConsoleProvider>");
  }
  return value;
}

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

function toolLabel(tool: unknown): string {
  return typeof tool === "string" ? formatToolName(tool).tool : "a tool call";
}

export function ConsoleProvider({ operator, children }: { operator: Operator; children: ReactNode }) {
  const router = useRouter();
  const [state, dispatch] = useReducer(reduce, initialState);
  const [now, setNow] = useState(() => Date.now());
  const [live, setLive] = useState<LiveState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [policy, setPolicy] = useState<PolicySummary | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastSeq = useRef(0);
  const canDecide = operator.actions.includes("approvals:decide");
  const canReset = operator.actions.includes("circuit_breakers:reset");

  const dismissToast = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const notify = useCallback(
    (toast: Omit<Toast, "id">) => {
      toastSeq.current += 1;
      const id = toastSeq.current;
      setToasts((current) => [...current.slice(-3), { ...toast, id }]);
      setTimeout(() => dismissToast(id), TOAST_MS);
    },
    [dismissToast],
  );

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

  const loadPolicy = useCallback(async () => {
    try {
      const summary = await getJson<PolicySummary>("/api/policy");
      if (summary) {
        setPolicy(summary);
      }
    } catch {
      // The policy view says when it is unavailable; the rest of the console works.
    }
  }, [getJson]);

  const refresh = useCallback(async () => {
    try {
      await Promise.all([loadApprovals(), loadSessions()]);
      setError(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "the gateway could not be reached");
    } finally {
      setReady(true);
    }
  }, [loadApprovals, loadSessions]);

  // The stream must not reconnect on every render, so it reads these through a ref.
  const handlers = useRef({ refresh, loadApprovals, loadSessions, signedOut, notify });
  useEffect(() => {
    handlers.current = { refresh, loadApprovals, loadSessions, signedOut, notify };
  }, [refresh, loadApprovals, loadSessions, signedOut, notify]);

  useEffect(() => {
    void refresh();
    void loadPolicy();
    const safety = setInterval(() => void refresh(), SAFETY_REFRESH_MS);
    const policyTimer = setInterval(() => void loadPolicy(), POLICY_REFRESH_MS);
    const clock = setInterval(() => setNow(Date.now()), 1_000);
    return () => {
      clearInterval(safety);
      clearInterval(policyTimer);
      clearInterval(clock);
    };
  }, [refresh, loadPolicy]);

  useEffect(() => {
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let approvalsTimer: ReturnType<typeof setTimeout> | undefined;
    let sessionsTimer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    let stopped = false;
    let lastId: string | null = null;

    const soon = (timer: ReturnType<typeof setTimeout> | undefined, run: () => Promise<void>) => {
      clearTimeout(timer);
      return setTimeout(() => void run().catch(() => undefined), 300);
    };

    const announce = (event: GatewayEvent) => {
      if (!isFresh(event)) {
        return; // history loaded at connect: no notifications for the past
      }
      const { notify: toast } = handlers.current;
      const data = event.data;
      if (event.type === "approval.requested") {
        toast({
          tone: "warning",
          title: `${toolLabel(data.tool)} is waiting for review`,
          detail: "An agent's call is held until someone decides it.",
          href: typeof data.approval_id === "string" ? `/approvals?id=${data.approval_id}` : "/approvals",
        });
      } else if (event.type === "approval.executed") {
        toast({ tone: "good", title: `${toolLabel(data.tool)} executed`, detail: "The approved payload ran once." });
      } else if (event.type === "approval.failed") {
        toast({ tone: "critical", title: `${toolLabel(data.tool)} failed after approval`, href: "/approvals" });
      } else if (event.type === "circuit_breaker.tripped") {
        toast({
          tone: "critical",
          title: `Loop stopped on ${toolLabel(data.tool)}`,
          detail: "The session is paused until the breaker cools down or is reset.",
          href: "/sessions",
        });
      }
    };

    const connect = () => {
      // First connect: the last 15 minutes, then live. Later reconnects: exactly where the
      // stream stopped (the browser's own retries send Last-Event-ID themselves).
      source = new EventSource(
        lastId === null
          ? `/api/events?since=${Date.now() - HISTORY_MS}`
          : `/api/events?after=${encodeURIComponent(lastId)}`,
      );
      source.onopen = () => {
        if (failures > 0) {
          void handlers.current.refresh(); // catch up on anything the outage hid
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
            handlers.current.signedOut();
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
          lastId = event.id ?? lastId;
          dispatch({ type: "event", event, at: Date.now() });
          announce(event);
          if (event.type.startsWith("approval.")) {
            approvalsTimer = soon(approvalsTimer, () => handlers.current.loadApprovals());
          }
          if (event.type.startsWith("circuit_breaker.")) {
            sessionsTimer = soon(sessionsTimer, () => handlers.current.loadSessions());
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

  const loadApproval = useCallback(
    (id: string) => {
      void getJson<Approval>(`/api/approvals/${encodeURIComponent(id)}`)
        .then((approval) => approval && dispatch({ type: "approval/loaded", approval }))
        .catch(() => undefined);
    },
    [getJson],
  );

  const decide = useCallback(
    async (id: string, decision: "approve" | "reject", reason: string): Promise<DecideResult> => {
      try {
        const response = await fetch(`/api/approvals/${encodeURIComponent(id)}/decide`, {
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
        notify(
          decision === "approve"
            ? { tone: "accent", title: `Approved ${toolLabel(approval.tool_name)}`, detail: "The gateway is running the stored payload." }
            : { tone: "critical", title: `Rejected ${toolLabel(approval.tool_name)}`, detail: "The agent receives your reason." },
        );
        void loadSessions().catch(() => undefined);
        return { ok: true, approval };
      } catch {
        return { ok: false, message: "The console could not reach the gateway." };
      }
    },
    [signedOut, loadSessions, notify],
  );

  const resetBreaker = useCallback(
    async (sessionId: string): Promise<string | null> => {
      try {
        const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/breaker`, { method: "POST" });
        if (!response.ok) {
          return problemText(response);
        }
        await loadSessions();
        notify({ tone: "good", title: "Circuit breaker reset", detail: "The session can call tools again." });
        return null;
      } catch {
        return "The console could not reach the gateway.";
      }
    },
    [loadSessions, notify],
  );

  const dismissAlert = useCallback((id: string) => dispatch({ type: "alert/dismiss", id }), []);

  const signOut = useCallback(async () => {
    await fetch("/api/session", { method: "DELETE" }).catch(() => undefined);
    signedOut();
  }, [signedOut]);

  const pending = useMemo(() => pendingApprovals(state), [state]);
  const decided = useMemo(() => recentDecisions(state, 30), [state]);
  const sessions = useMemo(() => sessionsByActivity(state), [state]);
  const kpis = useMemo(() => computeKpis(state, now), [state, now]);
  const stateOf = useCallback((status: SessionStatus) => budgetState(state, status), [state]);

  const value = useMemo<ConsoleValue>(
    () => ({
      operator,
      canDecide,
      canReset,
      state,
      now,
      live,
      error,
      ready,
      policy,
      pending,
      decided,
      sessions,
      kpis,
      stateOf,
      loadApproval,
      decide,
      resetBreaker,
      dismissAlert,
      signOut,
      toasts,
      notify,
      dismissToast,
    }),
    [
      operator,
      canDecide,
      canReset,
      state,
      now,
      live,
      error,
      ready,
      policy,
      pending,
      decided,
      sessions,
      kpis,
      stateOf,
      loadApproval,
      decide,
      resetBreaker,
      dismissAlert,
      signOut,
      toasts,
      notify,
      dismissToast,
    ],
  );

  return (
    <ConsoleContext.Provider value={value}>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </ConsoleContext.Provider>
  );
}
