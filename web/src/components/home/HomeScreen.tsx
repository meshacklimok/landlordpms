import { djangoGet } from "@/lib/django";
import type { Home } from "@/lib/types";
import { Figures } from "./Figures";
import { Onboarding } from "./Onboarding";
import { Panels } from "./Panels";

const dayFormat = new Intl.DateTimeFormat("en-KE", { weekday: "long", day: "numeric", month: "long", year: "numeric" });

/** The signed-in home (D-153), drawn from Django's `/web-api/home/`. */
export async function HomeScreen() {
  const home = await djangoGet<Home>("/web-api/home/");
  // A new account (nothing billed, paid or let yet, setup unfinished) gets the guided welcome instead of empty figures.
  const fresh = !home.started && home.checklist !== null;
  return (
    <>
      <div className="home-head">
        <div>
          <p className="home-date">{dayFormat.format(new Date(`${home.date}T12:00:00`))}</p>
          <h1 className="home-hello">{home.greeting}, {home.user}</h1>
          <p className="lead-text">{home.organization} · {home.role}</p>
        </div>
        {home.dashboard_url && (
          <a className="btn btn-outline" href={home.dashboard_url}>
            <i className="bi bi-bar-chart-line" aria-hidden="true" />Open the dashboard
          </a>
        )}
      </div>

      {home.suggest_mfa && (
        <div className="alert">
          <span>Your role can move money and see private records. Protect your account with two-step login.</span>
          <a className="btn btn-primary" href={home.security_url}>Set it up</a>
        </div>
      )}

      {fresh && home.checklist && (
        <Onboarding checklist={home.checklist} name={home.organization} units={home.vocab.units} />
      )}

      {!fresh && home.actions.length > 0 && (
        <nav className="home-actions" aria-label="Quick actions">
          {home.actions.map((a) => (
            <a key={a.key} href={a.url} className={`home-action${a.main ? " home-action-main" : ""}`}>{a.label}</a>
          ))}
        </nav>
      )}

      {!fresh && home.figures && <Figures figures={home.figures} rent={home.vocab.rent} due={home.due_soon} vacancy={home.vacancy} pace={home.pace} net={home.net} nudges={home.nudges} />}
      {!fresh && <Panels home={home} />}
      {fresh && home.tasks.length > 0 && <Panels home={{ ...home, checklist: null }} />}
    </>
  );
}
