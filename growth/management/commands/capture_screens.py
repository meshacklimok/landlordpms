"""Take the real product pictures for the public pages from the "Try the demo" landlord (D-220).

Run it on a machine with the app running and a fresh demo (`manage.py seed_public_demo`):

    python manage.py capture_screens                      # every screen in growth/screens.py, light and dark
    python manage.py capture_screens --only dashboard     # just one
    python manage.py capture_screens --base-url http://127.0.0.1:3000

Needs Playwright (requirements-dev.txt) and Chrome; `--channel ""` uses Playwright's own Chromium instead
(`playwright install chromium`). The pictures are WebP at 1x and 2x in static/img/screens/, with screens.json
holding their sizes and a version for the URLs. Commit them: production serves them as static files, and the
demo's sample data is all they show.

The visitor is signed in the way "Try the demo" does it (accounts.demo.switch), straight into a session, so no
form is posted. Those sessions are deleted at the end.
"""

import io
import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory
from django.utils import timezone

from accounts import demo
from growth import screens

STATIC = Path(settings.BASE_DIR) / "static"
OUT = STATIC / screens.DIR
COMPUTER = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}
CROP_RATIO = 0.68  # a cropped part of a page is cut to this height / width, so the tabs line up
CROP_MARGIN = 16  # page background kept around a cropped part, in CSS pixels (<main> has its own padding)
WEBP_QUALITY = 82
RETRIES = 2
# Left out of every picture: the demo strip, the floating buttons, the install bar, the "jump to" row of long report
# pages (it would be cut in half above a cropped section), and anything moving.
HIDE_CSS = """
[data-demo-notice], .help-fab, .ask-fab, .report-fab, .back-to-top, .install-bar, .report-nav { display: none !important; }
*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }
"""

# Everything before the part on the page is hidden (its space kept), so the margin above and beside it is plain page.
HIDE_BEFORE_JS = """el => {
  for (let node = el; node && node !== document.body; node = node.parentElement)
    for (let prev = node.previousElementSibling; prev; prev = prev.previousElementSibling) prev.style.visibility = "hidden";
}"""


