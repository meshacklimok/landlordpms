"use client";

import { useState } from "react";
import { djangoPostJson } from "@/lib/csrf";
import type { AnnouncementNew, AnnouncementPreview } from "@/lib/types";

const SMS_SEGMENT = 160;
const parts = (n: number) => (n === 0 ? 0 : n <= SMS_SEGMENT ? 1 : Math.ceil(n / 153));

/** The message, who gets it, a preview of every text, then Send. Nothing goes out until the preview is current. */
export function AnnouncementForm({ data }: { data: AnnouncementNew }) {
  const s = data.start;
  const [text, setText] = useState(s.text);
  const [props, setProps] = useState<string[]>([]);
  const [buildings, setBuildings] = useState<string[]>([]);
  const [owing, setOwing] = useState(s.owing);
  const [owingDays, setOwingDays] = useState(String(s.owing_days));
  const [due, setDue] = useState(s.due);
  const [dueDays, setDueDays] = useState(String(s.due_within));
  const [ending, setEnding] = useState(s.ending);
  const [endingDays, setEndingDays] = useState(String(s.ending_within));
  const [co, setCo] = useState(s.include_co_tenants);
  const [urgent, setUrgent] = useState(s.urgent);
  const [preview, setPreview] = useState<AnnouncementPreview | null>(null);
  const [stale, setStale] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [errors, setErrors] = useState<Record<string, string[]>>({});

  // Any change after a preview means the numbers shown are no longer what would be sent.
  const edit = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    if (preview) setStale(true);
  };
  const toggle = (list: string[], set: (v: string[]) => void, id: string) =>
    edit(set)(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

  async function run(action: "preview" | "send") {
    setBusy(true);
    setNotice(null);
    const result = await djangoPostJson<{ preview?: AnnouncementPreview; redirect?: string }>(data.post_url, {
      action, text, nonce: data.nonce, properties: props, buildings, include_co_tenants: co, urgent,
      owing, owing_days: owingDays, due, due_within: dueDays, ending, ending_within: endingDays,
    });
    if (!result.ok) {
      setBusy(false);
      setErrors(result.fields);
      const general = result.fields.__all__?.join(" ");
      const hasFields = Object.keys(result.fields).length > 0;
      return setNotice({ ok: false, text: general || (hasFields ? "Check the fields marked below." : result.message) });
    }
    setErrors({});
    if (action === "send" && result.data.redirect) {
      window.location.assign(result.data.redirect); // the sent announcement's page, still a Django page
      return;
    }
    setBusy(false);
    setPreview(result.data.preview ?? null);
    setStale(false);
  }

  const err = (f: string) => errors[f]?.map((m) => <p key={m} className="field-error" id={`${f}-err`}>{m}</p>);
  const chars = text.length + data.org_name.length + 1;
  const canSend = preview !== null && preview.sending > 0 && !stale;

  return (
    <div className="ann">
      <div className="page-head">
        <div>
          <p className="crumbs"><a href={data.list_url}>Announcements</a> / New</p>
          <h1>New announcement</h1>
        </div>
      </div>

      <div aria-live="polite">
        {notice && <p className={notice.ok ? "notice-ok" : "notice-bad"} role={notice.ok ? "status" : "alert"}>{notice.text}</p>}
      </div>

      <form onSubmit={(e) => { e.preventDefault(); void run("preview"); }} noValidate>
        <section className="card" aria-labelledby="ann-msg-h">
          <h2 id="ann-msg-h">Message</h2>
          {data.presets.length > 0 && (
            <p className="small">
              <span className="muted">Start from: </span>
              {data.presets.map((p) => (
                <a key={p.key} className="btn btn-outline btn-sm" href={`?template=${p.key}`}>{p.label}</a>
              ))}
              <span className="muted"> Fill in the [square brackets] before you send.</span>
            </p>
          )}
          <div className="field">
            <label htmlFor="ann-text">Message</label>
            <textarea
              id="ann-text" rows={4} maxLength={1000} value={text} required
              onChange={(e) => edit(setText)(e.target.value)}
              aria-invalid={errors.text ? true : undefined}
              aria-describedby={errors.text ? "text-err" : "ann-count"}
            />
            {err("text")}
            <span className="hint" id="ann-count">~{chars} characters with your name · ~{parts(chars)} SMS each</span>
          </div>
          <p className="muted small">Filled for each tenant (click to insert). “{data.org_name}” is added at the end.</p>
          <div className="ann-inserts">
            {data.fields.map((f) => (
              <button key={f} type="button" className="btn btn-outline btn-sm" onClick={() => edit(setText)(`${text}{${f}}`)}>
                <code>{`{${f}}`}</code>
              </button>
            ))}
          </div>
        </section>

        <section className="card" aria-labelledby="ann-who-h">
          <h2 id="ann-who-h">Who receives it</h2>
          <p className="muted small">Tenants living in a unit today. Leave a list empty to include everything you can see.</p>

          <fieldset className="field">
            <legend>Properties</legend>
            <div className="checks ann-list">
              {data.properties.length === 0 && <span className="muted small">No properties.</span>}
              {data.properties.map((p) => (
                <span className="check" key={p.id}>
                  <input id={`p-${p.id}`} type="checkbox" checked={props.includes(p.id)} onChange={() => toggle(props, setProps, p.id)} />
                  <label htmlFor={`p-${p.id}`}>{p.name}</label>
                </span>
              ))}
            </div>
            {err("properties")}
          </fieldset>

          {data.buildings.length > 0 && (
            <fieldset className="field">
              <legend>Buildings</legend>
              <div className="checks ann-list">
                {data.buildings.map((b) => (
                  <span className="check" key={b.id}>
                    <input id={`b-${b.id}`} type="checkbox" checked={buildings.includes(b.id)} onChange={() => toggle(buildings, setBuildings, b.id)} />
                    <label htmlFor={`b-${b.id}`}>{b.name}</label>
                  </span>
                ))}
              </div>
              {err("buildings")}
            </fieldset>
          )}

          {data.can_owing && (
            <div className="field">
              <div className="ann-inline">
                <span className="check">
                  <input id="ann-owing" type="checkbox" checked={owing} onChange={(e) => edit(setOwing)(e.target.checked)} />
                  <label htmlFor="ann-owing">Only tenants owing money, overdue for at least</label>
                </span>
                <input type="number" min={1} max={365} value={owingDays} aria-label="Days overdue" onChange={(e) => edit(setOwingDays)(e.target.value)} />
                <span className="small">days</span>
              </div>
              {err("owing_days")}
            </div>
          )}

          {data.can_due && (
            <div className="field">
              <div className="ann-inline">
                <span className="check">
                  <input id="ann-due" type="checkbox" checked={due} onChange={(e) => edit(setDue)(e.target.checked)} />
                  <label htmlFor="ann-due">Only tenants whose rent falls due within</label>
                </span>
                <input type="number" min={1} max={60} value={dueDays} aria-label="Days until rent falls due" onChange={(e) => edit(setDueDays)(e.target.value)} />
                <span className="small">days (not late yet)</span>
              </div>
              {err("due_within")}
            </div>
          )}

          <div className="field">
            <div className="ann-inline">
              <span className="check">
                <input id="ann-ending" type="checkbox" checked={ending} onChange={(e) => edit(setEnding)(e.target.checked)} />
                <label htmlFor="ann-ending">Only leases ending within</label>
              </span>
              <input type="number" min={0} max={365} value={endingDays} aria-label="Days until the lease ends" onChange={(e) => edit(setEndingDays)(e.target.value)} />
              <span className="small">days</span>
            </div>
            {err("ending_within")}
          </div>

          <div className="field">
            <span className="check">
              <input id="ann-co" type="checkbox" checked={co} onChange={(e) => edit(setCo)(e.target.checked)} />
              <label htmlFor="ann-co">Also send to co-tenants</label>
            </span>
          </div>
          <div className="field">
            <span className="check">
              <input id="ann-urgent" type="checkbox" checked={urgent} onChange={(e) => edit(setUrgent)(e.target.checked)} />
              <label htmlFor="ann-urgent">Send now, even during quiet hours</label>
            </span>
            <span className="hint">Otherwise messages written at night wait until quiet hours end.</span>
          </div>
        </section>

        {preview && <PreviewCard preview={preview} limit={data.inline_limit} />}

        <div className="form-actions">
          <button className={`btn ${canSend ? "btn-outline" : "btn-primary"}`} type="submit" disabled={busy}>
            {busy ? "Working…" : preview ? "Preview again" : "Preview"}
          </button>
          {preview && preview.sending > 0 && (
            <button
              className="btn btn-primary" type="button" disabled={busy || stale}
              onClick={() => {
                if (confirm(`Send to ${preview.sending} ${preview.sending === 1 ? "tenant" : "tenants"}?`)) void run("send");
              }}
            >
              <i className="bi bi-send" aria-hidden="true" />Send to {preview.sending} {preview.sending === 1 ? "tenant" : "tenants"}
            </button>
          )}
          {stale && <span className="small muted" role="status">You changed something. Preview again before sending.</span>}
        </div>
      </form>
    </div>
  );
}

