import { djangoGet } from "@/lib/django";
import type { BankImport } from "@/lib/types";
import { BankImportForm } from "./BankImportForm";

const when = new Intl.DateTimeFormat("en-KE", {
  day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
});

/** Import a statement (D-215): choose the account and file, then see what it holds before anything is imported. */
export async function BankImportScreen() {
  const data = await djangoGet<BankImport>("/bank/web-api/import/");
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Import a statement</h1>
          <p className="lead-text">
            Upload a bank statement, or an M-Pesa statement from the M-Pesa organization portal, as an Excel, CSV or PDF
            file. You see what it contains before anything is imported. Payments whose unit reference is found are
            confirmed and receipted; the rest wait in the inbox.
          </p>
        </div>
      </div>
      <div className="bank-grid">
        {data.accounts.length > 0 ? (
          <BankImportForm data={data} />
        ) : (
          <div className="card">
            <p>There is no bank, Paybill or Till account you can import for.</p>
            {data.can_add_bank && <a className="btn btn-outline" href={data.accounts_url}>Add a bank account</a>}
          </div>
        )}
        <section aria-labelledby="recent-h">
          <h2 id="recent-h">Recent imports</h2>
          <div className="card">
            {data.imports.length === 0 && <p className="muted">No statements imported yet.</p>}
            <ul className="plain-list">
              {data.imports.map((b) => (
                <li key={b.id}>
                  <a href={b.url}><strong>{b.file_name}</strong></a>{" "}
                  <span className="pill pill-muted">{b.status_label}</span>
                  <br />
                  <span className="muted small">
                    {b.account} · {when.format(new Date(b.created_at))} · {b.created_by}
                    {b.status === "APPLIED" && ` · ${b.new} lines, ${b.matched} matched`}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </section>
      </div>
    </>
  );
}
