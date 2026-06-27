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
