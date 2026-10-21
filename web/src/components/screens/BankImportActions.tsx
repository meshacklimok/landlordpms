"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { djangoPostJson } from "@/lib/csrf";
import type { BankImportDetail } from "@/lib/types";

/** Import or Discard a previewed statement. Importing places every new line and receipts the ones it can match. */
export function BankImportActions({ detail }: { detail: BankImportDetail }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  async function run(action: "apply" | "discard") {
    setBusy(true);
    setNotice("");
    const result = await djangoPostJson<{ message: string; redirect?: string }>(detail.post_url, { action });
    if (!result.ok) {
      setBusy(false);
      return setNotice(result.message);
    }
    if (result.data.redirect) window.location.assign(result.data.redirect);
    else router.refresh(); // the same page, now showing the imported counts
  }

  return (
    <div>
      {notice && <p className="notice-bad" role="alert">{notice}</p>}
      <div className="form-actions">
        {detail.new > 0 && (
          <button className="btn btn-primary" disabled={busy} onClick={() => void run("apply")}>
            <i className="bi bi-check2" aria-hidden="true" />Import {detail.new} {detail.new === 1 ? "line" : "lines"}
          </button>
        )}
        <button className="btn btn-outline" disabled={busy} onClick={() => void run("discard")}>Discard</button>
      </div>
      {detail.new === 0 && <p className="muted small">There is nothing new to import in this file.</p>}
    </div>
  );
}
