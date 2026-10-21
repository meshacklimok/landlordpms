"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { djangoPostJson } from "@/lib/csrf";
import type { PickupSheet } from "@/lib/types";

type Mark = "DONE" | "MISSED" | "";

const dayFormat = new Intl.DateTimeFormat("en-KE", { weekday: "long", day: "numeric", month: "long" });
const long = (iso: string) => dayFormat.format(new Date(`${iso}T12:00:00`));
const marksOf = (sheet: PickupSheet) =>
  Object.fromEntries(sheet.rows.map((r) => [r.id, (r.outcome as Mark) || ""])) as Record<number, Mark>;

/** The garbage collector's day sheet: collected or missed for each contract on the route. */
export function PickupsSheet({ initial }: { initial: PickupSheet }) {
  const router = useRouter();
  const [sheet, setSheet] = useState(initial);
  const [marks, setMarks] = useState(() => marksOf(initial));
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [, startNavigation] = useTransition();

  const earliest = new Date(`${sheet.today}T12:00:00`);
  earliest.setDate(earliest.getDate() - sheet.look_back_days);
  const unmarked = sheet.rows.filter((r) => !marks[r.id]).length;

  async function save(allDone: boolean) {
    setSaving(true);
    setNotice(null);
    const result = await djangoPostJson<{ message: string; sheet: PickupSheet }>(sheet.post_url, {
      day: sheet.day,
      marks: Object.fromEntries(Object.entries(marks).filter(([, v]) => v)),
      all_done: allDone,
    });
    setSaving(false);
    if (!result.ok) return setNotice({ ok: false, text: result.message });
    setSheet(result.data.sheet);
    setMarks(marksOf(result.data.sheet));
    setNotice({ ok: true, text: result.data.message });
  }

  function changeDay(day: string) {
    if (day) startNavigation(() => router.push(`/leases/pickups/${sheet.route.id}/?day=${day}`));
  }

  return (
    <>
      <div className="page-head">
        <div>
          <p className="crumbs">
            <a href="/properties/">Routes</a> / {sheet.route.name}
          </p>
          <h1>Collection sheet</h1>
          <p className="lead-text">{long(sheet.day)}</p>
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="day">Day</label>
          <input
            id="day"
            type="date"
            value={sheet.day}
            max={sheet.today}
            min={earliest.toISOString().slice(0, 10)}
            onChange={(e) => changeDay(e.target.value)}
          />
        </div>
      </div>

      <div aria-live="polite">
        {notice && (
          <p className={notice.ok ? "notice-ok" : "notice-bad"} role={notice.ok ? "status" : "alert"}>
            {notice.text}
          </p>
        )}
      </div>

      <section className="card" aria-labelledby="sheet-h">
        <div className="card-head">
          <h2 id="sheet-h">{sheet.rows.length} on this route</h2>
          <span className="muted small">{unmarked} not marked yet</span>
        </div>
        {sheet.rows.length === 0 ? (
          <p className="empty">No contract was running on this route that day.</p>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void save(false);
            }}
          >
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th scope="col">{sheet.words.unit}</th>
                    <th scope="col">{sheet.words.tenant}</th>
                    <th scope="col">Collected?</th>
                  </tr>
                </thead>
                <tbody>
                  {sheet.rows.map((r) => (
                    <tr key={r.id}>
                      <th scope="row">
                        <span className="unit-chip">{r.unit}</span>
                      </th>
                      <td>{r.who}</td>
                      <td>
                        <div className="seg" role="radiogroup" aria-label={`${r.unit} ${r.who}`}>
                          {(
                            [
                              ["DONE", "Collected"],
                              ["MISSED", "Missed"],
                            ] as const
                          ).map(([value, label]) => (
                            <label key={value}>
                              <input
                                type="radio"
                                name={`m${r.id}`}
                                value={value}
                                checked={marks[r.id] === value}
                                onChange={() => setMarks((m) => ({ ...m, [r.id]: value }))}
                              />
                              <span>{label}</span>
                            </label>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="form-actions" style={{ marginTop: "1rem" }}>
              <button className="btn btn-primary" type="submit" disabled={saving}>
                {saving ? "Saving…" : "Save the sheet"}
              </button>
              <button className="btn btn-outline" type="button" disabled={saving || unmarked === 0} onClick={() => void save(true)}>
                All collected
              </button>
              <span className="muted small">&ldquo;All collected&rdquo; marks only the rows you have not marked.</span>
            </div>
          </form>
        )}
      </section>

      <section className="card" style={{ marginTop: ".75rem" }} aria-labelledby="missed-h">
        <div className="card-head">
          <h2 id="missed-h">Missed this month</h2>
          <span className="muted small">since {long(sheet.month_start)}</span>
        </div>
        {sheet.missed.length === 0 ? (
          <p className="empty">
            <i className="bi bi-check-circle" aria-hidden="true" />
            Nothing missed this month.
          </p>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">{sheet.words.unit}</th>
                  <th scope="col">{sheet.words.tenant}</th>
                  <th scope="col" className="num">Missed</th>
                  <th scope="col" className="num">Collected</th>
                </tr>
              </thead>
              <tbody>
                {sheet.missed.map((m) => (
                  <tr key={m.id}>
                    <th scope="row">
                      <span className="unit-chip">{m.unit}</span>
                    </th>
                    <td>{m.who}</td>
                    <td className="num bad">{m.missed}</td>
                    <td className="num">{m.done}</td>
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
