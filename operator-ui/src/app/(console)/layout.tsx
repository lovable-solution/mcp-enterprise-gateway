import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { ConsoleProvider } from "@/components/console/ConsoleProvider";
import { AppShell } from "@/components/shell/AppShell";
import { currentSession } from "@/lib/server";

export const dynamic = "force-dynamic";

// Every console page shares one provider (one event stream, one state) and one shell.
export default async function ConsoleLayout({ children }: { children: ReactNode }) {
  const session = await currentSession();
  if (session === null) {
    redirect("/signin");
  }
  const { subject, role, tenant, expiresAt, actions } = session;
  return (
    <ConsoleProvider operator={{ subject, role, tenant, expiresAt, actions }}>
      <AppShell>{children}</AppShell>
    </ConsoleProvider>
  );
}
