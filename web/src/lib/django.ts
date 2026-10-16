import "server-only";
import { cookies, headers } from "next/headers";
import { notFound, redirect } from "next/navigation";

const DJANGO = process.env.DJANGO_URL ?? "http://127.0.0.1:8000";

/**
 * The X-Forwarded-For Django should see. nginx has already put the visitor's address in it. Next.js's own
 * pass-through of other pages (the rewrites) adds the address it received from, which is nginx on this machine,
 * so add the same hop here: Django then reads the same chain whichever way a request arrives, and prod sets
 * TRUSTED_PROXY_COUNT=2 (nginx and Next.js). Appended on the right, so anything a visitor forged stays on the
 * left where Django ignores it. With no header at all (dev, no nginx) it stays empty and Django uses the socket.
 */
function forwardedFor(incoming: string | null): string {
  return incoming ? `${incoming}, 127.0.0.1` : "";
}

/**
 * Reads one of Django's `/web-api/` views as the signed-in member: their own session cookie is passed on, so
 * they see exactly what the Django page would show them. A signed-out or half-signed-in member is sent to
 * Django's sign-in page (still a Django page) rather than shown an error.
 */
export async function djangoGet<T>(path: string): Promise<T> {
  const [jar, incoming] = await Promise.all([cookies(), headers()]);
  const response = await fetch(`${DJANGO}${path}`, {
    cache: "no-store",
    headers: {
      cookie: jar.toString(),
      "accept-language": incoming.get("accept-language") ?? "en",
      // Django decides "secure request" from this when it sits behind nginx.
      "x-forwarded-proto": incoming.get("x-forwarded-proto") ?? "http",
      "x-forwarded-for": forwardedFor(incoming.get("x-forwarded-for")),
    },
  });
  if (response.status === 404) notFound();
  if (response.status === 401 || response.status === 403) {
    const body = (await response.json().catch(() => ({}))) as { error?: string; login_url?: string };
    if (body.error === "owner_only") notFound(); // signed in, but this page is not theirs: same answer as a page that is not there
    redirect(body.login_url ?? "/login/");
  }
  if (!response.ok) throw new Error(`Django answered ${response.status} for ${path}`);
  return (await response.json()) as T;
}
