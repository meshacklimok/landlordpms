import type { Metadata } from "next";
import { Suspense } from "react";
import { BankImportScreen } from "@/components/screens/BankImportScreen";

export const metadata: Metadata = { title: "Import a statement" };

export default function Page() {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading…</p>}>
      <BankImportScreen />
    </Suspense>
  );
}
