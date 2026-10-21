"use client";

import { useState } from "react";
import { djangoPostJson } from "@/lib/csrf";
import type { Term } from "@/lib/types";
import { HostelFields } from "./HostelFields";

type Answer = { message: string; skipped: string[]; term: Term };

/** Next term: tick the students staying, set the dates, make drafts, then activate the drafts (D-193). */
export function TermView({ initial }: { initial: Term }) {
  const [term, setTerm] = useState(initial);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(initial.fields.map((f) => [f.name, f.value])),
  );
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [students, setStudents] = useState<string[]>([]);
  const [drafts, setDrafts] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [skipped, setSkipped] = useState<string[]>([]);

  function toggle(list: string[], set: (v: string[]) => void, id: string) {
    set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  }

  async function send(action: "draft" | "activate") {
    setBusy(true);
    setNotice(null);
    setErrors({});
    setSkipped([]);
    const leases = action === "draft" ? students : drafts;
    const result = await djangoPostJson<Answer>(term.post_url, { action, values, leases });
    setBusy(false);
    if (!result.ok) {
      setErrors(result.fields);
      return setNotice({ ok: false, text: result.message });
    }
    setTerm(result.data.term);
    setSkipped(result.data.skipped);
    setStudents([]);
    setDrafts([]);
    setNotice({ ok: true, text: result.data.message });
  }

  return (
    <>
      <div className="page-head">
        <div>
          <p className="crumbs">
            <a href={term.rooms_url}>Room map</a> / {term.property.name}
          </p>
          <h1>Next term</h1>
          <p className="lead-text">Draft the next booking for every student who is staying, then activate the drafts.</p>
        </div>
      </div>

      <div aria-live="polite">
        {notice && (
          <p className={notice.ok ? "notice-ok" : "notice-bad"} role={notice.ok ? "status" : "alert"}>
            {notice.text}
          </p>
        )}
        {skipped.length > 0 && (
          <ul className="muted small">
            {skipped.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        )}
      </div>

      {term.drafts.length > 0 && (
        <section className="card" aria-labelledby="drafts-h" style={{ marginBottom: ".75rem" }}>
          <div className="card-head">
            <h2 id="drafts-h">Renewal drafts waiting</h2>
          </div>
          <ul className="plain-list">
            {term.drafts.map((d) => (
              <li key={d.id}>
                <label>
                  {term.can_activate && (
                    <input
                      type="checkbox"
                      checked={drafts.includes(d.id)}
                      onChange={() => toggle(drafts, setDrafts, d.id)}
                    />
                  )}{" "}
                  {d.name} · {d.unit} · from {d.starts}
                  {d.ends && ` to ${d.ends}`}
                </label>
              </li>
            ))}
          </ul>
          {term.can_activate && (
            <div className="form-actions">
              <button
                className="btn btn-outline"
                type="button"
                onClick={() => setDrafts(drafts.length === term.drafts.length ? [] : term.drafts.map((d) => d.id))}
              >
                {drafts.length === term.drafts.length ? "Clear" : "Tick all"}
              </button>
              <button
                className="btn btn-primary"
                type="button"
                disabled={busy || drafts.length === 0}
                onClick={() => void send("activate")}
              >
                Activate ticked
              </button>
            </div>
          )}
        </section>
      )}

      <section className="card" aria-labelledby="students-h">
        <div className="card-head">
          <h2 id="students-h">Students staying</h2>
        </div>
        {term.students.length === 0 ? (
          <p className="empty">No active bookings to renew.</p>
        ) : (
          <form
            noValidate
            onSubmit={(e) => {
              e.preventDefault();
              void send("draft");
            }}
          >
            <ul className="plain-list">
              {term.students.map((s) => (
                <li key={s.id}>
                  <label>
                    <input
                      type="checkbox"
                      checked={students.includes(s.id)}
                      disabled={s.drafted}
                      onChange={() => toggle(students, setStudents, s.id)}
                    />{" "}
                    {s.name} · {s.unit} · ends {s.ends}
                    {s.drafted && <span className="muted small"> · draft made</span>}
                  </label>
                </li>
              ))}
            </ul>
            <HostelFields
              fields={term.fields}
              values={values}
              errors={errors}
              onChange={(name, value) => setValues((cur) => ({ ...cur, [name]: value }))}
            />
            <div className="form-actions">
              <button className="btn btn-primary" type="submit" disabled={busy || students.length === 0}>
                {busy ? "Working…" : "Make drafts"}
              </button>
            </div>
          </form>
        )}
      </section>
    </>
  );
}
