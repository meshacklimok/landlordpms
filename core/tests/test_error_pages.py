"""Unknown URLs and server errors show our own pages, not Django's defaults."""

import pytest
from django.template.loader import render_to_string

pytestmark = pytest.mark.django_db


def test_an_unknown_url_shows_our_not_found_page(client):
    response = client.get("/no-such-page-here/")
    assert response.status_code == 404
    assert "We could not find that page" in response.content.decode()


def test_the_server_error_page_is_standalone_and_shows_no_details():
    html = render_to_string("500.html")
    assert "Something went wrong on our side" in html
    assert "Traceback" not in html and "{%" not in html
