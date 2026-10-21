import type { Metadata } from "next";
import { Suspense } from "react";
import { NotificationList } from "@/components/screens/NotificationList";

export const metadata: Metadata = { title: "Notification settings" };

export default function Page() {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading…</p>}>
      <NotificationList />
    </Suspense>
  );
}
