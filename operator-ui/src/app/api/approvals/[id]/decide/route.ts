import { type NextRequest, NextResponse } from "next/server";

import { asOperator, gateway, problem, relay } from "@/lib/server";
import { decisionSchema, isApprovalId, isSameOrigin } from "@/lib/validation";

/** Approve or reject a held call. Approving runs it: the gateway executes the stored
 * payload exactly as reviewed and answers with the final state (EXECUTED or FAILED). */
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  if (!isSameOrigin(request)) {
    return problem(403, "Cross-origin request refused");
  }
  const { id } = await params;
  if (!isApprovalId(id)) {
    return problem(404, "No such approval");
  }
  const decision = decisionSchema.safeParse(await request.json().catch(() => null));
  if (!decision.success) {
    return problem(422, "Invalid decision", decision.error.issues[0]?.message);
  }
  return asOperator(async (session) =>
    relay(
      await gateway(session, `/v1/approvals/${id}/decide`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(decision.data),
        timeoutMs: 120_000, // includes the upstream call an approval triggers
      }),
    ),
  );
}
