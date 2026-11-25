import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

/** Liveness for the container healthcheck and the load balancer. */
export function GET(): NextResponse {
  return NextResponse.json({ status: "ok" });
}
