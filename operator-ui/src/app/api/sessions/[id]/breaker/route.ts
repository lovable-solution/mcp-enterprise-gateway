import { type NextRequest, NextResponse } from "next/server";

import { asOperator, gateway, problem, relay } from "@/lib/server";
import { isSameOrigin, isSessionId } from "@/lib/validation";

/** Close a tripped circuit breaker (operators hold circuit_breakers:reset). */
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  if (!isSameOrigin(request)) {
    return problem(403, "Cross-origin request refused");
  }
  const { id } = await params;
  if (!isSessionId(id)) {
    return problem(404, "No such session");
  }
  return asOperator(async (session) =>
    relay(await gateway(session, `/v1/sessions/${id}/circuit-breaker/reset`, { method: "POST" })),
  );
}
