import type { Metadata } from "next";

import { PolicyView } from "@/components/policy/PolicyView";

export const metadata: Metadata = { title: "Policy" };

export default function PolicyPage() {
  return <PolicyView />;
}
