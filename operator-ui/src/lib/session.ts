import { EncryptJWT, decodeJwt, jwtDecrypt } from "jose";

import type { Operator } from "./types";

// The operator's session: their gateway bearer token plus what the UI displays, sealed
// into one encrypted cookie (JWE, dir + A256GCM). Scripts in the page cannot read it
// (httpOnly), other sites cannot send it (SameSite=Strict), and tampering fails
// decryption. The gateway still validates the token itself on every request.

export const SESSION_COOKIE = "mcpgw_console";

export interface ConsoleSession extends Operator {
  token: string;
}

async function keyFrom(secret: string): Promise<Uint8Array> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(secret));
  return new Uint8Array(digest);
}

export async function sealSession(session: ConsoleSession, secret: string): Promise<string> {
  return new EncryptJWT({
    token: session.token,
    role: session.role,
    tenant: session.tenant,
    actions: session.actions,
  })
    .setProtectedHeader({ alg: "dir", enc: "A256GCM" })
    .setSubject(session.subject)
    .setIssuedAt()
    .setExpirationTime(session.expiresAt)
    .encrypt(await keyFrom(secret));
}

export async function openSession(
  sealed: string | undefined,
  secret: string,
): Promise<ConsoleSession | null> {
  if (!sealed) {
    return null;
  }
  try {
    const { payload } = await jwtDecrypt(sealed, await keyFrom(secret), {
      contentEncryptionAlgorithms: ["A256GCM"],
      keyManagementAlgorithms: ["dir"],
    });
    const { token, role, tenant, sub, exp, actions } = payload;
    if (
      typeof token !== "string" ||
      typeof role !== "string" ||
      typeof tenant !== "string" ||
      typeof sub !== "string" ||
      typeof exp !== "number" ||
      !Array.isArray(actions) ||
      !actions.every((action) => typeof action === "string")
    ) {
      return null;
    }
    return { token, role, tenant, subject: sub, expiresAt: exp, actions: actions as string[] };
  } catch {
    return null; // tampered, expired, or sealed with another secret
  }
}

/** Display claims of a token pasted at sign-in. Not a verification: the gateway verifies
 * the token (and the console checks it works) before a session is created. */
export function tokenClaims(token: string): Partial<Omit<Operator, "actions">> | null {
  try {
    const claims = decodeJwt(token);
    return {
      subject: typeof claims.sub === "string" ? claims.sub : undefined,
      role: typeof claims.role === "string" ? claims.role : undefined,
      tenant: typeof claims.tenant_id === "string" ? claims.tenant_id : undefined,
      expiresAt: typeof claims.exp === "number" ? claims.exp : undefined,
    };
  } catch {
    return null;
  }
}
