"use client";

import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { djangoPostJson } from "@/lib/csrf";
import type { RoomMap } from "@/lib/types";

const DONE: Record<string, string> = { added: "Rooms added.", updated: "Room updated.", archived: "Room archived." };

export function RoomMapView({ initial }: { initial: RoomMap }) {
  const [map, setMap] = useState(initial);
  const [saving, setSaving] = useState(false);
  const done = DONE[useSearchParams().get("done") ?? ""];
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(done ? { ok: true, text: done } : null);

  async function booking(action: "on" | "off") {
    setSaving(true);
    setNotice(null);
    const result = await djangoPostJson<{ message: string; map: RoomMap }>(map.post_url, { action });
    setSaving(false);
    if (!result.ok) return setNotice({ ok: false, text: result.message });
    setMap(result.data.map);
    setNotice({ ok: true, text: result.data.message });
  }

  return (
    <>
      <div className="page-head">
        <div>
          <p className="crumbs">
            <a href={map.property.url}>{map.property.name}</a>
          </p>
          <h1>Room map</h1>
          <p className="lead-text">
            {map.taken} beds taken, {map.free} free.
          </p>
        </div>
        {map.can_manage && (
          <a className="btn btn-primary" href={map.links.add_rooms}>
            <i className="bi bi-plus-lg" aria-hidden="true" /> Add rooms
          </a>
        )}
      </div>

      <div className="form-actions" style={{ marginBottom: ".75rem" }}>
        <a className="btn btn-outline" href={map.links.waitlist}>
          Waitlist{map.waiting > 0 ? ` (${map.waiting})` : ""}
        </a>
        {map.can_rollover && (
          <a className="btn btn-outline" href={map.links.rollover}>
            Next term
          </a>
        )}
      </div>

      <div aria-live="polite">
        {notice && (
          <p className={notice.ok ? "notice-ok" : "notice-bad"} role={notice.ok ? "status" : "alert"}>
            {notice.text}
          </p>
        )}
      </div>

      {map.can_manage && (
        <section className="card" aria-labelledby="booking-h" style={{ marginBottom: ".75rem" }}>
          <div className="card-head">
            <h2 id="booking-h">Booking page</h2>
          </div>
          {map.booking_url ? (
            <>
              <p className="muted small">
                Students open this link, see what is free and ask for a bed. When nothing suits them they join the
                waitlist.
              </p>
              <div className="field">
                <label htmlFor="booking-link">Booking link</label>
                <input id="booking-link" readOnly value={map.booking_url} />
              </div>
              <div className="form-actions">
                <button className="btn btn-outline" disabled={saving} onClick={() => void booking("on")}>
                  New link
                </button>
                <button className="btn btn-outline" disabled={saving} onClick={() => void booking("off")}>
                  Turn off
                </button>
              </div>
            </>
          ) : (
            <button className="btn btn-primary" disabled={saving} onClick={() => void booking("on")}>
              Turn on booking page
            </button>
          )}
        </section>
      )}

      {map.rooms.length === 0 && <p className="empty">No rooms yet.</p>}
      {map.rooms.map((room) => (
        <section className="card" key={room.id} style={{ marginBottom: ".75rem" }} aria-labelledby={`room-${room.id}`}>
          <div className="card-head">
            <h2 id={`room-${room.id}`}>
              Room {room.code}
              {room.gender_label && <span className="muted small"> · {room.gender_label}</span>}
              {room.building && <span className="muted small"> · {room.building}</span>}
              <span className="muted small">
                {" "}
                · {room.taken}/{room.beds}
              </span>
            </h2>
            {map.can_manage && (
              <a className="btn btn-outline" href={room.edit_url}>
                Edit
              </a>
            )}
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Bed</th>
                  <th scope="col">Who</th>
                  <th scope="col">Pay ref</th>
                </tr>
              </thead>
              <tbody>
                {room.units.map((u) => (
                  <tr key={u.id}>
                    <th scope="row">
                      <a className="unit-chip" href={u.url}>
                        {u.code}
                      </a>
                    </th>
                    <td>{u.free ? <span className="good">Free</span> : u.occupant || "Taken"}</td>
                    <td>
                      <code>{u.reference}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </>
  );
}
