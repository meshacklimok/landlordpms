import type { Metadata } from "next";
import { Suspense } from "react";
import { AddRoomsScreen } from "@/components/screens/HostelFormScreens";

export const metadata: Metadata = { title: "Add rooms" };

export default function Page({ params }: PageProps<"/properties/[id]/rooms/new">) {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading…</p>}>
      <Loaded params={params} />
    </Suspense>
  );
}

async function Loaded({ params }: { params: PageProps<"/properties/[id]/rooms/new">["params"] }) {
  const { id } = await params;
  return <AddRoomsScreen id={id} />;
}
