/** POSTs to a Django address from the browser: fetch a CSRF token first (which also sets Django's cookie). */
export async function djangoPost(path: string, body?: BodyInit): Promise<Response> {
  const { token } = (await (await fetch("/web-api/csrf/", { credentials: "same-origin" })).json()) as {
    token: string;
  };
  return fetch(path, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": token }, body });
}

/** What a JSON screen view answers to a save: the data on success, or the message (and field errors) Django gave. */
export type SaveResult<T> =
  | { ok: true; data: T }
  | { ok: false; message: string; fields: Record<string, string[]> };

async function save<T>(path: string, body: BodyInit, contentType?: string): Promise<SaveResult<T>> {
  const { token } = (await (await fetch("/web-api/csrf/", { credentials: "same-origin" })).json()) as {
    token: string;
  };
  const headers: Record<string, string> = { "X-CSRFToken": token };
  if (contentType) headers["Content-Type"] = contentType; // a FormData body sets its own, with the boundary
  const response = await fetch(path, { method: "POST", credentials: "same-origin", headers, body });
  const data = (await response.json().catch(() => ({}))) as T & {
    message?: string;
    fields?: Record<string, string[]>;
  };
  if (response.ok) return { ok: true, data };
  return {
    ok: false,
    message: data.message ?? (response.status === 403 ? "Your session has ended. Sign in again." : "That did not save. Try again."),
    fields: data.fields ?? {},
  };
}

/** POSTs JSON to a Django screen view and reads its answer, including the 400/422 messages, so a form can show them. */
export function djangoPostJson<T>(path: string, payload: unknown): Promise<SaveResult<T>> {
  return save<T>(path, JSON.stringify(payload), "application/json");
}

/** POSTs a form with files (multipart) to a Django screen view; the answer is read as for `djangoPostJson`. */
export function djangoPostForm<T>(path: string, form: FormData): Promise<SaveResult<T>> {
  return save<T>(path, form);
}
