import type { Figures as FiguresData, Home } from "@/lib/types";

const pct = (rate: number | null) => (rate === null ? "—" : `${Math.round(rate * 100)}%`);
const width = (rate: number | null) => `${Math.max(0, Math.min(100, Math.round((rate ?? 0) * 100)))}%`;

/** This month in four figures, then the twelve-month bars. */
export function Figures({ figures: f, rent, due, vacancy, pace, net, nudges }: {
  figures: FiguresData; rent: string; due: Home["due_soon"]; vacancy: Home["vacancy"];
  pace: Home["pace"]; net: Home["net"]; nudges: Home["nudges"];
}) {
  return (
    <>
      <section className="home-figures" aria-label="This month">
        <div className="stat">
          <div className="stat-label">Collected, {f.month_label}</div>
          <div className="stat-value">{f.collected}</div>
          <div className="stat-note">
            {f.rate === null ? `No ${rent.toLowerCase()} billed yet` : `of ${f.expected} billed · ${pct(f.rate)}`}
          </div>
          {f.previous?.rate != null && f.rate !== null && (
            <div className="stat-note">{pct(f.previous.rate)} collected in {f.previous.label}</div>
          )}
        </div>

        <div className="stat">
          <div className="stat-label">Still to collect this month</div>
          <div className="stat-value">{f.outstanding}</div>
          {f.rate !== null && <div className="meter" aria-hidden="true"><span style={{ width: width(f.rate) }} /></div>}
          <div className="stat-note">
            {f.has_outstanding
              ? `${rent} billed and not yet paid`
              : f.rate !== null ? `All of this month's ${rent.toLowerCase()} is in` : "Nothing billed yet this month"}
          </div>
        </div>

        <div className="stat">
          <div className="stat-label">Overdue today</div>
          <div className={`stat-value${f.aging.length ? " bad" : ""}`}>{f.overdue}</div>
          {f.aging.length > 0 ? (
            <>
              <div className="aging" role="img" aria-label="Overdue by age">
                {f.aging.map((b) => (
                  <span key={b.key} className={`aging-${b.key}`} style={{ width: `${Math.round(b.share * 100)}%` }}
                    title={`${b.label}: ${b.amount}`} />
                ))}
              </div>
              <div className="stat-note">
                {f.overdue_leases} {f.overdue_leases === 1 ? "lease" : "leases"} behind
                {f.arrears_url && <> · <a href={f.arrears_url}>See all</a></>}
              </div>
            </>
          ) : <div className="stat-note">Nobody is behind.</div>}
        </div>

        <div className="stat">
          <div className="stat-label">Occupancy</div>
          <div className="stat-value">{pct(f.occupancy.rate)}</div>
          <div className="stat-note">{f.occupancy.occupied} of {f.occupancy.rentable} units let</div>
          <div className="stat-note">{f.occupancy.vacant} {f.occupancy.vacant === 1 ? "unit" : "units"} empty</div>
        </div>
      </section>

      {(due || vacancy || pace || net || nudges.length > 0) && (
        <section className="home-signals" aria-label="Worth knowing">
          {pace && (
            <div className="signal">
              <span className="signal-icon" aria-hidden="true">
                <i className={`bi bi-${pace.change !== null && pace.change < 0 ? "graph-down-arrow" : "graph-up-arrow"}`} />
              </span>
              <span>
                <b>
                  {pace.now} in so far
                  {pace.change !== null && ` · ${pace.change >= 0 ? "up" : "down"} ${Math.abs(Math.round(pace.change * 100))}%`}
                </b>
                <span className="muted small">{pace.before} received in {pace.label}, the same days last month</span>
              </span>
            </div>
          )}
          {net && (
            <a className="signal" href={net.url}>
              <span className="signal-icon" aria-hidden="true"><i className="bi bi-wallet2" /></span>
              <span>
                <b className={net.negative ? "bad" : undefined}>{net.net} left after expenses</b>
                <span className="muted small">{net.cash} received less {net.expenses} approved expenses this month</span>
              </span>
            </a>
          )}
          {due && (
            <div className="signal">
              <span className="signal-icon" aria-hidden="true"><i className="bi bi-calendar-event" /></span>
              <span className="grow">
                <b><a href={due.url} className="signal-link">{due.amount} falls due in the next {due.days} days</a></b>
                <span className="muted small">{due.count} {due.count === 1 ? "invoice" : "invoices"} · nothing is late yet</span>
              </span>
              {due.remind_url && (
                <a className="btn btn-outline signal-act" href={due.remind_url}>
                  <i className="bi bi-chat-text" aria-hidden="true" />Remind them
                </a>
              )}
            </div>
          )}
          {vacancy && (
            <a className="signal" href={vacancy.url ?? undefined}>
              <span className="signal-icon" aria-hidden="true"><i className="bi bi-door-open" /></span>
              <span>
                <b>{vacancy.units} empty {vacancy.units === 1 ? "unit stands" : "units stand"} for {vacancy.amount} a month</b>
                <span className="muted small">
                  At asking rent{vacancy.unpriced > 0 ? `; ${vacancy.unpriced} without a price are left out` : ""}
                </span>
              </span>
            </a>
          )}
          {nudges.map((n) => (
            <a key={n.key} className="signal" href={n.url ?? undefined}>
              <span className="signal-icon" aria-hidden="true"><i className={`bi ${n.key === "long_empty" ? "bi-hourglass-split" : "bi-clock-history"}`} /></span>
              <span>
                <b>
                  {n.key === "long_empty"
                    ? `${n.count} ${n.count === 1 ? "unit has" : "units have"} stood empty ${n.days}+ days this year`
                    : `${n.count} ${n.count === 1 ? "unit has" : "units have"} paid late ${n.days} months running`}
                </b>
                <span className="muted small">
                  {n.units.join(" · ")}{n.count > n.units.length ? ` and ${n.count - n.units.length} more` : ""}
                </span>
              </span>
            </a>
          ))}
        </section>
      )}

      {f.trend.length > 0 && (
        <section className="stat home-trend" aria-labelledby="trend-h">
          <div className="home-trend-head">
            <h2 className="stat-label" id="trend-h">{rent} collected by month</h2>
            <span className="muted small">Each bar is the month&apos;s collection rate</span>
          </div>
          <ol className="bars">
            {f.trend.map((row, i) => (
              <li key={row.label} className={i === f.trend.length - 1 ? "now" : undefined}>
                <span className="bar" style={{ height: width(row.rate) }} title={`${row.label}: ${row.collected} · ${pct(row.rate)}`} />
                <span className="bar-m">{row.month}</span>
                <span className="visually-hidden">{row.label}: {pct(row.rate)}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
    </>
  );
}
