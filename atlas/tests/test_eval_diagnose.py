"""Tests for the Diagnostician: credit assignment, coordination detection,
escalation, and single-owner soft-tier target selection."""
from __future__ import annotations

from eval import diagnose


def _row(band_id, stage, rolls, passed, hard=False, placeholder=True,
         comparator="range", value=99, lo=1, hi=2, target=None):
    return {"band_id": band_id, "stage": stage, "rolls_up_to": rolls,
            "passed": passed, "hard": hard, "placeholder": placeholder,
            "comparator": comparator, "measured_value": value,
            "band_min": lo, "band_max": hi, "band_target": target, "note": "oob"}


def test_clean_single_owner_target():
    sc = {"rows": [
        _row("script:info_density", "script", ["G2"], False, value=7.0, lo=1.5, hi=4.0),
        _row("script:scene_count", "script", ["G1"], True),
    ], "floor": {"passed": True}, "decomposition_gap": False}
    dg = diagnose.diagnose(sc)
    t = dg["primary_target"]
    assert t is not None
    assert t["band_id"] == "script:info_density"
    assert t["owner"] == "Marlow"
    assert dg["coordination_needed"] == []


def test_prefers_highest_weight_dimension():
    sc = {"rows": [
        _row("narration:speech_cadence", "narration", ["G1"], False),  # G1 w0.20
        _row("script:info_density", "script", ["G2"], False),          # G2 w0.25
    ], "floor": {"passed": True}, "decomposition_gap": False}
    t = diagnose.diagnose(sc)["primary_target"]
    assert t["band_id"] == "script:info_density"  # G2 outranks G1


def test_multi_stage_coordination_flagged_and_excluded():
    # G1 fails from TWO stages -> coordination needed -> not a clean target
    sc = {"rows": [
        _row("narration:speech_cadence", "narration", ["G1"], False),
        _row("script:words_per_scene", "script", ["G1"], False),
    ], "floor": {"passed": True}, "decomposition_gap": False}
    dg = diagnose.diagnose(sc)
    assert "G1" in dg["coordination_needed"]
    assert dg["primary_target"] is None  # both rolled to the contested G1


def test_hard_floor_not_a_soft_target():
    sc = {"rows": [
        _row("assets:clearance_rate", "assets", ["F"], False, hard=True, placeholder=False),
    ], "floor": {"passed": False, "failures": [{"band_id": "assets:clearance_rate"}]},
        "decomposition_gap": False}
    dg = diagnose.diagnose(sc)
    assert dg["primary_target"] is None
    assert any("floor_failure" in e for e in dg["escalate_to_ceo"])


def test_decomposition_gap_escalates():
    sc = {"rows": [], "floor": {"passed": True}, "decomposition_gap": True}
    dg = diagnose.diagnose(sc)
    assert any("decomposition_gap" in e for e in dg["escalate_to_ceo"])
