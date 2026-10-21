import type { Checklist, ChecklistStep } from "@/lib/types";

const minutesText = (n: number) => (n <= 1 ? "about a minute" : `about ${n} minutes`);

/** A progress ring. Drawn with currentColor/tokens so it follows light and dark mode. */
function Ring({ done, total }: { done: number; total: number }) {
  const r = 34;
  const c = 2 * Math.PI * r;
  const share = total ? done / total : 0;
  return (
    <div className="ob-ring" role="img" aria-label={`${done} of ${total} steps done`}>
      <svg viewBox="0 0 80 80" aria-hidden="true">
        <circle cx="40" cy="40" r={r} className="ob-ring-track" />
        <circle cx="40" cy="40" r={r} className="ob-ring-fill" strokeDasharray={c} strokeDashoffset={c * (1 - share)} />
      </svg>
      <span className="ob-ring-text"><b>{done}</b>/{total}</span>
    </div>
  );
}

function StepRow({ step, index, current }: { step: ChecklistStep; index: number; current: boolean }) {
  const state = step.done ? "is-done" : current ? "is-current" : "is-todo";
  return (
    <li className={`ob-step ${state}`}>
      <span className="ob-step-mark" aria-hidden="true">
        {step.done ? <i className="bi bi-check-lg" /> : <i className={`bi bi-${step.icon}`} />}
      </span>
      <div className="ob-step-body">
        <h3 className="ob-step-title">
          <span className="ob-step-n">Step {index + 1}</span>
          {step.done ? <><span className="visually-hidden">Done: </span>{step.label}</> : step.label}
          {step.optional && <span className="muted small normal"> (optional)</span>}
        </h3>
        {!step.done && step.hint && <p className="ob-step-hint">{step.hint}</p>}
      </div>
      {step.done ? (
        <span className="ob-step-state small">Done</span>
      ) : (
        <a href={step.url} className={`btn ${current ? "btn-primary" : "btn-outline"} ob-step-go`}>
          {current ? "Start" : "Open"}
          {step.minutes > 0 && <span className="ob-min"> · {step.minutes} min</span>}
          <span className="visually-hidden">: {step.label}</span>
        </a>
      )}
    </li>
  );
}

/** What each step unlocks. No numbers: nothing here is invented, it only explains what will appear. */
const unlocks = [
  { icon: "cash-coin", title: "Rent collected", text: "What you billed, what came in, and what is still to collect each month." },
  { icon: "exclamation-circle", title: "Who is behind", text: "Overdue rent by age, with a call list so you know who to chase first." },
  { icon: "pie-chart", title: "Occupancy", text: "Which units are let and which are empty." },
  { icon: "graph-up-arrow", title: "Month by month", text: "Collection trends that build up as you bill." },
];

/** The first-run home: welcome, one clear next step, the whole path, and what appears once there is data. */
export function Onboarding({ checklist, name, units }: { checklist: Checklist; name: string; units: string }) {
  const required = checklist.steps.filter((s) => !s.optional);
  const next = checklist.steps.find((s) => s.key === checklist.next) ?? null;
  const begun = checklist.done > 0;
  return (
    <div className="ob">
      <section className="ob-hero" aria-labelledby="ob-h">
        <Ring done={checklist.done} total={checklist.total} />
        <div className="ob-hero-text">
          <p className="ob-eyebrow">{begun ? "Setup in progress" : "Welcome to NyumbaPMS"}</p>
          <h2 id="ob-h">{begun ? "You are on your way." : `Let's set up ${name}.`}</h2>
          <p className="ob-lead">
            {begun
              ? `${checklist.done} of ${checklist.total} steps done. ${checklist.minutes > 0 ? `${minutesText(checklist.minutes)} left.` : ""}`
              : `${checklist.total} short steps take you from an empty account to your first invoice. It takes ${minutesText(checklist.minutes)} in all, and you can stop and come back at any point.`}
          </p>
          {next && (
            <div className="ob-cta">
              <a href={next.url} className="btn btn-primary btn-lg">
                <i className={`bi bi-${next.icon}`} aria-hidden="true" />{next.label}
              </a>
              <a href={checklist.help_url} className="ob-alt">How it works</a>
            </div>
          )}
        </div>
      </section>

      <div className="ob-grid">
        <section aria-labelledby="ob-path">
          <h2 id="ob-path" className="ob-h2">Your setup path</h2>
          <ol className="ob-steps">
            {checklist.steps.map((s, i) => (
              <StepRow key={s.key} step={s} index={i} current={s.key === checklist.next} />
            ))}
          </ol>
          <p className="muted small ob-help">
            Already have your {units.toLowerCase()} in a spreadsheet? Send it to us and we will import it for you.{" "}
            <a href={checklist.help_url}>Read the getting started guide</a>.
          </p>
        </section>

        <aside className="ob-aside" aria-labelledby="ob-unlock">
          <h2 id="ob-unlock" className="ob-h2">What appears here next</h2>
          <p className="muted small">Your figures and charts show up on their own as soon as there is real data. Until then we keep them hidden rather than show zeros.</p>
          <ul className="ob-unlocks">
            {unlocks.map((u) => (
              <li key={u.title}>
                <span className="ob-unlock-icon" aria-hidden="true"><i className={`bi bi-${u.icon}`} /></span>
                <span><b>{u.title}</b><span className="muted small ob-unlock-text">{u.text}</span></span>
              </li>
            ))}
          </ul>
          <p className="ob-count small muted">{required.length - checklist.done === 0 ? "All required steps are done." : `${required.length - checklist.done} required ${required.length - checklist.done === 1 ? "step" : "steps"} to go.`}</p>
        </aside>
      </div>
    </div>
  );
}
