"""Refuses a request carrying a NUL byte in its address or form fields.

PostgreSQL text fields cannot hold one, so a search typed as `?q=%00` reached the database and ended as a 500.
Nothing legitimate sends it, so the answer is a plain 400.
"""

from urllib.parse import unquote_to_bytes

from django.http import HttpResponseBadRequest


class RejectNulMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if b"\x00" in unquote_to_bytes(request.META.get("QUERY_STRING", "")) or "\x00" in request.path_info:
            return HttpResponseBadRequest("Bad request.")
        if request.method == "POST" and request.content_type == "application/x-www-form-urlencoded":
            if b"\x00" in unquote_to_bytes(request.body.replace(b"+", b" ")):
                return HttpResponseBadRequest("Bad request.")
        return self.get_response(request)
