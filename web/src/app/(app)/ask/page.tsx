import type { Metadata } from "next";
import { Suspense } from "react";
import { AskScreen } from "@/components/screens/AskScreen";

export const metadata: Metadata = { title: "Ask" };

export default function Page() {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading…</p>}>
      <AskScreen />
    </Suspense>
  );
}
