import type { Metadata } from "next";
import { Suspense } from "react";
import { BankInboxScreen } from "@/components/screens/BankInboxScreen";

export const metadata: Metadata = { title: "Bank inbox" };

export default function Page() {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading the inbox…</p>}>
      <BankInboxScreen />
    </Suspense>
  );
}
