"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { AskState } from "@/lib/types";
import { BrandMark } from "./BrandMark";
import { AskPanel } from "./screens/AskPanel";

/** The floating Ask button at the bottom right of every page: opens the assistant in a panel without leaving the page. */
export function AskButton() {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<AskState | null>(null);
  const [failed, setFailed] = useState(false);
  const button = useRef<HTMLButtonElement>(null);

  // Load the conversation the first time the panel is opened.
  useEffect(() => {
  if (!open || state) return;
  fetch("/ask/web-api/", { credentials: "same-origin" })
      .then((r) => (r.ok ? (r.json() as Promise<AskState>) : Promise.reject(new Error(String(r.status)))))
      .then(setState)
      .catch(() => setFailed(true));
  }, [open, state]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  // The full Ask page has the same conversation; no button needed there.
  if (path.startsWith("/ask")) return null;

  return (
    <>
      {open && (
        <section className="ask-float" role="dialog" aria-label="NyumbaPMS AI">
          <header className="ask-float-head">
            <strong className="ask-float-brand">
              <BrandMark />
              <span>Nyumba<span className="brand-pms">PMS</span> AI</span>
            </strong>
            <span>
              <a className="btn btn-quiet" href="/ask/">Open full page</a>
              <button className="btn btn-quiet" type="button" onClick={() => setOpen(false)} aria-label="Close">
                <i className="bi bi-x-lg" aria-hidden="true" />
              </button>
            </span>
          </header>
          <div className="ask-float-body ask">
            {state ? (
              <AskPanel initial={state} compact />
            ) : failed ? (
              <p className="notice-bad" role="alert">Could not load the assistant. Try again.</p>
            ) : (
              <p className="muted" role="status">Loading…</p>
            )}
          </div>
        </section>
      )}
      <button
        ref={button}
        className="ask-fab"
        type="button"
        aria-expanded={open}
        aria-label={open ? "Close NyumbaPMS AI" : "Open NyumbaPMS AI"}
        onClick={() => setOpen((o) => !o)}
      >
        <i className={`bi ${open ? "bi-x-lg" : "bi-stars"}`} aria-hidden="true" />
        <span>Nyumba<b>PMS</b> AI</span>
      </button>
    </>
  );
}