function PreviewCard({ preview: p, limit }: { preview: AnnouncementPreview; limit: number }) {
  return (
    <section className="card" aria-labelledby="ann-prev-h" id="ann-preview">
      <h2 id="ann-prev-h">Preview</h2>
      <div className="ann-tiles">
        <div className="stat">
          <div className="stat-value">{p.sending}</div>
          <div className="stat-note">
            will receive it
            {p.whatsapp > 0 && <><br />{p.whatsapp} on WhatsApp</>}
            {p.email > 0 && <><br />{p.email} also by email</>}
          </div>
        </div>
        <div className="stat"><div className="stat-value">{p.parts}</div><div className="stat-note">SMS in total</div></div>
        <div className="stat"><div className="stat-value">~KES {p.cost}</div><div className="stat-note">at {p.price} per SMS</div></div>
      </div>
      {p.skipped.length > 0 && (
        <div className="alert" role="note">
          <div>
            <b>Skipped (no message goes to them):</b>
            <ul className="plain small">{p.skipped.map((r, i) => <li key={i}>{r.who} ({r.unit}): {r.reason}</li>)}</ul>
          </div>
        </div>
      )}
      {p.recipients.length > 0 ? (
        <div className="table-wrap ann-table">
          <table className="data">
            <thead><tr><th>Tenant</th><th>Unit</th><th>Message</th><th className="num">SMS</th></tr></thead>
            <tbody>
              {p.recipients.map((r, i) => (
                <tr key={i}>
                  <td>{r.who}</td><td>{r.unit}</td><td className="ann-body">{r.body}</td>
                  <td className="num">{r.channel === "WHATSAPP" ? "WhatsApp" : r.parts}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="muted small">No tenants match. Change who it goes to.</p>}
      {p.more > 0 && <p className="muted small">…and {p.more} more.</p>}
      {p.sending > limit && <p className="muted small">Large groups go out over the next few minutes.</p>}
    </section>
  );
}
