import type { Session } from "@/lib/types";
import { TermsNotice } from "./TermsNotice";

/** The strips above the page: plan and subscription state, the email ask and new Terms (as base.html shows them). */
export function Notices({ session }: { session: Session }) {
  const { notices, email_notice: email, terms_notice: terms, urls } = session;
  return (
    <>
      {notices.map((n) => (
        <div key={n.text} className={`alert alert-${n.level}`} role="status">
          <span>{n.text}</span>
          {n.url && <> <a className="alert-link" href={n.url}>{n.link}</a></>}
          {n.people && n.people.length > 0 && (
            <div className="mt-1 small">
              Look around as:{" "}
              {n.people.map((p, i) => (
                <span key={p.key}>
                  {i > 0 && " · "}
                  {p.current ? <strong>{p.name} ({p.role})</strong> : <a className="alert-link" href={p.url}>{p.name} ({p.role})</a>}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
      {email && (
        <div className="alert alert-warning app-notice">
          <i className="bi bi-envelope-exclamation" aria-hidden="true" />
          <span>
            {email.has_email
              ? `Confirm your email ${email.email} so we can reach you if you lose your phone.`
              : "Add your email so we can reach you if you lose your phone."}
          </span>
          <a className="btn btn-primary btn-sm" href={email.url}>{email.has_email ? "Confirm email" : "Add email"}</a>
        </div>
      )}
      {terms && <TermsNotice acceptUrl={urls.accept_terms} termsUrl={urls.terms} privacyUrl={urls.privacy} />}
    </>
  );
}
