import type { Metadata } from "next";
import { Suspense } from "react";
import { BankImportMapScreen } from "@/components/screens/BankImportMapScreen";

export const metadata: Metadata = { title: "Match the columns" };

export default function Page({ params }: PageProps<"/bank/import/[id]/columns">) {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading…</p>}>
      <Loaded params={params} />
    </Suspense>
  );
}

async function Loaded({ params }: { params: PageProps<"/bank/import/[id]/columns">["params"] }) {
  const { id } = await params;
  return <BankImportMapScreen id={id} />;
}
