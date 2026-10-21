import type { Metadata } from "next";
import { Suspense } from "react";
import { NotificationTypeScreen } from "@/components/screens/NotificationTypeScreen";

export const metadata: Metadata = { title: "Edit a notification" };

export default function Page({ params }: PageProps<"/console/control/notifications/[codename]">) {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading…</p>}>
      <Loaded params={params} />
    </Suspense>
  );
}

async function Loaded({ params }: { params: PageProps<"/console/control/notifications/[codename]">["params"] }) {
  const { codename } = await params;
  return <NotificationTypeScreen codename={codename} />;
}
