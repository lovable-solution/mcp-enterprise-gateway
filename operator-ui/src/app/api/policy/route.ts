import { NextResponse } from "next/server";

import { asOperator, gateway, relay } from "@/lib/server";

/** The loaded policy and its effective access matrix (needs policy:read). */
export async function GET(): Promise<NextResponse> {
  return asOperator(async (session) => relay(await gateway(session, "/v1/policy")));
}
