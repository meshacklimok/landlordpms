import pytest

from notifications import email_previews
from notifications.email_html import render_html

pytestmark = pytest.mark.django_db


def test_every_sample_renders_with_the_brand_and_no_raw_markup():
    for key in email_previews.keys():
        html = email_previews.render(key)
        assert "NyumbaPMS" in html and "#146c4a" in html


def test_a_code_alone_becomes_a_code_box_and_a_lone_address_a_button():
    html = render_html("NyumbaPMS", "Your code:\n\n482913\n\nhttps://example.com/go", button_label="Go")
    assert "letter-spacing:8px" in html and ">Go</a>" in html


def test_label_lines_become_a_table_and_text_is_escaped():
    html = render_html("A & B", "Hi <b>\n\nTotal: KES 5\nDue: soon")
    assert "A &amp; B" in html and "Hi &lt;b&gt;" in html and "<table" in html and "KES 5" in html


def test_the_preheader_skips_the_greeting_and_links():
    from notifications.email_html import preheader_for

    assert preheader_for("Dear Grace,\n\nRent of KES 500 is due on 5 Nov.\n\nhttps://x.co/p") == "Rent of KES 500 is due on 5 Nov."
    html = render_html("NyumbaPMS", "Dear Grace,\n\nYour code is ready.")
    assert "display:none" in html and "Your code is ready." in html


def test_a_pay_line_ending_in_an_address_becomes_a_pay_now_button():
    html = render_html("Sample", "Hi\n\nPay with account number B4.\nhttps://x.co/p/1", button_label="Pay now")
    assert ">Pay now</a>" in html and "Pay with account number B4." in html
