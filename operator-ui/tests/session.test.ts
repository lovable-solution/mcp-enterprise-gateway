import { EncryptJWT, SignJWT } from "jose";
import { describe, expect, it } from "vitest";

import { type ConsoleSession, openSession, sealSession, tokenClaims } from "@/lib/session";

const SECRET = "s".repeat(40);
const inAnHour = () => Math.floor(Date.now() / 1000) + 3600;

function sample(overrides: Partial<ConsoleSession> = {}): ConsoleSession {
  return {
    token: "header.payload.signature",
    subject: "ops-alice",
    role: "operator",
    tenant: "acme",
    expiresAt: inAnHour(),
    actions: ["approvals:read", "approvals:decide"],
    ...overrides,
  };
}

describe("session cookie", () => {
  it("round-trips the operator's session", async () => {
    const session = sample();
    expect(await openSession(await sealSession(session, SECRET), SECRET)).toEqual(session);
  });

  it("does not expose the token in the cookie value", async () => {
    const sealed = await sealSession(sample({ token: "very-secret-token-value" }), SECRET);
    expect(sealed).not.toContain("very-secret-token-value");
    expect(Buffer.from(sealed.split(".")[1] ?? "", "base64url").toString()).not.toContain("secret");
  });

  it("rejects tampered, foreign and expired cookies", async () => {
    const sealed = await sealSession(sample(), SECRET);
    const tampered = `${sealed.slice(0, -4)}AAAA`;
    expect(await openSession(tampered, SECRET)).toBeNull();
    expect(await openSession(sealed, "o".repeat(40))).toBeNull();
    expect(await openSession(await sealSession(sample({ expiresAt: 1_000 }), SECRET), SECRET)).toBeNull();
    expect(await openSession(undefined, SECRET)).toBeNull();
    expect(await openSession("not-a-jwe", SECRET)).toBeNull();
  });

  it("rejects a well-formed cookie missing required fields", async () => {
    const key = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(SECRET)));
    const partial = await new EncryptJWT({ role: "operator" })
      .setProtectedHeader({ alg: "dir", enc: "A256GCM" })
      .setSubject("ops-alice")
      .setExpirationTime(inAnHour())
      .encrypt(key);
    expect(await openSession(partial, SECRET)).toBeNull();
  });
});

describe("token claims", () => {
  it("reads display claims from a pasted token", async () => {
    const token = await new SignJWT({ tenant_id: "acme", role: "auditor" })
      .setProtectedHeader({ alg: "HS256" })
      .setSubject("audit-bob")
      .setExpirationTime(inAnHour())
      .sign(new TextEncoder().encode(SECRET));
    expect(tokenClaims(token)).toMatchObject({ subject: "audit-bob", role: "auditor", tenant: "acme" });
    expect(tokenClaims("garbage")).toBeNull();
  });
});
