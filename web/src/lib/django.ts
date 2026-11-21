import "server-only";
import { cookies, headers } from "next/headers";
import { notFound, redirect } from "next/navigation";

const DJANGO = process.env.DJANGO_URL ?? "http://127.0.0.1:8000";

/**
 * The X-Forwarded-For Django should see: exactly what nginx sent. nginx has already put the visitor's address in it,
 * and deploy/setup.sh sets TRUSTED_PROXY_COUNT=1 (Django reads the last entry). Adding a hop here made Django read
 * 127.0.0.1 for every page this app draws, so per-address limits and the audit log lost the visitor's address.
 * With no header at all (dev, no nginx) it stays empty and Django uses the socket.
 */
function forwardedFor(incoming: string | null): string {
  return incoming ?? "";
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
      // Django only answers for its own domain (ALLOWED_HOSTS), not for 127.0.0.1:8000, so pass the visitor's Host on.
      ...(incoming.get("host") ? { host: incoming.get("host") as string } : {}),
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
