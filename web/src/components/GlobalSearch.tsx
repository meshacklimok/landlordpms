"use client";

import { useEffect, useRef, useState } from "react";

type Hit = { url: string; label: string; detail?: string };
type Group = { key: string; title: string; hits: Hit[] };

/** The search box in the top bar: a plain GET form (works without script), plus suggestions as you type and "/" to focus. */
export function GlobalSearch({ action, suggestUrl }: { action: string; suggestUrl: string }) {
  const [q, setQ] = useState("");
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLFormElement>(null);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable;
      if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    const outside = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", key);
    document.addEventListener("mousedown", outside);
    return () => {
      document.removeEventListener("keydown", key);
      document.removeEventListener("mousedown", outside);
    };
  }, []);

  useEffect(() => {
    const term = q.replace(/\s+/g, " ").trim().slice(0, 60);
    if (term.length < 2) return;
    const ctl = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const r = await fetch(`${suggestUrl}?q=${encodeURIComponent(term)}`, { credentials: "same-origin", signal: ctl.signal });
        if (!r.ok) throw new Error(String(r.status));
        setGroups(((await r.json()) as { groups: Group[] }).groups);
        setFailed(false);
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        setFailed(true);
      }
      setOpen(true);
    }, 180);
    return () => {
      clearTimeout(timer);
      ctl.abort();
    };
  }, [q, suggestUrl]);

  const short = q.trim().length < 2;
  const hits = groups?.reduce((n, g) => n + g.hits.length, 0) ?? 0;
  return (
    <form ref={box} method="get" action={action} className="global-search" role="search">
      <i className="bi bi-search" aria-hidden="true" />
      <input
        ref={input}
        type="search"
        role="combobox"
        aria-autocomplete="list"
        name="q"
        maxLength={60}
        autoComplete="off"
        spellCheck={false}
        placeholder="Search tenants, units, invoices"
        aria-label="Search tenants, units, leases, invoices and payments"
        aria-keyshortcuts="/"
        aria-controls="global-search-list"
        aria-expanded={open && !short}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => groups && setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
        }}
      />
      <kbd className="global-search-key" aria-hidden="true">/</kbd>
      {open && !short && (
        <div id="global-search-list" className="global-search-list" role="listbox" aria-label="Suggestions">
          {failed && <div className="gs-none">Search is not available right now</div>}
          {!failed &&
            groups?.map(
              (g) =>
                g.hits.length > 0 && (
                  <div key={g.key}>
                    <div className="gs-head" role="presentation">{g.title}</div>
                    {g.hits.map((h) => (
                      <a key={h.url + h.label} className="gs-item" href={h.url} role="option" aria-selected="false">
                        <div>{h.label}</div>
                        {h.detail && <div className="small muted">{h.detail}</div>}
                      </a>
                    ))}
                  </div>
                ),
            )}
          {!failed && hits === 0 && <div className="gs-none">No matches</div>}
          <a className="gs-item gs-all" href={`${action}?q=${encodeURIComponent(q)}`}>See all results</a>
        </div>
      )}
    </form>
  );
}
