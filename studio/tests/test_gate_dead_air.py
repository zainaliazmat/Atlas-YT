# studio/tests/test_gate_dead_air.py
from studio.gate.types import load_thresholds
from studio.gate import dimensions as D

T = load_thresholds()


def test_dead_air_flags_named_scenes():
    ev = {"motion": {"any_flag": True, "scenes": [
        {"scene_no": 1, "flags": [], "status": "PASS"},
        {"scene_no": 3, "flags": ["trailing_static"], "status": "FLAG"},
        {"scene_no": 6, "flags": ["trailing_static"], "status": "FLAG"},
        {"scene_no": 8, "flags": ["silent_gap"], "status": "FLAG"}]}}
    r = D.score_dead_air(ev, T)
    assert r.passed is False
    joined = " ".join(r.diagnostics)
    assert "3" in joined and "6" in joined and "8" in joined


def test_dead_air_clean_passes_full():
    ev = {"motion": {"any_flag": False, "scenes": [
        {"scene_no": i, "flags": [], "status": "PASS"} for i in range(1, 6)]}}
    r = D.score_dead_air(ev, T)
    assert r.passed is True and r.score == 5.0


def test_dead_air_no_scenes_is_none():
    r = D.score_dead_air({"motion": {"scenes": []}}, T)
    assert r.score is None and r.passed is None
