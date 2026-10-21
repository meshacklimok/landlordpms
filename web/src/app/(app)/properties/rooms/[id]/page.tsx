import type { Metadata } from "next";
import { Suspense } from "react";
import { EditRoomScreen } from "@/components/screens/HostelFormScreens";

export const metadata: Metadata = { title: "Edit room" };

export default function Page({ params }: PageProps<"/properties/rooms/[id]">) {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading…</p>}>
      <Loaded params={params} />
    </Suspense>
  );
}

async function Loaded({ params }: { params: PageProps<"/properties/rooms/[id]">["params"] }) {
  const { id } = await params;
  return <EditRoomScreen id={id} />;
}
