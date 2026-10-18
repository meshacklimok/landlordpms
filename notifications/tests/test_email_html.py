from notifications import email_previews
from notifications.email_html import render_html


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
