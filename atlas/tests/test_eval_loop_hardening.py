"""Tests for the Phase-2 step-2 loop hardening: the noise-floor gate inside
decide(), and the held-out verifier + human spot-check inside run_loop(). All
offline (injected engine/verifier), so loop LOGIC is deterministic."""
from __future__ import annotations

from pathlib import Path

import rubric
from eval import loop
from eval.types import Measurement


def _m(stage, prop, value, detail=None):
    b = rubric.band(stage, prop)
    return Measurement(artifact=b["artifact"], stage=stage, owner=b["owner"], prop=prop,
                       value=value, kind=b["kind"], rolls_up_to=tuple(b["rolls_up_to"]),
                       unit=b.get("unit", ""), detail=detail or {})


# --- noise-floor gate: JUDGED target --------------------------------------

def test_judged_within_noise_floor_is_rejected():
    # hook_strength is judged (gte 0.55). A 0.5 -> 0.6 move passes the band but is
    # smaller than 2σ of the measured floor (σ≈0.233 -> 2σ≈0.466) -> reject.
    base = [_m("script", "hook_strength", 0.5)]
    cand = [_m("script", "hook_strength", 0.6)]
    v = loop.decide(base, cand, "script:hook_strength",
                    noise_floor={"std": 0.233}, sigma=2.0)
    assert v["target_passes_now"] is True
    assert v["beats_noise_floor"] is False
    assert v["accept"] is False


def test_judged_beating_noise_floor_is_accepted():
    base = [_m("script", "hook_strength", 0.2)]
    cand = [_m("script", "hook_strength", 0.9)]   # Δ=0.7 > 2σ=0.466
    v = loop.decide(base, cand, "script:hook_strength",
                    noise_floor={"std": 0.233}, sigma=2.0)
    assert v["beats_noise_floor"] is True and v["accept"] is True


# --- noise-floor gate: OBJECTIVE target (cross band with margin) -----------

def test_objective_margin_rejects_edge_landing():
    base = [_m("script", "info_density", 7.0)]
    cand = [_m("script", "info_density", 3.95)]   # inside [1.5,4.0] but on the edge
    v = loop.decide(base, cand, "script:info_density", objective_margin=0.2)
    assert v["target_passes_now"] is True         # gate passes...
    assert v["beats_noise_floor"] is False         # ...but not by the margin
    assert v["accept"] is False


def test_objective_margin_accepts_clear_landing():
    base = [_m("script", "info_density", 7.0)]
    cand = [_m("script", "info_density", 3.0)]     # comfortably inside the band
    v = loop.decide(base, cand, "script:info_density", objective_margin=0.2)
    assert v["accept"] is True


def test_decide_backward_compatible_without_gates():
    base = [_m("script", "info_density", 7.0)]
    cand = [_m("script", "info_density", 3.0)]
    v = loop.decide(base, cand, "script:info_density")  # no noise args
    assert v["accept"] is True and v["beats_noise_floor"] is True


# --- run_loop: held-out verifier + spot-check -----------------------------

_TARGET = {"band_id": "script:info_density", "stage": "script", "comparator": "range",
           "measured_value": 7.0, "band_min": 1.5, "band_max": 4.0, "band_target": None}


def _good_remeasure(addendum):
    return [_m("script", "info_density", 3.0), _m("script", "scene_count", 11)]


def _base():
    return [_m("script", "info_density", 7.0), _m("script", "scene_count", 11)]


def test_run_loop_heldout_regression_rejects(tmp_path):
    soft = tmp_path / "COACH_ADDENDUM.md"
    res = loop.run_loop(baseline_measurements=_base(), target=_TARGET,
                        remeasure_fn=_good_remeasure, max_iters=1, soft_path=str(soft),
                        verify_fn=lambda add: {"generalizes": False, "regressions": ["x:y"]})
    assert res["accepted"] is False
    assert not soft.exists()                       # reverted on held-out regression
    assert res["iterations"][0]["verification"]["generalizes"] is False


def test_run_loop_spot_check_veto_rejects(tmp_path):
    soft = tmp_path / "COACH_ADDENDUM.md"
    res = loop.run_loop(baseline_measurements=_base(), target=_TARGET,
                        remeasure_fn=_good_remeasure, max_iters=1, soft_path=str(soft),
                        verify_fn=lambda add: {"generalizes": True},
                        spot_check_fn=lambda proposal, verdict: False)
    assert res["accepted"] is False
    assert not soft.exists()                       # human vetoed -> reverted
    assert res["iterations"][0]["spot_check"] is False


def test_run_loop_all_gates_pass_accepts(tmp_path):
    soft = tmp_path / "COACH_ADDENDUM.md"
    res = loop.run_loop(baseline_measurements=_base(), target=_TARGET,
                        remeasure_fn=_good_remeasure, max_iters=1, soft_path=str(soft),
                        objective_margin=0.2,
                        verify_fn=lambda add: {"generalizes": True},
                        spot_check_fn=lambda proposal, verdict: True)
    assert res["accepted"] is True
    assert soft.read_text()                        # persisted only after all gates
    assert res["iterations"][0]["final_accept"] is True
