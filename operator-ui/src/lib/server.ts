import "server-only";

import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { config } from "./config";
import { type ConsoleSession, SESSION_COOKIE, openSession, sealSession } from "./session";

const GATEWAY_TIMEOUT_MS = 15_000;

export async function currentSession(): Promise<ConsoleSession | null> {
  const store = await cookies();
  const session = await openSession(store.get(SESSION_COOKIE)?.value, config().sessionSecret);
  if (session === null || session.expiresAt * 1000 <= Date.now()) {
    return null;
  }
  return session;
}

export async function startSession(response: NextResponse, session: ConsoleSession): Promise<void> {
  response.cookies.set(SESSION_COOKIE, await sealSession(session, config().sessionSecret), {
    httpOnly: true,
    sameSite: "strict",
    secure: config().cookieSecure,
    path: "/",
    expires: new Date(session.expiresAt * 1000),
  });
}

export function endSession(response: NextResponse): void {
  response.cookies.set(SESSION_COOKIE, "", {
    httpOnly: true,
    sameSite: "strict",
    secure: config().cookieSecure,
    path: "/",
    maxAge: 0,
  });
}

/** RFC 9457 problem details, the same format the gateway itself answers with. */
export function problem(status: number, title: string, detail?: string): NextResponse {
  return NextResponse.json(
    { type: "about:blank", title, status, ...(detail ? { detail } : {}) },
    { status, headers: { "Content-Type": "application/problem+json" } },
  );
}

/** A request to the gateway's control-plane API as the signed-in operator. */
export async function gateway(
  session: ConsoleSession,
  path: string,
  init: RequestInit & { timeoutMs?: number } = {},
): Promise<Response> {
  const { timeoutMs = GATEWAY_TIMEOUT_MS, headers, ...rest } = init;
  return fetch(`${config().gatewayUrl}${path}`, {
    ...rest,
    cache: "no-store",
    redirect: "error",
    signal: rest.signal ?? AbortSignal.timeout(timeoutMs),
    headers: {
      Accept: "application/json",
      ...headers,
      Authorization: `Bearer ${session.token}`,
    },
  });
}

/** Pass the gateway's answer through (status and JSON body); an expired or revoked token
 * also ends the console session. */
export async function relay(upstream: Response): Promise<NextResponse> {
  const body = await upstream.text();
  const response = new NextResponse(body, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") ?? "application/json",
      "Cache-Control": "no-store",
    },
  });
  if (upstream.status === 401) {
    endSession(response);
  }
  return response;
}

/** Run a handler as the signed-in operator; gateway outages become a 502 problem. */
export async function asOperator(
  handler: (session: ConsoleSession) => Promise<NextResponse>,
): Promise<NextResponse> {
  const session = await currentSession();
  if (session === null) {
    return problem(401, "Sign-in required");
  }
  try {
    return await handler(session);
  } catch (error) {
    const reason = error instanceof Error ? error.name : "error";
    console.error("gateway request failed:", reason);
    return problem(502, "Gateway unavailable", `the gateway did not answer (${reason})`);
  }
}
