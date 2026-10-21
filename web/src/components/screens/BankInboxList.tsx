"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { djangoPostJson } from "@/lib/csrf";
import type { BankInbox } from "@/lib/types";

const day = new Intl.DateTimeFormat("en-KE", { day: "numeric", month: "short", year: "numeric" });

/** Each line: accept the suggested lease in one press, choose another lease on its page, or ignore it with a reason. */
export function BankInboxList({ data }: { data: BankInbox }) {
  const router = useRouter();
  const [busy, setBusy] = useState("");
  const [ignoring, setIgnoring] = useState("");
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  async function act(line: string, action: "accept" | "ignore") {
    setBusy(line);
    setNotice(null);
    const result = await djangoPostJson<{ message: string }>(data.post_url, { line, action, reason });
    setBusy("");
    if (!result.ok) return setNotice({ ok: false, text: result.message });
    setNotice({ ok: true, text: result.data.message });
    setIgnoring("");
    setReason("");
    router.refresh();
  }

  if (data.rows.length === 0) {
    return (
      <div className="empty">
        <p><strong>All caught up</strong></p>
        <p className="muted">Every imported bank line has been placed on a lease or ignored.</p>
        <a className="btn btn-outline" href={data.lines_url}>See all bank lines</a>
      </div>
    );
  }
  return (
    <>
      <div aria-live="polite">
        {notice && (
          <p className={notice.ok ? "notice-ok" : "notice-bad"} role={notice.ok ? "status" : "alert"}>{notice.text}</p>
        )}
      </div>
      <ul className="bank-inbox">
        {data.rows.map((l) => (
          <li key={l.id} className="card">
            <div className="bank-line-head">
              <div>
                <a href={l.url}><strong>{l.description || l.reference || "No description"}</strong></a>
                <br />
                <span className="muted small">
                  {day.format(new Date(`${l.date}T12:00:00`))} · {l.account}
                  {l.reference && <> · Ref. <code>{l.reference}</code></>}
                </span>
                {l.note && <p className="small"><i className="bi bi-info-circle" aria-hidden="true" /> {l.note}</p>}
              </div>
              <strong className="num">{l.amount}</strong>
            </div>
            <div className="form-actions">
              {l.suggested && (
                <button
                  className="btn btn-primary btn-sm" disabled={busy === l.id}
                  onClick={() => {
                    const lease = l.suggested?.lease ?? "";
                    if (window.confirm(`Match this payment to ${lease}? It is confirmed and receipted at once.`))
                      void act(l.id, "accept");
                  }}
                >
                  <i className="bi bi-check2" aria-hidden="true" />Match to {l.suggested.property} · {l.suggested.unit}
                </button>
              )}
              <a className="btn btn-outline btn-sm" href={l.url}>
                {l.suggested ? "Choose another lease" : "Choose a lease"}
              </a>
              <button
                className="btn btn-quiet btn-sm" aria-expanded={ignoring === l.id}
                onClick={() => setIgnoring(ignoring === l.id ? "" : l.id)}
              >
                Ignore
              </button>
            </div>
            {ignoring === l.id && (
              <div className="field">
                <label htmlFor={`why-${l.id}`}>Why is this being ignored?</label>
                <input id={`why-${l.id}`} value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} />
                <button
                  className="btn btn-outline btn-sm" disabled={busy === l.id || !reason.trim()}
                  onClick={() => void act(l.id, "ignore")}
                >
                  Ignore this line
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
      {data.more && <p className="muted small">Showing the oldest {data.rows.length} of {data.count}.</p>}
    </>
  );
}
