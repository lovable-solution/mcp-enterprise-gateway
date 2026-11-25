import "server-only";

import { z } from "zod";

// Server-side configuration, read once from the environment. Nothing here reaches the
// browser: the console is a backend-for-frontend.
const schema = z
  .object({
    GATEWAY_URL: z.url().default("http://localhost:8000"),
    /** Encrypts the session cookie (which carries the operator's gateway token). */
    SESSION_SECRET: z.string().min(32, "SESSION_SECRET must be at least 32 characters"),
    /** Local development only: sign in by minting a token with the gateway's HS256 secret. */
    DEV_SIGNIN: z.enum(["true", "false"]).default("false"),
    GATEWAY_JWT_SECRET: z.string().min(32).optional(),
    GATEWAY_JWT_ISSUER: z.string().min(1).default("http://localhost/dev-idp"),
    GATEWAY_JWT_AUDIENCE: z.string().min(1).default("mcp-gateway"),
    CONSOLE_TENANT: z
      .string()
      .regex(/^[a-z0-9][a-z0-9_-]{0,62}$/)
      .default("acme"),
    /** Cookie `Secure` flag; on by default, off only for plain-HTTP local development. */
    COOKIE_SECURE: z.enum(["true", "false"]).optional(),
  })
  .superRefine((value, context) => {
    if (value.DEV_SIGNIN === "true" && !value.GATEWAY_JWT_SECRET) {
      context.addIssue({
        code: "custom",
        message: "DEV_SIGNIN=true needs GATEWAY_JWT_SECRET",
        path: ["GATEWAY_JWT_SECRET"],
      });
    }
  });

export interface ConsoleConfig {
  gatewayUrl: string;
  sessionSecret: string;
  devSignIn: boolean;
  jwtSecret: string | undefined;
  jwtIssuer: string;
  jwtAudience: string;
  tenant: string;
  cookieSecure: boolean;
}

let cached: ConsoleConfig | undefined;

export function parseConfig(env: Record<string, string | undefined>): ConsoleConfig {
  const parsed = schema.safeParse(env);
  if (!parsed.success) {
    const problems = parsed.error.issues.map((issue) => `${issue.path.join(".")}: ${issue.message}`);
    throw new Error(`operator console configuration: ${problems.join("; ")}`);
  }
  const value = parsed.data;
  return {
    gatewayUrl: value.GATEWAY_URL.replace(/\/+$/, ""),
    sessionSecret: value.SESSION_SECRET,
    devSignIn: value.DEV_SIGNIN === "true",
    jwtSecret: value.GATEWAY_JWT_SECRET,
    jwtIssuer: value.GATEWAY_JWT_ISSUER,
    jwtAudience: value.GATEWAY_JWT_AUDIENCE,
    tenant: value.CONSOLE_TENANT,
    cookieSecure: value.COOKIE_SECURE ? value.COOKIE_SECURE === "true" : process.env.NODE_ENV === "production",
  };
}

export function config(): ConsoleConfig {
  cached ??= parseConfig(process.env);
  return cached;
}
