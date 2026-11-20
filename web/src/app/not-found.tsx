import Link from "next/link";

/** A screen that does not exist, or a record the member may not see (both answer the same, on purpose). */
export default function NotFound() {
  return (
    <main id="main" style={{ maxWidth: "32rem", margin: "4rem auto", padding: "0 1rem" }}>
      <h1 style={{ fontSize: "1.4rem" }}>We could not find that page</h1>
      <p style={{ color: "var(--muted)" }}>The link may be old or mistyped, or the record may have been removed.</p>
      <p><Link className="btn btn-primary" href="/">Go home</Link></p>
    </main>
  );
}
