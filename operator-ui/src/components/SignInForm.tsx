"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

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

  const field = "mt-1 w-full rounded-md border border-hairline bg-surface px-3 py-2 text-sm text-ink";
  return (
    <form onSubmit={submit} className="mt-8 rounded-xl border border-hairline bg-surface p-6 shadow-sm">
      {devSignIn && (
        <div role="tablist" aria-label="Sign-in method" className="mb-5 flex gap-1 rounded-lg bg-page p-1 text-sm">
          {(["dev", "token"] as const).map((option) => (
            <button
              key={option}
              type="button"
              role="tab"
              aria-selected={mode === option}
              onClick={() => setMode(option)}
              className={`flex-1 rounded-md px-3 py-1.5 ${mode === option ? "bg-surface font-medium shadow-sm" : "text-ink-2"}`}
            >
              {option === "dev" ? "Development sign-in" : "Access token"}
            </button>
          ))}
        </div>
      )}

      {mode === "dev" ? (
        <>
          <label className="block text-sm font-medium" htmlFor="subject">
            Reviewer id
          </label>
          <input id="subject" className={field} value={subject} onChange={(e) => setSubject(e.target.value)} required />
          <label className="mt-4 block text-sm font-medium" htmlFor="role">
            Role
          </label>
          <select id="role" className={field} value={role} onChange={(e) => setRole(e.target.value as typeof role)}>
            <option value="operator">operator: decides approvals, resets breakers</option>
            <option value="auditor">auditor: read-only</option>
          </select>
          <p className="mt-3 text-xs text-ink-2">
            Local stack only: the console mints a short-lived token with the gateway&apos;s development
            secret. Deployed consoles sign in through the identity provider.
          </p>
        </>
      ) : (
        <>
          <label className="block text-sm font-medium" htmlFor="token">
            Gateway access token
          </label>
          <textarea
            id="token"
            className={`${field} h-28 font-mono text-xs`}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="eyJhbGciOi..."
            required
            spellCheck={false}
            autoComplete="off"
          />
          <p className="mt-3 text-xs text-ink-2">
            A JWT from your identity provider with an operator or auditor role. It is kept in an
            encrypted, http-only cookie and never exposed to page scripts.
          </p>
        </>
      )}

      {error && (
        <p role="alert" className="mt-4 rounded-md px-3 py-2 text-sm text-ink" style={{ background: "color-mix(in oklab, var(--critical) 14%, transparent)" }}>
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={busy}
        className="mt-5 w-full rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
      >
        {busy ? "Signing in..." : "Sign in"}
      </button>
    </form>
  );
}
