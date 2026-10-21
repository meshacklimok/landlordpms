import type { Metadata } from "next";
import { Suspense } from "react";
import { WaitlistScreen } from "@/components/screens/WaitlistScreen";

export const metadata: Metadata = { title: "Waitlist" };

export default function Page({ params }: PageProps<"/properties/[id]/waitlist">) {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading the waitlist…</p>}>
      <Loaded params={params} />
    </Suspense>
  );
}

async function Loaded({ params }: { params: PageProps<"/properties/[id]/waitlist">["params"] }) {
  const { id } = await params;
  return <WaitlistScreen id={id} />;
}
