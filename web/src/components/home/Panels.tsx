import type { Home } from "@/lib/types";

const shortDate = new Intl.DateTimeFormat("en-KE", { day: "numeric", month: "short" });
const longDate = new Intl.DateTimeFormat("en-KE", { day: "numeric", month: "short", year: "numeric" });
const at = (iso: string) => new Date(`${iso}T12:00:00`);
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** Work waiting and who owes the most on the left; setup, payments, properties and leases ending on the right. */
export function Panels({ home }: { home: Home }) {
  const { tasks, reports, debtors, payments, checklist, collection, expiring, summary } = home;
  return (
    <div className="home-grid">
      <div className="stack">
        <section className="card" aria-labelledby="today-h">
          <div className="card-head">
            <h2 id="today-h">Today</h2>
            {tasks.length > 0 && <span className="muted small">{plural(tasks.length, "thing", "things")} waiting</span>}
          </div>
          {tasks.length > 0 && (
            <ul className="tasks">
              {tasks.map((t) => (
                <li key={t.key}>
                  <a href={t.url} className={`task${t.urgent ? " is-urgent" : ""}`}>
                    <span className="task-icon" aria-hidden="true"><i className={`bi bi-${t.icon}`} /></span>
                    <span className="task-text">
                      <b>{t.count}</b> {t.label}
                      {t.detail && <span className="bad small"> · {t.detail}</span>}
                    </span>
                    <i className="bi bi-chevron-right task-go" aria-hidden="true" />
                  </a>
                </li>
              ))}
            </ul>
          )}
          {reports.length > 0 && (
            <>
              <h3 className="card-sub">Condition reports to finish</h3>
              <ul className="plain small">
                {reports.map((r) => (
                  <li key={r.url}><a href={r.url}>{r.label}</a> <span className="muted">· {longDate.format(at(r.date))}</span></li>
                ))}
              </ul>
            </>
          )}
          {tasks.length === 0 && reports.length === 0 && (
            <p className="empty"><i className="bi bi-check2-circle" aria-hidden="true" />Nothing is waiting for you.</p>
          )}
        </section>

        {debtors && home.started && (
          <section className="card" aria-labelledby="owe-h">
            <div className="card-head">
              <h2 id="owe-h">Who owes the most</h2>
              {debtors.length > 0 && home.call_list_url && <a className="small" href={home.call_list_url}>Call list</a>}
            </div>
            {debtors.length > 0 ? (
              <ul className="owe">
                {debtors.map((d) => (
                  <li key={d.url}>
                    <a className="owe-who" href={d.url}>
                      <span className="unit-chip">{d.unit}</span>
                      <span className="grow">
                        <b className="trunc">{d.who}</b>
                        <span className="muted small">{d.property} · {plural(d.days, "day", "days")} late</span>
                      </span>
                    </a>
                    <span className="num bad">{d.overdue}</span>
                    {d.phone && (
                      <a className="btn btn-outline btn-icon" href={`tel:${d.phone}`} aria-label={`Call ${d.who}`}>
                        <i className="bi bi-telephone" aria-hidden="true" />
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            ) : <p className="empty"><i className="bi bi-check2-circle" aria-hidden="true" />Nothing is overdue.</p>}
          </section>
        )}
      </div>

      <div className="stack">
        {checklist && (
          <section className="card" aria-labelledby="setup-h">
            <h2 id="setup-h">Finish setting up <span className="muted normal">{checklist.done} of {checklist.total} done</span></h2>
            <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={checklist.total} aria-valuenow={checklist.done} aria-label="Setup progress">
              <span style={{ width: `${Math.round((checklist.done / checklist.total) * 100)}%` }} />
            </div>
            <ul className="plain">
              {checklist.steps.map((s) => (
                <li key={s.key} className="step">
                  <i className={`bi ${s.done ? "bi-check-circle-fill step-done" : "bi-circle muted"}`} aria-hidden="true" />
                  <span>
                    {s.done ? <><span className="visually-hidden">Done: </span>{s.label}</> : <a href={s.url}>{s.label}</a>}
                    {s.optional && <span className="muted small"> (optional)</span>}
                    {s.key === checklist.next && s.hint && <span className="muted small step-hint">{s.hint}</span>}
                  </span>
                </li>
              ))}
            </ul>
            <p className="muted small">Stuck? Read <a href={checklist.help_url}>getting started</a>, or send us your spreadsheet and we will import it for you.</p>
          </section>
        )}

        {summary && (
          <section className="card" aria-labelledby="sum-h">
            <h2 id="sum-h">Summary</h2>
            <dl className="facts small">
              <dt>Occupied</dt><dd>{summary.rentable ? `${summary.occupied} of ${summary.rentable} units` : `No ${home.vocab.units.toLowerCase()} yet`}</dd>
              <dt>Vacant</dt>
              <dd>{summary.vacant}{(summary.reserved > 0 || summary.maintenance > 0) && <span className="muted"> ({summary.reserved} reserved, {summary.maintenance} under repair)</span>}</dd>
              <dt>Overdue</dt><dd>{summary.overdue_leases ?? 0}</dd>
            </dl>
          </section>
        )}

        {payments && home.started && (
          <section className="card" aria-labelledby="paid-h">
            <div className="card-head">
              <h2 id="paid-h">Latest payments</h2>
              {home.payments_url && <a className="small" href={home.payments_url}>All payments</a>}
            </div>
            {payments.length > 0 ? (
              <ul className="paid">
                {payments.map((p, i) => (
                  <li key={`${p.label}-${i}`}>
                    <span className="grow">
                      <b className="trunc">{p.label}</b>
                      <span className="muted small">{p.method_label} · {shortDate.format(at(p.date))}</span>
                    </span>
                    <span className="num">{p.amount}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="muted small">No payments confirmed yet.</p>}
          </section>
        )}

        {home.methods.length > 0 && (
          <section className="card" aria-labelledby="meth-h">
            <div className="card-head"><h2 id="meth-h">Cash received this month</h2><span className="muted small">By how it came in</span></div>
            <ul className="props">
              {home.methods.map((x) => (
                <li key={x.method}>
                  <div className="row-between small">
                    <b className="trunc">{x.label} <span className="muted normal">· {plural(x.count, "payment", "payments")}</span></b>
                    <span className="num">{x.amount}</span>
                  </div>
                  <div className="meter" role="img" aria-label={`${Math.round(x.share * 100)}% of cash received`}>
                    <span style={{ width: `${Math.round(x.share * 100)}%` }} />
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}

        {collection.length > 0 && (
          <section className="card" aria-labelledby="prop-h">
            <div className="card-head"><h2 id="prop-h">This month by property</h2><span className="muted small">Lowest first</span></div>
            <ul className="props">
              {collection.map((r) => (
                <li key={r.label}>
                  <div className="row-between small"><b className="trunc">{r.label}</b><span className="muted">{r.rate === null ? "—" : `${Math.round(r.rate * 100)}%`}</span></div>
                  <div className={`meter${r.rate !== null && r.rate < 0.5 ? " is-low" : ""}`} aria-hidden="true">
                    <span style={{ width: `${Math.round((r.rate ?? 0) * 100)}%` }} />
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}

        {expiring.length > 0 && (
          <section className="card" aria-labelledby="end-h">
            <h2 id="end-h">{plural(home.expiring_count, "lease ends", "leases end")} within 60 days</h2>
            <ul className="plain small">
              {expiring.map((l) => (
                <li key={l.url}>
                  <a href={l.url}>{l.label}</a>{l.tenant && ` · ${l.tenant}`}{" "}
                  <span className="muted">· ends {longDate.format(at(l.ends))}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}