class Command(BaseCommand):
    help = "Take the product screenshots for the public pages from the demo landlord (light and dark, 1x and 2x)."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", default="http://127.0.0.1:3000",
                            help="Where the app runs: the Next.js address, as visitors see it (default %(default)s).")
        parser.add_argument("--only", nargs="+", choices=list(screens.BY_KEY), help="Take only these screens.")
        parser.add_argument("--channel", default="chrome",
                            help='Browser to drive: "chrome" (installed Chrome), or "" for Playwright\'s Chromium.')

    def handle(self, *args, base_url, only, channel, **options):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CommandError("Playwright is not installed: pip install -r requirements-dev.txt") from exc
        from PIL import Image

        if demo.demo_org() is None:
            raise CommandError("There is no demo organization yet: run manage.py seed_public_demo first.")
        shots = [s for s in screens.SHOTS if not only or s.key in only]
        # Sessions are made before the browser starts: Playwright runs an event loop, and the ORM refuses to run
        # inside one.
        sessions = {person: self._session(person) for person in {s.person for s in shots}}
        fill = self._placeholders()
        base_url = base_url.rstrip("/")
        OUT.mkdir(parents=True, exist_ok=True)
        sizes = {}
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(**({"channel": channel} if channel else {}))
                try:
                    for shot in shots:
                        for theme in screens.THEMES:
                            png = self._take(browser, base_url + shot.path.format(**fill), sessions[shot.person],
                                             shot, theme)
                            sizes[shot.key] = self._save(Image, png, shot.key, theme)
                        self.stdout.write(f"{shot.key}: {sizes[shot.key]['width']}×{sizes[shot.key]['height']}")
                finally:
                    browser.close()
        finally:
            for session in sessions.values():
                session.delete()
            self._write_manifest(sizes)  # the screens taken before a failure are kept and listed
        self.stdout.write(self.style.SUCCESS(f"{len(sizes)} screens saved to {OUT}."))

    def _session(self, person: str):
        """A signed-in session as this sample person, made the way the demo's own buttons make one."""
        request = RequestFactory().get("/")
        SessionMiddleware(lambda r: None).process_request(request)
        request.user = AnonymousUser()
        if not demo.switch(request, person):
            raise CommandError(f"The demo has no sample {person}: run manage.py seed_public_demo again.")
        request.session.save()
        return request.session

    def _placeholders(self) -> dict:
        """What {property} and {lease} in a shot's path stand for: the demo's Riverside Apartments (its first
        property if renamed), and the sample tenant's lease."""
        from portal.models import TenantAccount
        from properties.models import Property

        org = demo.demo_org()
        props = Property.objects.for_org(org).order_by("pk")
        prop = props.filter(code="RVS").first() or props.first()
        account = TenantAccount.objects.filter(organization=org, revoked_at__isnull=True).select_related("tenant").first()
        link = account and account.tenant.lease_links.filter(lease__status="ACTIVE").select_related("lease").first()
        if prop is None or link is None:
            raise CommandError("The demo has no property or no sample tenant lease: run manage.py seed_public_demo again.")
        return {"property": prop.public_id, "lease": link.lease.public_id}

    def _take(self, browser, url: str, session, shot: screens.Shot, theme: str) -> bytes:
        viewport = PHONE if shot.phone else COMPUTER
        context = browser.new_context(viewport=viewport, device_scale_factor=2, color_scheme=theme,
                                      reduced_motion="reduce", is_mobile=shot.phone, has_touch=shot.phone,
                                      locale="en-KE", timezone_id="Africa/Nairobi")
        try:
            context.add_cookies([{"name": settings.SESSION_COOKIE_NAME, "value": session.session_key, "url": url}])
            # The theme is the visitor's own choice on this device (static/js/theme.js); set it before any page runs.
            context.add_init_script(f"try {{ localStorage.setItem('theme', '{theme}'); }} catch (e) {{}}")
            page = context.new_page()
            for attempt in range(RETRIES + 1):  # a dev server restarting after a file change answers 500 for a moment
                response = page.goto(url, wait_until="networkidle")
                if response is None or response.status < 500 or attempt == RETRIES:
                    break
                page.wait_for_timeout(3000)
            if response is None or not response.ok or "/login" in page.url:
                raise CommandError(f"{shot.key}: {shot.path} did not open as the demo "
                                   f"({response.status if response else 'no answer'} at {page.url}).")
            page.add_style_tag(content=HIDE_CSS + (f"{shot.hide} {{ display: none !important; }}" if shot.hide else ""))
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(800)  # charts draw after the data arrives
            if not shot.crop:
                return page.screenshot(type="png")
            part = page.locator(shot.crop).first
            box = part.bounding_box()
            if box is None:
                raise CommandError(f"{shot.key}: nothing matches {shot.crop!r} on {shot.path}.")
            margin = 0 if shot.crop == "main" else CROP_MARGIN
            if margin:
                part.evaluate(HIDE_BEFORE_JS)
            # Always the same shape, whatever follows the part on the page, so the pictures line up.
            x, y = max(box["x"] - margin, 0), max(box["y"] - margin, 0)
            width = min(box["width"] + 2 * margin, viewport["width"] - x)
            clip = {"x": x, "y": y, "width": width, "height": round(width * CROP_RATIO)}
            return page.screenshot(type="png", clip=clip, full_page=True)
        finally:
            context.close()

    def _save(self, Image, png: bytes, key: str, theme: str) -> dict:
        """Writes the 2x picture as taken and a 1x copy half the size; returns the 1x size."""
        image = Image.open(io.BytesIO(png)).convert("RGB")
        image.save(STATIC / screens.filename(key, theme, 2), "WEBP", quality=WEBP_QUALITY, method=6)
        small = image.resize((image.width // 2, image.height // 2), Image.Resampling.LANCZOS)
        small.save(STATIC / screens.filename(key, theme, 1), "WEBP", quality=WEBP_QUALITY, method=6)
        return {"width": small.width, "height": small.height}

    def _write_manifest(self, sizes: dict) -> None:
        """screens.json: kept entries for screens not retaken, and a new version so browsers fetch the new files."""
        path = OUT / "screens.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {}
        now = timezone.now()
        manifest = {"version": now.strftime("%Y%m%d%H%M"), "taken": now.isoformat(timespec="seconds"),
                    "shots": {**manifest.get("shots", {}), **sizes}}
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
