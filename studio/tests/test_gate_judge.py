# studio/tests/test_gate_judge.py
from studio.gate.types import load_thresholds
from studio.gate import judge

T = load_thresholds()


def test_polish_high_rate_passes():
    ev = {"polish_vs_reference": {"rate": 0.9, "n": 5}}
    r = judge.score_polish(ev, T)
    assert r.passed is True and r.score >= T["dimensions"]["polish_vs_reference"]["floor"]


def test_polish_low_rate_fails_with_reason():
    ev = {"polish_vs_reference": {"rate": 0.0, "n": 5}}
    r = judge.score_polish(ev, T)
    assert r.passed is False and any("reference" in d.lower() for d in r.diagnostics)


def test_polish_too_few_votes_is_none_not_block():
    ev = {"polish_vs_reference": {"rate": 0.0, "n": 1}}   # below min_votes=3
    r = judge.score_polish(ev, T)
    assert r.score is None and r.passed is None


def test_polish_unmeasured_is_none():
    r = judge.score_polish({"polish_vs_reference": {"rate": None, "n": 0}}, T)
    assert r.score is None and r.passed is None
