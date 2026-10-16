"use client";

import { useState, type ReactNode } from "react";
import type { Session } from "@/lib/types";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

/** Holds whether the phone menu is open, which the top bar's button and the menu share. */
export function ShellFrame({ session, children }: { session: Session; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="shell">
      <Sidebar session={session} open={open} onClose={() => setOpen(false)} />
      {open && <div className="sidebar-scrim" onClick={() => setOpen(false)} aria-hidden="true" />}
      <div className="shell-body">
        <TopBar session={session} menuOpen={open} onMenu={() => setOpen((v) => !v)} />
        <main id="main" className="shell-main" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
