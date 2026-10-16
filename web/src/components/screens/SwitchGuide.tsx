import { djangoGet } from "@/lib/django";
import type { SwitchGuide as Guide } from "@/lib/types";

/** The "move to NyumbaPMS" guide: six steps, each ticked once the data shows it is done. */
export async function SwitchGuide() {
  const guide = await djangoGet<Guide>("/imports/web-api/switch/");
  const finished = guide.steps.filter((s) => s.done).length;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Move to NyumbaPMS</h1>
          <p className="lead-text">
            Bring what you already keep in a book or a spreadsheet. {finished} of {guide.steps.length} steps done.
          </p>
        </div>
        {guide.can_import && (
          <a className="btn btn-primary" href="/imports/">
            <i className="bi bi-upload" aria-hidden="true" />All imports
          </a>
        )}
      </div>

      <div
        className="progress"
        role="progressbar"
        aria-label="Steps done"
        aria-valuemin={0}
        aria-valuemax={guide.steps.length}
        aria-valuenow={finished}
      >
        <span style={{ width: `${(finished / Math.max(guide.steps.length, 1)) * 100}%` }} />
      </div>

      <ol className="guide card">
        {guide.steps.map((step, i) => (
          <li key={step.title} className={step.done ? "done" : undefined}>
            <span className="guide-mark" aria-hidden="true">
              {step.done ? <i className="bi bi-check-lg" /> : i + 1}
            </span>
            <div className="guide-body">
              <h2>
                {step.title}
                {step.done && <span className="pill pill-muted" style={{ marginLeft: ".5rem" }}>Done</span>}
              </h2>
              <p className="muted">{step.text}</p>
              {step.url && (
                <a className="btn btn-outline" href={step.url}>
                  {step.action}
                </a>
              )}
            </div>
          </li>
        ))}
      </ol>

      {!guide.can_announce && (
        <p className="muted small">Telling your tenants about the move is done by someone who can send bulk messages.</p>
      )}
    </>
  );
}
