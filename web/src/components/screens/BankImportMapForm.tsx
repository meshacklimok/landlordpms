"use client";

import { useState } from "react";
import { djangoPostJson } from "@/lib/csrf";
import type { BankImportMap } from "@/lib/types";

export function BankImportMapForm({ data }: { data: BankImportMap }) {
  const [chosen, setChosen] = useState<Record<string, string>>(
    Object.fromEntries(data.fields.map((f) => [f.key, f.value])),
  );
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setNotice("");
    const result = await djangoPostJson<{ redirect: string }>(data.post_url, chosen);
    if (!result.ok) {
      setBusy(false);
      setErrors(result.fields);
      return setNotice(result.message);
    }
    window.location.assign(result.data.redirect);
  }

  return (
    <form className="card" onSubmit={submit} noValidate>
      {notice && <p className="notice-bad" role="alert">{notice}</p>}
      <div className="bank-fields">
        {data.fields.map((f) => (
          <div className="field" key={f.key}>
            <label htmlFor={`col-${f.key}`}>{f.label}</label>
            <select
              id={`col-${f.key}`} value={chosen[f.key]} aria-describedby={`col-${f.key}-help`}
              aria-invalid={errors[f.key] ? true : undefined}
              onChange={(e) => setChosen({ ...chosen, [f.key]: e.target.value })}
            >
              <option value="">(none)</option>
              {data.headers.filter(Boolean).map((h) => <option key={h} value={h}>{h}</option>)}
            </select>
            {errors[f.key]?.map((m) => <p key={m} className="field-error">{m}</p>)}
            <span className="hint" id={`col-${f.key}-help`}>{f.help}</span>
          </div>
        ))}
      </div>
      <div className="form-actions">
        <button className="btn btn-primary" disabled={busy}>
          <i className="bi bi-eye" aria-hidden="true" />{busy ? "Reading…" : "Check the file"}
        </button>
      </div>
    </form>
  );
}
