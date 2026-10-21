import type { Metadata } from "next";
import { Suspense } from "react";
import { TermScreen } from "@/components/screens/HostelFormScreens";

export const metadata: Metadata = { title: "Next term" };

export default function Page({ params }: PageProps<"/properties/[id]/rollover">) {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading…</p>}>
      <Loaded params={params} />
    </Suspense>
  );
}

async function Loaded({ params }: { params: PageProps<"/properties/[id]/rollover">["params"] }) {
  const { id } = await params;
  return <TermScreen id={id} />;
}
