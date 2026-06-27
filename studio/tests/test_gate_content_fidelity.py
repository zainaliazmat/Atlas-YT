# studio/tests/test_gate_content_fidelity.py
from studio.gate.types import load_thresholds
from studio.gate import parse, dimensions as D

T = load_thresholds()


def test_is_attributed_quote():
    assert parse.is_attributed_quote('"Behavioral cocaine." — Aza Raskin')
    assert not parse.is_attributed_quote("141 minutes a day")


def test_missing_attributed_quote_forces_below_floor():
    # script scene 5 has the quote card; the composition dropped it.
    ev = {
        "index_html": '<section id="s5" class="scene clip"><div class="lead">THEY ADMIT IT</div></section>',
        "script": {"scenes": [
            {"scene_no": 5, "on_screen_text": '"Behavioral cocaine." — Aza Raskin', "claims": []}]},
        "scenes": [{"scene_no": 5, "on_screen_text": '"Behavioral cocaine." — Aza Raskin'}],
    }
    r = D.score_content_fidelity(ev, T)
    assert r.passed is False
    assert any("quote" in d.lower() and "raskin" in d.lower() for d in r.diagnostics)


def test_all_content_present_passes():
    ev = {
        "index_html": '<section id="s1" class="scene clip"><div class="lead">141 MINUTES A DAY</div></section>',
        "script": {"scenes": [{"scene_no": 1, "on_screen_text": "141 minutes a day", "claims": []}]},
        "scenes": [{"scene_no": 1, "on_screen_text": "141 minutes a day"}],
    }
    r = D.score_content_fidelity(ev, T)
    assert r.passed is True


def test_no_script_is_none():
    r = D.score_content_fidelity({"index_html": "<section id='s1'></section>", "script": {"scenes": []}}, T)
    assert r.score is None and r.passed is None
