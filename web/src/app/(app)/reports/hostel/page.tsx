import type { Metadata } from "next";
import { Suspense } from "react";
import { HostelReportScreen } from "@/components/screens/HostelReportScreen";

export const metadata: Metadata = { title: "Hostel" };

export default function Page({ searchParams }: PageProps<"/reports/hostel">) {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading the report…</p>}>
      <Loaded searchParams={searchParams} />
    </Suspense>
  );
}

async function Loaded({ searchParams }: { searchParams: PageProps<"/reports/hostel">["searchParams"] }) {
  const query = await searchParams;
  return <HostelReportScreen property={typeof query.property === "string" ? query.property : ""} />;
}
