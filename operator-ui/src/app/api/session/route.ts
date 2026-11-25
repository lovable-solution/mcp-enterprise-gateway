import { SignJWT } from "jose";
import { type NextRequest, NextResponse } from "next/server";

import { config } from "@/lib/config";
import { currentSession, endSession, problem, startSession } from "@/lib/server";
import { type ConsoleSession, tokenClaims } from "@/lib/session";
import { isSameOrigin, signInSchema } from "@/lib/validation";

const DEV_TOKEN_SECONDS = 4 * 60 * 60;

interface PolicySummary {
  roles?: { name: string; actions: string[] }[];
}

/** Who is signed in (never the token). */
export async function GET(): Promise<NextResponse> {
  const session = await currentSession();
  if (session === null) {
    return problem(401, "Sign-in required");
  }
  const { subject, role, tenant, expiresAt, actions } = session;
  return NextResponse.json({ subject, role, tenant, expiresAt, actions });
}

/** Sign in: with a token from the identity provider, or (local development only) by
 * minting one with the gateway's development secret. Either way the token must work
 * against the gateway before a session exists. */
export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isSameOrigin(request)) {
    return problem(403, "Cross-origin request refused");
  }
  const parsed = signInSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return problem(422, "Invalid sign-in", parsed.error.issues[0]?.message);
  }
  const cfg = config();
  let session: ConsoleSession;
  if (parsed.data.mode === "dev") {
    if (!cfg.devSignIn || !cfg.jwtSecret) {
      return problem(403, "Development sign-in is disabled");
    }
    const now = Math.floor(Date.now() / 1000);
    const expiresAt = now + DEV_TOKEN_SECONDS;
    const token = await new SignJWT({ tenant_id: cfg.tenant, role: parsed.data.role })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setIssuer(cfg.jwtIssuer)
      .setAudience(cfg.jwtAudience)
      .setSubject(parsed.data.subject)
      .setIssuedAt(now)
      .setExpirationTime(expiresAt)
      .setJti(crypto.randomUUID())
      .sign(new TextEncoder().encode(cfg.jwtSecret));
    session = {
      token,
      subject: parsed.data.subject,
      role: parsed.data.role,
      tenant: cfg.tenant,
      expiresAt,
      actions: [],
    };
  } else {
    const claims = tokenClaims(parsed.data.token);
    if (!claims?.subject || !claims.role || !claims.tenant || !claims.expiresAt) {
      return problem(422, "Invalid sign-in", "the token lacks sub, role, tenant_id or exp");
    }
    if (claims.expiresAt * 1000 <= Date.now()) {
      return problem(422, "Invalid sign-in", "the token has expired");
    }
    session = {
      token: parsed.data.token,
      subject: claims.subject,
      role: claims.role,
      tenant: claims.tenant,
      expiresAt: claims.expiresAt,
      actions: [],
    };
  }

  // The console needs policy:read (operators and auditors have it; agents do not).
  let check: Response;
  try {
    check = await fetch(`${cfg.gatewayUrl}/v1/policy`, {
      headers: { Authorization: `Bearer ${session.token}`, Accept: "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    return problem(502, "Gateway unavailable", "could not verify the token with the gateway");
  }
  if (check.status === 401) {
    return problem(401, "Token rejected", "the gateway did not accept this token");
  }
  if (check.status === 403) {
    return problem(403, "Not a console role", `role '${session.role}' may not use the operator console`);
  }
  if (!check.ok) {
    return problem(502, "Gateway unavailable", `the gateway answered ${check.status}`);
  }
  const policy = (await check.json().catch(() => null)) as PolicySummary | null;
  session.actions = policy?.roles?.find((entry) => entry.name === session.role)?.actions ?? [];
  const response = NextResponse.json({
    subject: session.subject,
    role: session.role,
    tenant: session.tenant,
    expiresAt: session.expiresAt,
    actions: session.actions,
  });
  await startSession(response, session);
  return response;
}

export async function DELETE(request: NextRequest): Promise<NextResponse> {
  if (!isSameOrigin(request)) {
    return problem(403, "Cross-origin request refused");
  }
  const response = new NextResponse(null, { status: 204 });
  endSession(response);
  return response;
}
