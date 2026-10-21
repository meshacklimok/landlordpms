import type { Metadata } from "next";
import { Suspense } from "react";
import { AnnouncementScreen } from "@/components/screens/AnnouncementScreen";

export const metadata: Metadata = { title: "New announcement" };

export default function Page({ searchParams }: PageProps<"/messages/announcements/new">) {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading…</p>}>
      <Loaded searchParams={searchParams} />
    </Suspense>
  );
}

async function Loaded({ searchParams }: { searchParams: PageProps<"/messages/announcements/new">["searchParams"] }) {
  const query = await searchParams;
  const one = (k: string) => (typeof query[k] === "string" ? (query[k] as string) : "");
  return <AnnouncementScreen template={one("template")} days={one("days")} />;
}
