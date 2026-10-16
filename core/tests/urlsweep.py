"""Every route in the project, with its parameters filled in, for the sweeps in test_hardening (D-097)."""

import re
import uuid

from django.urls import URLPattern, URLResolver, get_resolver
from django.urls.resolvers import RoutePattern

FILL = {"int": "500000", "str": "x", "slug": "x", "uuid": str(uuid.UUID(int=500000)), "path": "x"}


def routes():
    """(path, name, view class or function) for every route outside the Django admin."""
    out = []

    def walk(patterns, prefix, namespace):
        for p in patterns:
            if not isinstance(p.pattern, RoutePattern):
                continue
            route = prefix + str(p.pattern)
            if isinstance(p, URLResolver):
                if route.startswith(("admin/", "ok-ml-staff/")):
                    continue
                ns = ":".join(filter(None, [namespace, p.namespace]))
                walk(p.url_patterns, route, ns)
            elif isinstance(p, URLPattern):
                path = "/" + re.sub(r"<(?:(\w+):)?\w+>", lambda m: FILL[m.group(1) or "str"], route)
                name = ":".join(filter(None, [namespace, p.name]))
                view = getattr(p.callback, "view_class", p.callback)
                out.append((path, name, view))

    walk(get_resolver().url_patterns, "", "")
    return out
