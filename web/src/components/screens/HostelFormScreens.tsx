import { djangoGet } from "@/lib/django";
import type { HostelFormData, Term } from "@/lib/types";
import { RoomFormView } from "./RoomFormView";
import { TermView } from "./TermView";

const api = (path: string) => `/properties/web-api/hostel/${path}`;

/** Add a run of rooms or beds to a hostel (D-196). */
export async function AddRoomsScreen({ id }: { id: string }) {
  const data = await djangoGet<HostelFormData>(api(`${encodeURIComponent(id)}/rooms/new/`));
  return (
    <RoomFormView
      initial={data}
      title="Add rooms"
      lead="Make rooms in a run, such as R101 to R110, each with its beds."
      submit="Add rooms"
    />
  );
}

/** Change a room's gender or bed count, or archive it. */
export async function EditRoomScreen({ id }: { id: string }) {
  const data = await djangoGet<HostelFormData>(api(`room/${encodeURIComponent(id)}/`));
  return (
    <RoomFormView
      initial={data}
      title={`Room ${data.room?.code ?? ""}`}
      lead="Change who the room is for or how many beds it has."
      submit="Save room"
      canArchive
    />
  );
}

/** Next term's drafts and activation. */
export async function TermScreen({ id }: { id: string }) {
  const data = await djangoGet<Term>(api(`${encodeURIComponent(id)}/rollover/`));
  return <TermView initial={data} />;
}
