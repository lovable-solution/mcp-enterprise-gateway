import type { NextRequest } from "next/server";

import { config } from "@/lib/config";
import { currentSession, problem } from "@/lib/server";
import { resumeFrom } from "@/lib/validation";

export const dynamic = "force-dynamic";

/** The gateway's server-sent event stream, relayed with the operator's token. A fresh
 * console asks for recent history (`?since=`); a reconnect resumes exactly where the
 * stream stopped (Last-Event-ID or `?after=`). */
export async function GET(request: NextRequest): Promise<Response> {
  const session = await currentSession();
  if (session === null) {
    return problem(401, "Sign-in required");
  }
  const headers: Record<string, string> = {
    Authorization: `Bearer ${session.token}`,
    Accept: "text/event-stream",
  };
  const resume = resumeFrom(request.headers.get("last-event-id"), request.nextUrl.searchParams);
  if (resume) {
    headers["Last-Event-ID"] = resume;
  }
  let upstream: Response;
  try {
    upstream = await fetch(`${config().gatewayUrl}/v1/events`, {
      headers,
      cache: "no-store",
      signal: request.signal, // the browser leaving closes the upstream stream too
    });
  } catch {
    return problem(502, "Gateway unavailable");
  }
  if (!upstream.ok || upstream.body === null) {
    return problem(upstream.status === 401 ? 401 : 502, "Event stream unavailable");
  }
  // An SSE comment straight away: the response (and the browser's `open`) must not wait
  // for the first event or keep-alive, which can be many seconds away.
  const preamble = new TextEncoder().encode(": connected\nretry: 3000\n\n");
  const body = upstream.body;
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      controller.enqueue(preamble);
      const reader = body.getReader();
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) {
            break;
          }
          controller.enqueue(value);
        }
        controller.close();
      } catch {
        controller.close(); // the browser left, or the gateway ended the stream
      }
    },
    cancel(reason) {
      return body.cancel(reason);
    },
  });
  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
