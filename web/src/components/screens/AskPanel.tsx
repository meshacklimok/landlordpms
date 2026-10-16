"use client";

import { useEffect, useRef, useState } from "react";
import { djangoPostJson } from "@/lib/csrf";
import type { AskCard, AskState } from "@/lib/types";

type Reply = AskState & { message?: string };
type Notice = { ok: boolean; text: string } | null;

const when = (iso: string) =>
  new Date(iso).toLocaleString("en-KE", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** The question box, the conversation, the proposals waiting for Confirm, and (for owners) the switches. */
export function AskPanel({ initial }: { initial: AskState }) {
  const [state, setState] = useState(initial);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [texts, setTexts] = useState<Record<string, string>>({});
  const end = useRef<HTMLDivElement>(null);
  const turnCount = state.turns.length;

  // After a new answer, bring it into view.
  useEffect(() => {
    if (turnCount > 0) end.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [turnCount]);

  async function send(key: string, payload: Record<string, unknown>, after?: () => void) {
    setPending(key);
    setNotice(null);
    const result = await djangoPostJson<Reply>(state.post_url, payload);
    setPending(null);
    if (!result.ok) return setNotice({ ok: false, text: result.message });
    const { message, ...next } = result.data;
    setState(next);
    if (message) setNotice({ ok: true, text: message });
    after?.();
  }

  function ask(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim()) return;
    void send("ask", { action: "ask", question }, () => setQuestion(""));
  }

  const card = (c: AskCard) => {
    const text = texts[c.id] ?? c.text;
    const shown = c.text && c.state !== "open" ? <p className="ask-text">{c.text}</p> : null;
    return (
      <section className="card ask-card" key={c.id} id={`p-${c.id}`} aria-label={c.title}>
        <h2>{c.title}</h2>
        <dl className="ask-lines">
          {c.lines.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        {c.state === "done" ? (
          <>
            {shown}
            <p className="ok-text">
              {c.result} {c.url && <a href={c.url}>Open</a>}
            </p>
          </>
        ) : c.state === "dismissed" ? (
          <>
            {shown}
            <p className="muted">Dismissed. Nothing was done.</p>
          </>
        ) : c.expired ? (
          <>
            {c.text && <p className="ask-text">{c.text}</p>}
            <p className="muted">This has expired. Ask again if it is still needed.</p>
          </>
        ) : (
          <>
            {c.kind === "message" && (
              <div className="field">
                <label htmlFor={`text-${c.id}`}>Message</label>
                <textarea
                  id={`text-${c.id}`}
                  rows={3}
                  maxLength={459}
                  value={text}
                  onChange={(e) => setTexts((cur) => ({ ...cur, [c.id]: e.target.value }))}
                />
              </div>
            )}
            <p className="muted small">Nothing is done until you confirm. Check the details first.</p>
            <div className="form-actions">
              <button
                className="btn btn-primary"
                type="button"
                disabled={pending !== null}
                onClick={() => void send(c.id, { action: "confirm", id: c.id, text: c.kind === "message" ? text : undefined })}
              >
                Confirm
              </button>
              <button
                className="btn btn-outline"
                type="button"
                disabled={pending !== null}
                onClick={() => void send(c.id, { action: "dismiss", id: c.id })}
              >
                Dismiss
              </button>
            </div>
          </>
        )}
      </section>
    );
  };

  const switchBlock = state.can_switch && state.platform_on && (
    <section className="card ask-settings" id="settings" aria-labelledby="ask-settings-h">
      <h2 id="ask-settings-h">Settings</h2>
      <p className="small">
        To answer, the question and the names and figures used to answer it (tenants, properties, units, amounts, dates
        and repair titles) are sent to OpenAI, which runs the GPT model. Phone numbers, emails, ID numbers, notes,
        documents and photos are never sent. Questions are kept for 90 days.
      </p>
      <button
        className={`btn ${state.is_on ? "btn-outline" : "btn-primary"}`}
        type="button"
        disabled={pending !== null}
        onClick={() => void send("switch", { action: "switch", on: !state.is_on })}
      >
        {state.is_on ? "Switch the assistant off" : "Switch the assistant on"}
      </button>

      <h3 id="knowledge">What the assistant knows</h3>
      <p className="small">
        Add your own rules and practices, such as office hours, deposit refunds or house rules, for the assistant to
        answer from. Choose for each one whether tenants can be told it.
      </p>
      <a className="btn btn-outline" href={state.knowledge_url}>
        {state.knowledge_count
          ? `${state.knowledge_count} ${state.knowledge_count === 1 ? "entry" : "entries"}`
          : "Add entries"}
      </a>

      {state.tenants_available && (
        <>
          <h3 id="tenants">Answer tenants</h3>
          <p className="small">
            {state.tenants_whatsapp
              ? "Tenants can ask on the Ask page of the tenant portal, and by messaging your WhatsApp number from the phone on their tenant record. "
              : "Tenants can ask on the Ask page of the tenant portal. "}
            They get answers about their own rent, balance, how to pay, receipts and repairs. They can also ask to report
            a repair or to get an M-Pesa prompt on their own phone, which happens only after they confirm. Their question
            and their own figures are sent to OpenAI, which runs the GPT model. If a tenant has set a USSD PIN, balances
            and payments are not given on WhatsApp. Tenants&apos; questions are kept for 90 days and are not shown to staff.
          </p>
          <button
            className={`btn ${state.tenants_on ? "btn-outline" : "btn-primary"}`}
            type="button"
            disabled={pending !== null}
            onClick={() => void send("tenants", { action: "switch", which: "tenants", on: !state.tenants_on })}
          >
            {state.tenants_on ? "Stop answering tenants" : "Answer tenants"}
          </button>
        </>
      )}
    </section>
  );

  return (
    <>
      <div aria-live="polite">
        {notice && (
          <p className={notice.ok ? "notice-ok" : "notice-bad"} role={notice.ok ? "status" : "alert"}>
            {notice.text}
          </p>
        )}
      </div>

      {!state.platform_on ? (
        <p className="card muted small">The assistant is not set up on this service yet.</p>
      ) : !state.is_on ? (
        <p className="card muted small">
          {state.can_switch
            ? "The assistant is off for your organization. You can switch it on below."
            : "The assistant is off for your organization. An owner can switch it on."}
        </p>
      ) : (
        <>
          {state.turns.length > 0 && (
            <div className="ask-thread" aria-label="Conversation">
              {state.turns.map((turn, i) => (
                <div className="ask-turn" key={`${i}-${turn.q}`}>
                  <p className="ask-q">{turn.q}</p>
                  <div className={`card ask-a${turn.ok ? "" : " ask-a-warn"}`}>
                    {turn.a.split("\n").map((line, j) => (
                      <p key={j}>{line}</p>
                    ))}
                    {turn.links.length > 0 && (
                      <p className="muted small">
                        Pages used:{" "}
                        {turn.links.map((l) => (
                          <a key={l.url} href={l.url} className="ask-link">
                            {l.label}
                          </a>
                        ))}
                      </p>
                    )}
                  </div>
                  {turn.cards.map(card)}
                </div>
              ))}
              <div ref={end} />
            </div>
          )}

          <form className="card" onSubmit={ask}>
            <div className="field">
              <label htmlFor="question">{turnCount ? "Ask a follow-up question" : "Your question"}</label>
              <textarea
                id="question"
                rows={2}
                maxLength={state.max_length}
                required
                value={question}
                placeholder="Who owes more than 30 days at Acacia Court?"
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) e.currentTarget.form?.requestSubmit();
                }}
              />
              <span className="hint">
                {question.length} of {state.max_length} characters. Press Ctrl+Enter to send.
              </span>
            </div>
            <div className="form-actions">
              <button className="btn btn-primary" type="submit" disabled={pending !== null || !question.trim()}>
                {pending === "ask" ? "Thinking…" : "Ask"}
              </button>
              {turnCount > 0 && (
                <button
                  className="btn btn-quiet"
                  type="button"
                  disabled={pending !== null}
                  onClick={() => void send("new", { action: "new" })}
                >
                  New conversation
                </button>
              )}
            </div>
          </form>
          <p className="muted small">
            Check important figures on their page before acting on them. You can ask {state.user_daily} questions a day.
            The assistant can propose a payment, a message or a repair assignment, but nothing happens until you press
            Confirm.
          </p>

          {state.recent.length > 0 && (
            <details className="ask-recent">
              <summary className="small">Your recent questions</summary>
              <ul className="small">
                {state.recent.map((r) => (
                  <li key={`${r.at}-${r.question}`}>
                    {r.question}{" "}
                    <span className="muted">
                      · {when(r.at)}
                      {r.status && ` · ${r.status}`}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}

      {switchBlock}
    </>
  );
}
