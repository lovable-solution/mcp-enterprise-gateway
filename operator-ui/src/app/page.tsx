import { redirect } from "next/navigation";

import { Dashboard } from "@/components/Dashboard";
import { currentSession } from "@/lib/server";

export const dynamic = "force-dynamic";

export default async function Home() {
  const session = await currentSession();
  if (session === null) {
    redirect("/signin");
  }
  const { subject, role, tenant, expiresAt, actions } = session;
  return <Dashboard operator={{ subject, role, tenant, expiresAt, actions }} />;
}
