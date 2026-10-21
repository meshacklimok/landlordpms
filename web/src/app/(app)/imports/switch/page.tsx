import type { Metadata } from "next";
import { Suspense } from "react";
import { SwitchGuide } from "@/components/screens/SwitchGuide";

export const metadata: Metadata = { title: "Move to NyumbaPMS" };

export default function Page() {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading the guide…</p>}>
      <SwitchGuide />
    </Suspense>
  );
}
