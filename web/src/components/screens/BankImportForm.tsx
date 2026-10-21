"use client";

import { useState } from "react";
import { djangoPostForm } from "@/lib/csrf";
import type { BankImport } from "@/lib/types";

/** Account, file and the "this is a photo" tick; on success the browser goes to the next step (columns or preview). */
export function BankImportForm({ data }: { data: BankImport }) {
  const [account, setAccount] = useState(String(data.accounts[0].id));
  const [scanned, setScanned] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [notice, setNotice] = useState("");
  const bank = data.accounts.find((a) => String(a.id) === account)?.bank ?? false;

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    form.set("account", account);
    if (scanned && bank) form.set("scanned", "on");
    else form.delete("scanned");
    setBusy(true);
    setNotice("");
    const result = await djangoPostForm<{ url: string }>(data.post_url, form);
    if (!result.ok) {
      setBusy(false);
      setErrors(result.fields);
      return setNotice(result.message);
    }
    window.location.assign(result.data.url);
  }

  const err = (f: string) => errors[f]?.map((m) => <p key={m} className="field-error" id={`${f}-err`}>{m}</p>);
  return (
    <form className="card" onSubmit={submit} noValidate>
      {notice && <p className="notice-bad" role="alert">{notice}</p>}
      <div className="field">
        <label htmlFor="bank-account">Account</label>
        <select id="bank-account" value={account} onChange={(e) => setAccount(e.target.value)}>
          {data.accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        {err("account")}
      </div>
      <div className="field">
        <label htmlFor="bank-file">Statement (Excel, CSV or PDF)</label>
        <input
          id="bank-file" name="file" type="file" required
          accept=".csv,.txt,.xlsx,.pdf,.jpg,.jpeg,.png,.webp"
          aria-invalid={errors.file ? true : undefined} aria-describedby={errors.file ? "file-err" : "bank-help"}
        />
        {err("file")}
      </div>
      {bank && data.scan_available && (
        <div className="field">
          <span className="check">
            <input id="bank-scanned" type="checkbox" checked={scanned} onChange={(e) => setScanned(e.target.checked)} />
            <label htmlFor="bank-scanned">This is a photo or scan</label>
          </span>
          <span className="hint">
            A photo, screenshot or scanned PDF is read by AI and uses one of this month&rsquo;s photo scans. Nothing from
            a photo is matched automatically.
          </span>
        </div>
      )}
      <p className="muted small" id="bank-help">
        Download the statement from your bank&rsquo;s app or website. It needs a header row with a date, and a Credit
        (or Money in) or Amount column; if we do not recognise the headers we ask you to choose them once. Lines already
        imported from an earlier statement are skipped, so overlapping statements are fine.
      </p>
      <div className="form-actions">
        <button className="btn btn-primary" disabled={busy}>
          <i className="bi bi-eye" aria-hidden="true" />{busy ? "Reading the file…" : "Check the file"}
        </button>
      </div>
    </form>
  );
}
