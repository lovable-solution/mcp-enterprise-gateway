"use client";

import { AnimatePresence, motion } from "motion/react";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { cx } from "@/lib/cx";

import { buttonClass } from "../ui/Button";
import { IconAlert, IconArrowRight, IconKey } from "../ui/Icons";

const FIELD =
  "mt-1.5 w-full rounded-[10px] bg-panel-2 px-3.5 text-[14px] text-ink shadow-[0_0_0_1px_var(--line-strong)] outline-none transition-shadow placeholder:text-muted focus:shadow-[0_0_0_1.5px_var(--accent),0_0_0_5px_color-mix(in_oklab,var(--accent)_16%,transparent)]";

export function SignInForm({ devSignIn }: { devSignIn: boolean }) {
  const router = useRouter();
  const [mode, setMode] = useState<"dev" | "token">(devSignIn ? "dev" : "token");
  const [subject, setSubject] = useState("ops-alice");
  const [role, setRole] = useState<"operator" | "auditor">("operator");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const body = mode === "dev" ? { mode, subject, role } : { mode, token };
    try {
      const response = await fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const problem = (await response.json().catch(() => ({}))) as { title?: string; detail?: string };
        setError(problem.detail ?? problem.title ?? `sign-in failed (${response.status})`);
        return;
      }
      router.replace("/");
      router.refresh();
    } catch {
      setError("the console could not be reached");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-8">
      {devSignIn && (
        <div role="tablist" aria-label="Sign-in method" className="mb-6 flex rounded-[10px] bg-panel-3/70 p-1 text-[13px]">
          {(["dev", "token"] as const).map((option) => (
            <button
              key={option}
              type="button"
              role="tab"
              aria-selected={mode === option}
              onClick={() => setMode(option)}
              className={cx(
                "relative h-8 flex-1 rounded-lg font-medium transition-colors",
                mode === option ? "text-ink" : "text-muted hover:text-ink-2",
              )}
            >
              {mode === option && (
                <motion.span
                  layoutId="signin-mode"
                  className="absolute inset-0 rounded-lg bg-panel shadow-[var(--shadow-panel)]"
                  transition={{ type: "spring", stiffness: 500, damping: 38 }}
                />
              )}
              <span className="relative">{option === "dev" ? "Development sign-in" : "Access token"}</span>
            </button>
          ))}
        </div>
      )}

      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={mode}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.18 }}
        >
          {mode === "dev" ? (
            <>
              <label className="block text-[13px] font-medium text-ink" htmlFor="subject">
                Reviewer id
              </label>
              <input
                id="subject"
                className={cx(FIELD, "h-11")}
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                autoComplete="username"
                required
              />
              <label className="mt-4 block text-[13px] font-medium text-ink" htmlFor="role">
                Role
              </label>
              <select
                id="role"
                className={cx(FIELD, "h-11 appearance-none bg-[length:16px] bg-[right_12px_center] bg-no-repeat")}
                style={{
                  backgroundImage:
                    "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='%23888' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M3.5 6 8 10.5 12.5 6'/%3E%3C/svg%3E\")",
                }}
                value={role}
                onChange={(e) => setRole(e.target.value as typeof role)}
              >
                <option value="operator">operator · decides approvals, resets breakers</option>
                <option value="auditor">auditor · read-only</option>
              </select>
              <p className="mt-4 flex gap-2.5 rounded-[10px] bg-panel-2 p-3 text-[12px] leading-relaxed text-ink-2">
                <IconKey size={14} className="mt-0.5 shrink-0 text-muted" />
                Local stack only: the console mints a short-lived token with the gateway&apos;s development secret.
                Deployed consoles sign in through your identity provider.
              </p>
            </>
          ) : (
            <>
              <label className="block text-[13px] font-medium text-ink" htmlFor="token">
                Gateway access token
              </label>
              <textarea
                id="token"
                className={cx(FIELD, "mono h-32 resize-none py-3 text-[11.5px] leading-relaxed")}
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="eyJhbGciOi…"
                required
                spellCheck={false}
                autoComplete="off"
              />
              <p className="mt-3 text-[12px] leading-relaxed text-ink-2">
                A JWT from your identity provider with an operator or auditor role. The console keeps it in an
                encrypted, http-only cookie; page scripts never see it.
              </p>
            </>
          )}
        </motion.div>
      </AnimatePresence>

      <AnimatePresence>
        {error && (
          <motion.p
            role="alert"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="mt-4 flex items-start gap-2 overflow-hidden rounded-[10px] bg-critical/10 px-3.5 py-2.5 text-[13px] text-ink"
          >
            <IconAlert size={14} className="mt-0.5 shrink-0 text-critical" />
            {error}
          </motion.p>
        )}
      </AnimatePresence>

      <button type="submit" disabled={busy} className={buttonClass("primary", "lg", "group mt-6 w-full")}>
        {busy ? "Signing in…" : "Sign in"}
        {!busy && <IconArrowRight size={15} className="transition-transform duration-200 group-hover:translate-x-0.5" />}
      </button>
    </form>
  );
}
