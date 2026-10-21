import type { Metadata } from "next";
import { Suspense } from "react";
import { BankImportDetailScreen } from "@/components/screens/BankImportDetailScreen";

export const metadata: Metadata = { title: "Statement" };

export default function Page({ params }: PageProps<"/bank/import/[id]">) {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading the statement…</p>}>
      <Loaded params={params} />
    </Suspense>
  );
}

async function Loaded({ params }: { params: PageProps<"/bank/import/[id]">["params"] }) {
  const { id } = await params;
  return <BankImportDetailScreen id={id} />;
}
