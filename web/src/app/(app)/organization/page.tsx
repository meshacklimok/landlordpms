import type { Metadata } from "next";
import { Suspense } from "react";
import { SettingsScreen } from "@/components/screens/SettingsScreen";

export const metadata: Metadata = { title: "Settings" };

export default function Page() {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading settings…</p>}>
      <SettingsScreen />
    </Suspense>
  );
}
