"""Tests for the roll-up engine: comparator gating, compound secondary
conditions, floor-block behavior, weighted dimension scores, decomposition-gap
detection."""
from __future__ import annotations

import rubric
from eval import rollup
from eval.types import Measurement


def _m(stage, prop, value, detail=None):
    b = rubric.band(stage, prop)
    return Measurement(artifact=b["artifact"], stage=stage, owner=b["owner"], prop=prop,
                       value=value, kind=b["kind"], rolls_up_to=tuple(b["rolls_up_to"]),
                       unit=b.get("unit", ""), detail=detail or {})


def test_comparator_range():
    assert rollup.gate(_m("script", "scene_count", 11), rubric.band("script", "scene_count"))[0] is True
    assert rollup.gate(_m("script", "scene_count", 3), rubric.band("script", "scene_count"))[0] is False
    assert rollup.gate(_m("script", "scene_count", 20), rubric.band("script", "scene_count"))[0] is False


def test_comparator_gte_lte_eq_true():
    # gte: shot_specificity >= 0.8
    assert rollup.gate(_m("storyboard", "shot_specificity", 0.9), rubric.band("storyboard", "shot_specificity"))[0] is True
    assert rollup.gate(_m("storyboard", "shot_specificity", 0.5), rubric.band("storyboard", "shot_specificity"))[0] is False
    # lte: on_screen_text_density <= 45
    assert rollup.gate(_m("script", "on_screen_text_density", 20), rubric.band("script", "on_screen_text_density"))[0] is True
    assert rollup.gate(_m("script", "on_screen_text_density", 60), rubric.band("script", "on_screen_text_density"))[0] is False
    # eq_true: narrative_arc
    assert rollup.gate(_m("script", "narrative_arc", 1.0), rubric.band("script", "narrative_arc"))[0] is True
    assert rollup.gate(_m("script", "narrative_arc", 0.0), rubric.band("script", "narrative_arc"))[0] is False


def test_comparator_eq_hard():
    # effect_discipline: exactly 1 highlighter-FFD000 (hard)
    assert rollup.gate(_m("storyboard", "effect_discipline", 1.0), rubric.band("storyboard", "effect_discipline"))[0] is True
    assert rollup.gate(_m("storyboard", "effect_discipline", 2.0), rubric.band("storyboard", "effect_discipline"))[0] is False
    assert rollup.gate(_m("storyboard", "effect_discipline", 0.0), rubric.band("storyboard", "effect_discipline"))[0] is False


def test_info_band_not_gated():
    passed, _ = rollup.gate(_m("style", "palette_distance", 12.0), rubric.band("style", "palette_distance"))
    assert passed is None


def test_unmeasured_is_none():
    passed, reason = rollup.gate(_m("script", "scene_count", None), rubric.band("script", "scene_count"))
    assert passed is None


def test_secondary_condition_layout_variety():
    band = rubric.band("storyboard", "layout_variety")
    # entropy in band but one layout dominates -> secondary fails
    m = _m("storyboard", "layout_variety", 0.6, detail={"max_share": 0.75})
    assert rollup.gate(m, band)[0] is False
    # entropy in band AND well distributed -> passes
    m2 = _m("storyboard", "layout_variety", 0.6, detail={"max_share": 0.4})
    assert rollup.gate(m2, band)[0] is True


def test_secondary_condition_transition_needs_match_cut():
    band = rubric.band("storyboard", "transition_character")
    m = _m("storyboard", "transition_character", 0.5, detail={"match_cut_count": 0})
    assert rollup.gate(m, band)[0] is False
    m2 = _m("storyboard", "transition_character", 0.5, detail={"match_cut_count": 2})
    assert rollup.gate(m2, band)[0] is True


def test_floor_failure_blocks():
    measurements = [
        _m("script", "scene_count", 11),                       # G1 pass
        _m("assets", "clearance_rate", 0.9),                   # F hard FAIL
    ]
    sc = rollup.build_scorecard(measurements)
    assert sc["floor"]["passed"] is False
    assert sc["overall"] == "BLOCKED_BY_FLOOR"
    assert any(f["band_id"] == "assets:clearance_rate" for f in sc["floor"]["failures"])


def test_clean_pass_and_quality_score():
    measurements = [
        _m("script", "scene_count", 11),
        _m("script", "narrative_arc", 1.0),
        _m("assets", "clearance_rate", 1.0),
    ]
    sc = rollup.build_scorecard(measurements)
    assert sc["floor"]["passed"] is True
    assert sc["overall"] == "PASS"
    assert sc["quality_score"] == 1.0


def test_quality_score_partial():
    measurements = [
        _m("script", "scene_count", 11),     # G1 pass
        _m("script", "scene_count", 3),      # second G1 contributor fail (re-use prop ok for math)
    ]
    sc = rollup.build_scorecard(measurements)
    # one of two G1 contributors passed -> G1 score 0.5
    assert abs(sc["dimensions"]["G1"]["score"] - 0.5) < 1e-9


def test_decomposition_gap():
    # all gated locals pass, but the holistic anchor (overall_polish) fails
    measurements = [
        _m("script", "scene_count", 11),
        _m("assets", "clearance_rate", 1.0),
        _m("render", "overall_polish", 0.1),   # anchor, gte 0.5 -> fail
    ]
    sc = rollup.build_scorecard(measurements)
    assert sc["anchor"]["present"] is True
    assert sc["anchor"]["passed"] is False
    assert sc["decomposition_gap"] is True


def test_every_measurement_maps_to_a_band_or_is_flagged():
    # a stray prop with no band is recorded ungated with a drift note (not dropped)
    m = Measurement(artifact="x", stage="ghost", owner="?", prop="nope", value=1.0,
                    kind="objective", rolls_up_to=("G1",))
    row = rollup.measurement_to_row(m)
    assert row["passed"] is None and "no rubric band" in row["note"]
