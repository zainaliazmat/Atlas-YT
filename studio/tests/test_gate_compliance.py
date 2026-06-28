import json
from studio.gate.types import load_thresholds
from studio.gate import compliance as C

T = load_thresholds()


def test_determinism_fails_on_math_random():
    r = C.check_determinism('<script>var x = Math.random();\nwindow.__timelines["a"]=t;</script>')
    assert r.passed is False and "random" in r.reason.lower()


def test_determinism_passes_clean():
    r = C.check_determinism('<script>window.__timelines["a"] = gsap.timeline();</script>')
    assert r.passed is True


def test_factcheck_block_fails(tmp_path):
    (tmp_path / "factcheck_report.json").write_text(json.dumps({"verdict": "block"}))
    assert C.check_factcheck(tmp_path).passed is False


def test_factcheck_pass(tmp_path):
    (tmp_path / "factcheck_report.json").write_text(json.dumps({"verdict": "pass"}))
    assert C.check_factcheck(tmp_path).passed is True


def test_overflow_blocks_when_inspect_reports_clip(tmp_path):
    fake = lambda pdir: {"overflow": [{"scene": 2, "text": "DARK TRUTH BEHIN"}], "ok": False}
    r = C.check_overflow(tmp_path, inspect_fn=fake)
    assert r.passed is False and "2" in r.reason


def test_overflow_unavailable_is_none(tmp_path):
    r = C.check_overflow(tmp_path, inspect_fn=lambda pdir: None)
    assert r.passed is None
