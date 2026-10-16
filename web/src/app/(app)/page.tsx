import type { Metadata } from "next";
import { Suspense } from "react";
import { HomeScreen } from "@/components/home/HomeScreen";

export const metadata: Metadata = { title: "Home" };

export default function Page() {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading your home…</p>}>
      <HomeScreen />
    </Suspense>
  );
}
