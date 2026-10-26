"""Real product screens on the home page (D-220): shown when captured, the drawn sample when not."""

import json

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from growth import content, pages, screens, solutions

pytestmark = pytest.mark.django_db


@pytest.fixture
def static_dir(tmp_path):
    """An empty static folder in place of the project's, so each test decides which screens exist."""
    with override_settings(STATICFILES_DIRS=[tmp_path]):
        yield tmp_path


def capture(root, keys, version="202610250900", skip=()):
    """What `capture_screens` leaves behind, without a browser: the four files of each screen and the manifest."""
    (root / screens.DIR).mkdir(parents=True, exist_ok=True)
    for key in keys:
        for theme in screens.THEMES:
            for scale in (1, 2):
                name = screens.filename(key, theme, scale)
                if name not in skip:
                    (root / name).write_bytes(b"RIFF")
    shots = {key: {"width": 390, "height": 844} if screens.BY_KEY[key].phone else {"width": 1280, "height": 800}
             for key in keys}
    (root / screens.MANIFEST).write_text(json.dumps({"version": version, "shots": shots}))


def test_nothing_is_available_before_the_first_capture(static_dir):
    assert screens.available() == {}


def test_a_captured_screen_has_both_themes_at_both_sizes(static_dir):
    capture(static_dir, ["dashboard"])
    pic = screens.available()["dashboard"]
    assert pic.light_src.endswith("/img/screens/dashboard-light.webp?v=202610250900")
    assert pic.light_srcset.endswith("dashboard-light-2x.webp?v=202610250900 2560w")
    assert pic.dark_srcset.startswith(pic.dark_src + " 1280w")
    assert (pic.width, pic.height, pic.phone) == (1280, 800, False)
    assert "Sample data" in str(pic.alt)


def test_a_screen_with_a_file_missing_is_left_out(static_dir):
    capture(static_dir, ["dashboard", "collect"], skip={screens.filename("collect", "dark", 2)})
    assert set(screens.available()) == {"dashboard"}


def test_a_new_capture_gives_new_addresses(static_dir):
    capture(static_dir, ["dashboard"], version="1")
    capture(static_dir, ["dashboard"], version="2")
    assert screens.available()["dashboard"].light_src.endswith("?v=2")


def test_the_home_page_draws_the_sample_until_screens_are_captured(client, static_dir):
    page = client.get("/").content.decode()
    assert "land-preview-body" in page
    assert "land-shot" not in page


def test_the_home_page_shows_the_real_screens(client, static_dir):
    capture(static_dir, [s.key for s in screens.SHOTS])
    page = client.get("/").content.decode()
    assert "land-preview-body" not in page  # the drawn hero is replaced
    # the hero pair, one per tab, one on each "Built for you" card
    pictured = 2 + len(content.LANDING_BLOCKS) + len(content.LANDING_AUDIENCES)
    assert page.count('class="land-shot land-shot-light"') == pictured
    assert page.count('class="land-shot land-shot-dark"') == pictured
    assert page.count('fetchpriority="high"') == 1  # only the hero is fetched straight away
    assert "dashboard-dark.webp" in page and "mpesa-phone-light-2x.webp" in page and "tenant-light" not in page and "see-dark-2x.webp" in page


def test_a_tab_without_its_screen_keeps_its_drawn_sample(client, static_dir):
    capture(static_dir, ["dashboard", "collect"])
    page = client.get("/").content.decode()
    assert "collect-light.webp" in page
    assert "land-mock" in page  # the other three tabs
    assert "land-phone" not in page  # no tenant screen, no phone


def test_capture_needs_the_demo():
    pytest.importorskip("playwright")
    with pytest.raises(CommandError, match="seed_public_demo"):
        call_command("capture_screens")


def test_each_tab_opens_its_page_in_the_demo(client, static_dir):
    capture(static_dir, [s.key for s in screens.SHOTS])
    page = client.get("/").content.decode()
    for path in ("/mpesa/", "/reports/arrears/", "/maintenance/", "/reports/"):
        assert f'name="next" value="{path}"' in page


def test_the_demo_button_goes_only_to_pages_with_a_picture(client, static_dir):
    from accounts import demo
    from accounts.models import Organization
    from accounts.tests.factories import make_org
    org = make_org(name=demo.DEMO_ORG_NAME, owner=demo.demo_user()).organization
    Organization.objects.filter(pk=org.pk).update(is_demo=True)
    assert client.post("/demo/try/", {"next": "/mpesa/"}).headers["Location"] == "/mpesa/"
    client.logout()
    assert client.post("/demo/try/", {"next": "/staff/api-keys/"}).headers["Location"] == "/"
    client.logout()
    assert client.post("/demo/try/", {"next": "https://evil.example/"}).headers["Location"] == "/"


def test_every_page_names_screens_that_exist():
    named = {k for hero, phone, gallery in screens.FOR_SOLUTION.values() for k in (hero, phone, *gallery)}
    assert named | set(screens.FOR_HOW_STEPS) <= set(screens.BY_KEY)
    assert set(screens.FOR_SOLUTION) == {s["slug"] for s in solutions.SOLUTIONS}  # every solution page has pictures
    assert len(screens.FOR_HOW_STEPS) == len(pages.HOW_STEPS)
    assert all(not screens.BY_KEY[hero].phone and screens.BY_KEY[phone].phone
               for hero, phone, _ in screens.FOR_SOLUTION.values())


def test_a_solution_page_shows_its_screens_and_gallery(client, static_dir):
    capture(static_dir, [s.key for s in screens.SHOTS])
    page = client.get("/solutions/mpesa-rent-collection/").content.decode()
    assert "pub-read-head sol-hero" in page and "See it in the app" in page
    for key in ("collect-light.webp", "receipts-light.webp", "inbox-light.webp", "messages-light.webp"):
        assert key in page
    assert str(screens.BY_KEY["inbox"].caption) in page


def test_a_solution_page_without_captures_is_text_only(client, static_dir):
    page = client.get("/solutions/mpesa-rent-collection/").content.decode()
    assert "pub-read-head sol-hero" not in page and "See it in the app" not in page and "land-shot" not in page


def test_the_solutions_list_and_how_it_works_show_screens(client, static_dir):
    capture(static_dir, [s.key for s in screens.SHOTS])
    listing = client.get("/solutions/").content.decode()
    assert listing.count('class="land-card-shot"') == len(solutions.SOLUTIONS)
    how = client.get("/how-it-works/").content.decode()
    assert how.count("pub-timeline-item has-shot") == len(pages.HOW_STEPS)
