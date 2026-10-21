import { djangoGet } from "@/lib/django";
import type { RoomMap } from "@/lib/types";
import { RoomMapView } from "./RoomMapView";

/** A hostel's rooms with their beds, who is in each and which are free (D-188). */
export async function RoomMapScreen({ id }: { id: string }) {
  const map = await djangoGet<RoomMap>(`/properties/web-api/hostel/${encodeURIComponent(id)}/rooms/`);
  return <RoomMapView initial={map} />;
}
