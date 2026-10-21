import { djangoGet } from "@/lib/django";
import type { HostelReport } from "@/lib/types";

const GENDER: Record<string, string> = { F: "Women", M: "Men", "": "Anyone" };

/** Beds, the term's collection, parents who owe, the waitlist and bookings about to end (D-192). */
export async function HostelReportScreen({ property }: { property: string }) {
  const query = property ? `?property=${encodeURIComponent(property)}` : "";
  const data = await djangoGet<HostelReport>(`/reports/web-api/hostel/${query}`);
  const money = (v: string) =>
    `${data.currency} ${Number(v).toLocaleString("en-KE", { maximumFractionDigits: 0 })}`;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Hostel</h1>
          <p className="lead-text">
            Beds taken and free, what this term&apos;s students have paid, the parents who owe, who is waiting and the
            bookings about to end.
          </p>
        </div>
        {data.csv_url && (
          <a className="btn btn-outline" href={data.csv_url}>
            <i className="bi bi-download" aria-hidden="true" /> CSV
          </a>
        )}
      </div>

      {data.properties.length > 1 && (
        <form method="get" className="form-actions" style={{ marginBottom: ".75rem" }}>
          <label htmlFor="property" className="visually-hidden">
            Hostel
          </label>
          <select id="property" name="property" defaultValue={data.selected}>
            <option value="">All hostels</option>
            {data.properties.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <button className="btn btn-outline" type="submit">
            Show
          </button>
        </form>
      )}

      {data.rows.length === 0 && <p className="empty">No hostels yet.</p>}
      {data.rows.map((r) => (
        <section className="card" key={r.property.id} aria-labelledby={`h-${r.property.id}`} style={{ marginBottom: ".75rem" }}>
          <div className="card-head">
            <h2 id={`h-${r.property.id}`}>{r.property.name}</h2>
          </div>
          <div className="home-figures">
            <div className="stat">
              <div className="stat-label">Beds taken</div>
              <div className="stat-value">{r.taken} / {r.beds}</div>
              <div className="stat-note">{r.free} free in {r.rooms} rooms
                {r.occupancy !== null && ` · ${Math.round(r.occupancy * 100)}%`}</div>
            </div>
            <div className="stat">
              <div className="stat-label">Paid this term</div>
              <div className="stat-value">{money(r.paid)}</div>
              <div className="stat-note">of {money(r.billed)} billed</div>
            </div>
            <div className="stat">
              <div className="stat-label">Overdue</div>
              <div className="stat-value">{money(r.owed)}</div>
              <div className="stat-note">{r.owing.length} invoice{r.owing.length === 1 ? "" : "s"}</div>
            </div>
            <div className="stat">
              <div className="stat-label">Waitlist</div>
              <div className="stat-value">{r.waiting}</div>
              <div className="stat-note">{r.ready} can be offered a bed now</div>
            </div>
          </div>

          <h3>Beds by room type</h3>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Rooms for</th>
                  <th scope="col" className="num">Beds</th>
                  <th scope="col" className="num">Taken</th>
                  <th scope="col" className="num">Waiting</th>
                </tr>
              </thead>
              <tbody>
                {r.by_gender.length === 0 && (
                  <tr>
                    <td colSpan={4} className="muted">No rooms yet.</td>
                  </tr>
                )}
                {r.by_gender.map((g) => (
                  <tr key={g.gender}>
                    <th scope="row">{GENDER[g.gender] ?? "Anyone"}</th>
                    <td className="num">{g.beds}</td>
                    <td className="num">{g.taken}</td>
                    <td className="num">{g.waiting}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h3>Parents and guardians who owe</h3>
          {r.owing.length === 0 ? (
            <p className="muted small">Nobody is overdue.</p>
          ) : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th scope="col">Student</th>
                    <th scope="col">Parent or guardian</th>
                    <th scope="col">Bed or room</th>
                    <th scope="col" className="num">Owed</th>
                    <th scope="col" className="num">Days late</th>
                  </tr>
                </thead>
                <tbody>
                  {r.owing.map((o, i) => (
                    <tr key={`${o.unit}-${i}`}>
                      <td>{data.show_names ? o.student : "–"}</td>
                      <td>
                        {data.show_names ? o.guardian : "–"}
                        {data.show_names && o.guardian_phone && ` · ${o.guardian_phone}`}
                      </td>
                      <td>{o.unit}</td>
                      <td className="num">{money(o.owed)}</td>
                      <td className="num">{o.days_late}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h3>Bookings ending in the next {data.ending_within} days</h3>
          {r.ending.length === 0 ? (
            <p className="muted small">None.</p>
          ) : (
            <>
              <ul className="plain-list">
                {r.ending.map((e, i) => (
                  <li key={`${e.unit}-${i}`}>
                    {data.show_names && e.student ? `${e.student} · ` : ""}
                    {e.unit} · {e.ends}
                    {e.drafted && " · renewal drafted"}
                  </li>
                ))}
              </ul>
              <a className="btn btn-outline" href={r.rollover_url}>
                Next term
              </a>
            </>
          )}
        </section>
      ))}
    </>
  );
}
