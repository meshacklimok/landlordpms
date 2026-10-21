import { djangoGet } from "@/lib/django";
import type { Waitlist } from "@/lib/types";

const dateFormat = new Intl.DateTimeFormat("en-KE", { day: "numeric", month: "short" });
const short = (iso: string) => dateFormat.format(new Date(`${iso}T12:00:00`));

/** Students waiting for a bed, and how many beds of their kind are free now (D-189). */
export async function WaitlistScreen({ id }: { id: string }) {
  const list = await djangoGet<Waitlist>(`/properties/web-api/hostel/${encodeURIComponent(id)}/waitlist/`);
  return (
    <>
      <div className="page-head">
        <div>
          <p className="crumbs">
            <a href={list.rooms_url}>Room map</a> / {list.property.name}
          </p>
          <h1>Waitlist</h1>
          <p className="lead-text">
            {list.ready} {list.ready === 1 ? "student" : "students"} can be offered a bed now.
          </p>
        </div>
      </div>
      <section className="card" aria-labelledby="waiting-h">
        <div className="card-head">
          <h2 id="waiting-h">{list.rows.length} waiting</h2>
        </div>
        {list.rows.length === 0 ? (
          <p className="empty">Nobody is waiting.</p>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Student</th>
                  <th scope="col">Wants</th>
                  <th scope="col">Since</th>
                  <th scope="col">Beds free</th>
                </tr>
              </thead>
              <tbody>
                {list.rows.map((r) => (
                  <tr key={r.url}>
                    <th scope="row">
                      <a href={r.url}>{r.name}</a>
                      <div className="muted small">{[r.gender_label, r.phone].filter(Boolean).join(" · ")}</div>
                    </th>
                    <td>{r.wants}</td>
                    <td>
                      {short(r.since)}
                      {r.offered && <div className="muted small">Texted {short(r.offered)}</div>}
                    </td>
                    <td>
                      {r.free > 0 ? <span className="good">{r.free} free</span> : <span className="muted">None free</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
