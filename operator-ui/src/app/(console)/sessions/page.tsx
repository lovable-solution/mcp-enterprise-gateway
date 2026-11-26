import type { Metadata } from "next";
import { Suspense } from "react";

import { SessionsView } from "@/components/sessions/SessionsView";

export const metadata: Metadata = { title: "Sessions" };

export default function SessionsPage() {
  return (
    <Suspense>
      <SessionsView />
    </Suspense>
  );
}
