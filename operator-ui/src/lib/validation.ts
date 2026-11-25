import { z } from "zod";

// Inputs the console forwards to the gateway are validated here first, so a route
// parameter can never smuggle a different path (e.g. "../policy") into an upstream URL.

const APPROVAL_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const SUBJECT = /^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$/;

export const APPROVAL_STATUSES = [
  "PENDING_APPROVAL",
  "APPROVED",
  "REJECTED",
  "EXPIRED",
  "EXECUTING",
  "EXECUTED",
  "FAILED",
] as const;

export function isApprovalId(value: string): boolean {
  return APPROVAL_ID.test(value);
}

export function isSessionId(value: string): boolean {
  return SESSION_ID.test(value);
}

export const decisionSchema = z
  .object({
    decision: z.enum(["approve", "reject"]),
    reason: z
      .string()
      .trim()
      .max(1000)
      .optional()
      .transform((reason) => (reason ? reason : undefined)),
  })
  .refine((value) => value.decision === "approve" || value.reason !== undefined, {
    message: "a rejection needs a reason",
    path: ["reason"],
  });

export type Decision = z.infer<typeof decisionSchema>;

export const signInSchema = z.discriminatedUnion("mode", [
  z.object({
    mode: z.literal("dev"),
    subject: z.string().regex(SUBJECT, "letters, digits and . _ @ - (max 64)"),
    role: z.enum(["operator", "auditor"]),
  }),
  z.object({
    mode: z.literal("token"),
    token: z
      .string()
      .trim()
      .min(16)
      .max(8192)
      .regex(/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$/, "not a JWT"),
  }),
]);

export type SignIn = z.infer<typeof signInSchema>;

export const approvalListQuery = z.object({
  status: z.enum(APPROVAL_STATUSES).optional(),
  limit: z.coerce.number().int().min(1).max(200).default(50),
});

/** Same-origin check for state-changing requests (defence in depth: the session cookie
 * is SameSite=Strict, so a cross-site request does not carry it in the first place). */
export function isSameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) {
    // Browsers send Origin on every cross-origin POST; a same-origin fetch may omit it.
    return request.headers.get("sec-fetch-site") !== "cross-site";
  }
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  try {
    return host !== null && new URL(origin).host === host;
  } catch {
    return false;
  }
}
