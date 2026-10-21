import { djangoGet } from "@/lib/django";
import type { BankImportDetail } from "@/lib/types";
import { BankImportActions } from "./BankImportActions";

const when = new Intl.DateTimeFormat("en-KE", {
  day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
});

/** One statement: what it holds, what could not be read, and Import or Discard while it is still a preview. */
export async function BankImportDetailScreen({ id }: { id: string }) {
  const b = await djangoGet<BankImportDetail>(`/bank/web-api/import/${id}/`);
  const bank = b.kind === "BANK";
  return (
    <>
      <div className="page-head">
        <div>
          <p className="crumbs"><a href={b.import_url}>Import a statement</a> / {b.file_name}</p>
          <h1>{b.file_name}</h1>
          <p className="lead-text">
            {b.kind_label} · {b.account} · {when.format(new Date(b.created_at))} · {b.status_label}
          </p>
        </div>
      </div>

      {b.waiting_for_columns && (
        <p className="alert alert-info">
          This statement is waiting for you to match its columns. <a href={b.map_url}>Match the columns</a>
        </p>
      )}

      <div className="ann-tiles">
        <div className="stat"><span className="stat-value">{b.new}</span><span className="stat-label">{b.status === "APPLIED" ? "lines imported" : "new money-in lines"}</span></div>
        <div className="stat"><span className="stat-value">{b.duplicates}</span><span className="stat-label">already imported (skipped)</span></div>
        <div className="stat"><span className="stat-value">{b.skipped}</span><span className="stat-label">money out or not completed (skipped)</span></div>
        <div className="stat"><span className="stat-value">{b.errors.length}</span><span className="stat-label">could not be read</span></div>
      </div>

      {b.scanned && b.status !== "DISCARDED" && (
        <p className="alert alert-warning">
          <i className="bi bi-camera" aria-hidden="true" /> Read from a photo or scan. Check each line against your
          statement before importing. Nothing from a photo is confirmed automatically: every line waits in the inbox
          with a suggested lease for you to accept.
        </p>
      )}

      {b.status === "APPLIED" && (
        <p className="alert alert-info">
          {b.matched} matched, confirmed and receipted; {b.unmatched} waiting to be matched.{" "}
          {b.unmatched > 0 && <a href={b.inbox_url}>Open the inbox</a>}
        </p>
      )}
      {b.status === "PREVIEW" && b.expired && (
        <p className="alert alert-warning">This preview has expired. <a href={b.import_url}>Upload the file again.</a></p>
      )}
      {b.status === "PREVIEW" && !b.expired && <BankImportActions detail={b} />}

      {b.errors.length > 0 && (
        <section className="card" aria-labelledby="unread-h">
          <h2 id="unread-h">Lines that could not be read</h2>
          <ul className="plain-list small">
            {b.errors.map((e, i) => <li key={i}>Line {e.line}: {e.message}</li>)}
          </ul>
        </section>
      )}

      {b.rows.length > 0 && (
        <div className="card table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th scope="col">Line</th><th scope="col">Date</th>
                <th scope="col">{bank ? "Description" : "M-Pesa code"}</th>
                <th scope="col">{bank ? "Reference" : "Account no."}</th>
                {!bank && <th scope="col">Payer</th>}
                <th scope="col" className="num">Amount</th>
              </tr>
            </thead>
            <tbody>
              {b.rows.map((r, i) => (
                <tr key={i}>
                  <td className="muted">{r.line}</td><td>{r.date}</td><td>{r.description || "—"}</td>
                  <td><code>{r.reference || "—"}</code></td>
                  {!bank && <td>{r.payer || "—"}</td>}
                  <td className="num">{r.amount}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {b.more > 0 && <p className="muted small">Showing the first {b.rows.length}.</p>}
    </>
  );
}
