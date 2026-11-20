"use client";
import Link from "next/link";

/** Shown when a screen cannot be drawn, for example while Django is restarting. Nothing the member typed is lost: they retry. */
export default function Error({ retry }: { error: unknown; retry: () => void }) {
  return (
    <main id="main" style={{ maxWidth: "32rem", margin: "4rem auto", padding: "0 1rem" }}>
      <h1 style={{ fontSize: "1.4rem" }}>Something went wrong on our side</h1>
      <p style={{ color: "var(--muted)" }}>
        The page could not load. This is usually brief. Try again, and if it keeps happening, tell us on the Help page.
      </p>
      <p style={{ display: "flex", gap: ".5rem", flexWrap: "wrap" }}>
        <button type="button" className="btn btn-primary" onClick={() => retry()}>Try again</button>
        <Link className="btn btn-outline" href="/">Go home</Link>
      </p>
    </main>
  );
}
