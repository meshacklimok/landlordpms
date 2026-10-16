import type { Figures as FiguresData } from "@/lib/types";

const pct = (rate: number | null) => (rate === null ? "—" : `${Math.round(rate * 100)}%`);
const width = (rate: number | null) => `${Math.max(0, Math.min(100, Math.round((rate ?? 0) * 100)))}%`;

/** This month in four figures, then the twelve-month bars. */
export function Figures({ figures: f, rent }: { figures: FiguresData; rent: string }) {
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
