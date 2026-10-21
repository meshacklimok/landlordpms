import { djangoGet } from "@/lib/django";
import type { PickupSheet } from "@/lib/types";
import { PickupsSheet } from "./PickupsSheet";

/** Loads the day's sheet for a route; the marking itself is the client form. */
export async function PickupsScreen({ id, day }: { id: string; day: string }) {
  const query = /^\d{4}-\d{2}-\d{2}$/.test(day) ? `?day=${day}` : "";
  const sheet = await djangoGet<PickupSheet>(`/leases/web-api/pickups/${encodeURIComponent(id)}/${query}`);
  return <PickupsSheet key={sheet.day} initial={sheet} />;
}
