# NyumbaPMS screens (Next.js)

Next.js (App Router) and strict TypeScript, drawing the signed-in screens one at a time (D-168). Django stays the back
end: models, M-Pesa, webhooks, USSD, the public pages. Anything this app does not draw is passed on to Django
(`next.config.ts`), so a page moves over by adding a route here.

## Run it

```bash
# terminal 1, from the project root
.venv/bin/python manage.py runserver 8000
# terminal 2
cd web && cp .env.example .env.local && npm install && npm run dev   # http://localhost:3000
```

`DJANGO_URL` (default `http://127.0.0.1:8000`) is where Django runs. The browser never sees it.

## How data reaches a screen

- `src/lib/django.ts` reads Django's `/web-api/` views on the server, as the signed-in member (their session cookie is
  passed on). A signed-out visitor is sent to Django's sign-in page.
- `src/lib/types.ts` mirrors those views' JSON. `accounts/tests/test_web_api.py` fails if the two disagree.
- POSTs from the browser go through `src/lib/csrf.ts` (fetches a CSRF token first).

## Before this goes live

- nginx must set `X-Forwarded-For` (it does) and Django needs `TRUSTED_PROXY_COUNT=2` once Next.js sits between nginx and
  Django, or every visitor shares one rate-limit address.
- Pages served here carry no Django CSP header yet; add one (with a nonce) in `next.config.ts` or a proxy.

Check: `npx tsc --noEmit && npx eslint src`.
