import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from growth import articles
from growth.models import Article

pytestmark = pytest.mark.django_db


def make(slug="a-test", **kw):
    data = {"title": "A test", "summary": "Short.", "body": "## One\n\nText.", "is_published": True,
            "published_at": timezone.now() - datetime.timedelta(hours=1)}
    data.update(kw)
    return Article.objects.create(slug=slug, **data)


def test_the_nine_starting_articles_are_loaded():
    assert articles.published().count() >= 9
    assert Article.objects.filter(slug="paybill-or-till", is_published=True).exists()


def test_render_escapes_html_and_keeps_only_safe_links():
    html, toc = articles.render('## Hi <b>\n\n<script>alert(1)</script> **bold** [ok](https://a.co/x?a=1&b=2) '
                                '[bad](javascript:alert(1))\n\n- one\n- two\n\n1. first\n2. second\n\n> note')
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<strong>bold</strong>" in html
    assert 'href="https://a.co/x?a=1&amp;b=2" target="_blank" rel="noopener"' in html
    assert "javascript:" not in html.replace("[bad](javascript:alert(1))", "")
    assert "<li>one</li>" in html and "<ol><li>first</li>" in html and 'class="pub-note"' in html
    assert toc == [("hi-b", "Hi <b>")] or toc[0][0].startswith("hi")


def test_render_makes_unique_anchors_for_repeated_headings():
    _html, toc = articles.render("## Same\n\nx\n\n## Same\n\ny")
    assert [a for a, _t in toc] == ["same", "same-2"]


def test_a_draft_or_future_article_is_not_public(client):
    make("draft-one", is_published=False)
    make("later-one", published_at=timezone.now() + datetime.timedelta(days=2))
    assert client.get(reverse("growth:article", args=["draft-one"])).status_code == 404
    assert client.get(reverse("growth:article", args=["later-one"])).status_code == 404
    listing = client.get(reverse("growth:articles")).content.decode()
    assert "draft-one" not in listing and "later-one" not in listing
    sitemap = client.get(reverse("growth:sitemap")).content.decode()
    assert "draft-one" not in sitemap and "later-one" not in sitemap


def test_a_published_article_is_read_listed_and_in_the_sitemap_and_ai_text(client):
    make("fresh-one", title="Fresh one", body="## Part\n\nHello **world**.")
    page = client.get(reverse("growth:article", args=["fresh-one"])).content.decode()
    assert "Fresh one" in page and "<strong>world</strong>" in page and "datePublished" in page
    assert "fresh-one" in client.get(reverse("growth:articles")).content.decode()
    sitemap = client.get(reverse("growth:sitemap")).content.decode()
    assert "/articles/fresh-one/" in sitemap and "<lastmod>" in sitemap
    assert "Fresh one" in client.get("/llms.txt").content.decode()
    assert "Hello world." in client.get("/llms-full.txt").content.decode()


def test_the_list_can_be_filtered_by_topic(client):
    make("util-one", title="Util piece", category=Article.Category.UTILITIES)
    body = client.get(reverse("growth:articles") + "?category=UTILITIES").content.decode()
    assert "Util piece" in body and "pub-card-title\">Seven habits" not in body
    assert client.get(reverse("growth:articles") + "?category=nonsense").status_code == 200


def test_the_list_shows_the_featured_piece_first_and_can_be_searched(client):
    make("big-one", title="Big one", featured=True)
    make("water-one", title="Water piece", body="## A\n\nBorehole tariffs.")
    page = client.get(reverse("growth:articles")).content.decode()
    assert "pub-feature" in page and "Big one" in page
    found = client.get(reverse("growth:articles") + "?q=borehole").content.decode()
    assert "Water piece" in found and "pub-feature" not in found and "1 article found" in found
    assert "Nothing matches" in client.get(reverse("growth:articles") + "?q=zzzqqq").content.decode()


def test_the_list_page_lists_every_article_in_its_structured_data(client):
    make("data-one", title="Data one")
    page = client.get(reverse("growth:articles")).content.decode()
    assert "ItemList" in page and "/articles/data-one/" in page
