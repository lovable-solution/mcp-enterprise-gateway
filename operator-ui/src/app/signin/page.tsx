import { redirect } from "next/navigation";

import { SignInForm } from "@/components/SignInForm";
import { config } from "@/lib/config";
import { currentSession } from "@/lib/server";

export const dynamic = "force-dynamic";

export default async function SignIn() {
  if ((await currentSession()) !== null) {
    redirect("/");
  }
  const { devSignIn, tenant } = config();
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6 py-12">
      <p className="text-sm text-ink-2">MCP Enterprise Gateway</p>
      <h1 className="mt-1 text-2xl font-semibold">Operator console</h1>
      <p className="mt-2 text-sm text-ink-2">
        Review held tool calls, watch live agent spend, and clear circuit breakers for tenant{" "}
        <span className="font-medium text-ink">{tenant}</span>.
      </p>
      <SignInForm devSignIn={devSignIn} />
    </main>
  );
}
