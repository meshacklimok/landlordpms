import type { Metadata } from "next";
import { Suspense } from "react";
import { PickupsScreen } from "@/components/screens/PickupsScreen";

export const metadata: Metadata = { title: "Collection sheet" };

export default function Page({ params, searchParams }: PageProps<"/leases/pickups/[id]">) {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading the sheet…</p>}>
      <Loaded params={params} searchParams={searchParams} />
    </Suspense>
  );
}

async function Loaded({
  params,
  searchParams,
}: {
  params: PageProps<"/leases/pickups/[id]">["params"];
  searchParams: PageProps<"/leases/pickups/[id]">["searchParams"];
}) {
  const [{ id }, query] = await Promise.all([params, searchParams]);
  const day = typeof query.day === "string" ? query.day : "";
  return <PickupsScreen id={id} day={day} />;
}
