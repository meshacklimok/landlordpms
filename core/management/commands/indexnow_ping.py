"""Tell Bing, Yandex and other IndexNow search engines which public pages exist or changed (D-158).

Run after a deploy that changes public pages. Needs INDEXNOW_KEY (served at /indexnow/<key>.txt). Google does not use
IndexNow: submit sitemap.xml in Search Console once instead.
"""
import json
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from growth import seo

ENDPOINT = "https://api.indexnow.org/indexnow"


class Command(BaseCommand):
    help = "Submit the public page URLs to IndexNow (Bing, Yandex)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List the URLs without sending them.")

    def handle(self, *args, dry_run=False, **options):
        key = getattr(settings, "INDEXNOW_KEY", "")
        if not key:
            raise CommandError("Set INDEXNOW_KEY first (any 8 to 128 letters or digits).")
        urls = [seo.absolute(page.path) for page in seo.all_pages()]
        if dry_run:
            self.stdout.write("\n".join(urls))
            return
        host = settings.SITE_URL.split("://", 1)[1]
        body = json.dumps({"host": host, "key": key, "keyLocation": f"{settings.SITE_URL}/indexnow/{key}.txt",
                           "urlList": urls}).encode()
        request = urllib.request.Request(ENDPOINT, body, {"Content-Type": "application/json; charset=utf-8"})  # noqa: S310 - the address is a fixed https endpoint, never user input
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - the address is a fixed https endpoint, never user input
            self.stdout.write(f"Sent {len(urls)} URLs; IndexNow answered {response.status}.")
