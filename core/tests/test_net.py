import pytest
from django.test import RequestFactory

from core.net import client_ip


@pytest.mark.parametrize("proxies, forwarded, expected", [
    (0, "6.6.6.6", "10.0.0.1"),                   # no proxy trusted: the header is ignored
    (1, "203.0.113.5", "203.0.113.5"),            # one proxy wrote the client's address
    (1, "6.6.6.6, 203.0.113.5", "203.0.113.5"),   # a spoofed entry on the left is ignored
    (2, "6.6.6.6, 203.0.113.5, 10.0.0.9", "203.0.113.5"),
    (2, "203.0.113.5", "10.0.0.1"),               # fewer hops than proxies: don't guess
    (1, "", "10.0.0.1"),
])
def test_client_ip(settings, proxies, forwarded, expected):
    settings.TRUSTED_PROXY_COUNT = proxies
    request = RequestFactory().get("/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR=forwarded)
    assert client_ip(request) == expected
