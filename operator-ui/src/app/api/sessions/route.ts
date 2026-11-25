import { NextResponse } from "next/server";

import { asOperator, gateway, relay } from "@/lib/server";

export async function GET(): Promise<NextResponse> {
  return asOperator(async (session) => relay(await gateway(session, "/v1/sessions")));
}
