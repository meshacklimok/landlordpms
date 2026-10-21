import type { Metadata } from "next";
import { Suspense } from "react";
import { RoomMapScreen } from "@/components/screens/RoomMapScreen";

export const metadata: Metadata = { title: "Room map" };

export default function Page({ params }: PageProps<"/properties/[id]/rooms">) {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading the room map…</p>}>
      <Loaded params={params} />
    </Suspense>
  );
}

async function Loaded({ params }: { params: PageProps<"/properties/[id]/rooms">["params"] }) {
  const { id } = await params;
  return <RoomMapScreen id={id} />;
}
