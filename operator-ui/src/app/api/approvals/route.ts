import { type NextRequest, NextResponse } from "next/server";

import { asOperator, gateway, problem, relay } from "@/lib/server";
import { approvalListQuery } from "@/lib/validation";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const query = approvalListQuery.safeParse(Object.fromEntries(request.nextUrl.searchParams));
  if (!query.success) {
    return problem(422, "Invalid query", query.error.issues[0]?.message);
  }
  const params = new URLSearchParams({ limit: String(query.data.limit) });
  if (query.data.status) {
    params.set("status", query.data.status);
  }
  return asOperator(async (session) => relay(await gateway(session, `/v1/approvals?${params}`)));
}
