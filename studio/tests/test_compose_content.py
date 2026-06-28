from studio.compose import _content


def test_multiline_text_preserves_all_lines():
    html = _content.render_on_screen_text("DARK TRUTH / BEHIND THE / SOCIAL MEDIA")
    assert html.count("lead-line") == 3
    assert "DARK" in html and "BEHIND" in html and "SOCIAL" in html
    assert '<span class="em">MEDIA</span>' in html  # last word of last line emphasized


def test_single_line_emphasizes_last_word():
    html = _content.render_on_screen_text("IT'S NOT A BUG")
    assert html.count("lead-line") == 1
    assert '<span class="em">BUG</span>' in html


def test_empty_is_nbsp():
    assert "&nbsp;" in _content.render_on_screen_text("")


def test_attributed_quote_renders_as_quote_card_with_byline():
    scene = {"claims": [
        {"claim_id": "c1", "text": '"Sprinkling behavioral cocaine over your interface." — Aza Raskin',
         "source_ref": "F1"}]}
    html_ = _content.render_claims(scene)
    assert "quote-card" in html_
    assert "behavioral cocaine" in html_.lower()
    assert "Aza Raskin" in html_          # byline attribution present
    assert "byline" in html_


def test_plain_claim_renders_text_visibly():
    scene = {"claims": [{"claim_id": "c1", "text": "141 minutes a day", "source_ref": "F2"}]}
    html_ = _content.render_claims(scene)
    assert "claim" in html_ and "141 minutes a day" in html_


def test_no_claims_is_empty():
    assert _content.render_claims({"claims": []}) == ""
