"""Where browsers report what the Content Security Policy blocked (D-107).

A report says which rule blocked what on which page. Only the page path, the rule, the blocked origin and the script
file and line are logged: never query strings, which can hold tokens, and never the sample of blocked code. Anyone
can post here, so reports are small, limited per IP and never stored.
"""

import json
import logging
from urllib.parse import urlsplit

from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core import ratelimit, watch
from core.net import client_ip

logger = logging.getLogger("django.security.csp")

MAX_BYTES = 10 * 1024
PER_IP_MINUTE = 20


def _where(url) -> str:
    """The origin and path of a URL, or a keyword such as "inline" or "eval", without the query string."""
    url = str(url or "")
    if "://" not in url:
        return url[:40]
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"[:200]


def violations(body: bytes) -> list[dict]:
    """The reports in a body, in either format browsers send (report-uri, or the Reporting API's list)."""
    try:
        data = json.loads(body)
    except ValueError:
        return []
    found = []
    if isinstance(data, dict) and isinstance(data.get("csp-report"), dict):
        r = data["csp-report"]
        found.append({"page": r.get("document-uri"),
                      "rule": r.get("effective-directive") or r.get("violated-directive"),
                      "blocked": r.get("blocked-uri"), "file": r.get("source-file"), "line": r.get("line-number")})
    elif isinstance(data, list):
        for item in data[:10]:
            r = item.get("body") if isinstance(item, dict) and item.get("type") == "csp-violation" else None
            if isinstance(r, dict):
                found.append({"page": r.get("documentURL"), "rule": r.get("effectiveDirective"),
                              "blocked": r.get("blockedURL"), "file": r.get("sourceFile"), "line": r.get("lineNumber")})
    return found


@method_decorator(csrf_exempt, name="dispatch")
class CspReportView(View):
    def post(self, request):
        if len(request.body) <= MAX_BYTES and ratelimit.hit(f"csp:ip:{client_ip(request)}", PER_IP_MINUTE, 60):
            for v in violations(request.body):
                watch.count("csp")
                line =v["line"] if isinstance(v["line"], int) else ""
                logger.warning("CSP blocked %s on %s (rule %s, from %s:%s)", _where(v["blocked"]),
                               urlsplit(str(v["page"] or "")).path[:200], str(v["rule"] or "")[:40],
                               _where(v["file"]), line)
        return HttpResponse(status=204)
