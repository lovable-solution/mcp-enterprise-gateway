import { NextResponse } from "next/server";

import { asOperator, gateway, problem, relay } from "@/lib/server";
import { isApprovalId } from "@/lib/validation";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  if (!isApprovalId(id)) {
    return problem(404, "No such approval");
  }
  return asOperator(async (session) => relay(await gateway(session, `/v1/approvals/${id}`)));
}
