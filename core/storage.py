"""Static file URLs that change when the file changes (D-141).

Static files are not renamed with a hash, so a browser or the app's service worker could keep an old app.css
next to new pages after a deploy. Each CSS and JS URL gets ?v=<size and time> of the file, so a new
version is a new address and is fetched fresh. Fonts, images and vendored libraries keep their plain URLs.
"""

import os

from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import StaticFilesStorage

VERSIONED = (".css", ".js")


class VersionedStaticFilesStorage(StaticFilesStorage):
    def url(self, name):
        url = super().url(name)
        if not name or not name.endswith(VERSIONED) or name.startswith("vendor/"):
            return url
        path = self.path(name) if self.exists(name) else finders.find(name)
        if not path:
            return url
        try:
            stat = os.stat(path)
        except OSError:
            return url
        return f"{url}?v={stat.st_size:x}{int(stat.st_mtime):x}"
