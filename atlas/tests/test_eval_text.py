"""Tests for the TEXT/STRUCTURAL analyzer (atlas/eval/analyzers/text.py).

Runs against the gold fixture project and an empty tmp dir (graceful degradation).
No LLM, no ffmpeg.
"""
from __future__ import annotations

import pathlib

import pytest

import rubric
from eval.analyzers import text
from eval.types import EvalContext

FIX = (pathlib.Path(__file__).resolve().parents[1] / "projects"
       / "gpt-4o-vs-claude-vs-gemini-vs-deepseek-comparison--20260621-013345-67a3")

# Every (stage, prop) this analyzer owns.
OWNED = [
    ("script", "scene_count"), ("script", "runtime_fit"), ("script", "words_per_scene"),
    ("script", "one_point_adherence"), ("script", "claim_support_ratio"),
    ("script", "info_density"), ("script", "narrative_arc"), ("script", "cta_quality"),
    ("script", "on_screen_text_density"),
    ("style", "signature_present"), ("style", "type_in_system"),
    ("style", "motion_budget_sane"), ("style", "palette_distance"),
    ("storyboard", "layout_variety"), ("storyboard", "effect_discipline"),
    ("storyboard", "transition_character"), ("storyboard", "shot_specificity"),
    ("storyboard", "signature_beat_placement"),
    ("assets", "placeholder_rate"), ("assets", "clearance_rate"),
    ("assets", "relevance_score"), ("assets", "min_resolution"),
    ("narration", "speech_cadence"), ("narration", "pause_structure"),
    ("narration", "scene_timing_fit"), ("narration", "total_duration_fit"),
    ("render", "caption_sync"),
]


@pytest.fixture(scope="module")
def measurements():
    assert FIX.is_dir(), f"fixture missing: {FIX}"
    return text.analyze(EvalContext(FIX))


def _by(measurements):
    return {(m.stage, m.prop): m for m in measurements}


# --- (a) returns Measurements for ALL owned props, no exceptions ----------

def test_returns_all_owned_props(measurements):
    got = {(m.stage, m.prop) for m in measurements}
    missing = set(OWNED) - got
    assert not missing, f"missing measurements: {sorted(missing)}"


# --- (b) known fixture truths ---------------------------------------------

def test_fixture_known_truths(measurements):
    m = _by(measurements)
    assert m[("script", "scene_count")].value == 11
    assert m[("style", "signature_present")].value == 1.0
    assert m[("storyboard", "effect_discipline")].value == 1.0
    assert m[("storyboard", "signature_beat_placement")].value == 1.0
    assert m[("script", "narrative_arc")].value == 1.0

    pr = m[("assets", "placeholder_rate")].value
    assert pr is not None and 0.0 <= pr <= 1.0

    wpm = m[("narration", "speech_cadence")].value
    assert wpm is not None and 80 <= wpm <= 220

    lv = m[("storyboard", "layout_variety")].value
    assert lv is not None and 0.0 <= lv <= 1.0

    cap = m[("render", "caption_sync")]
    assert cap.value is None
    assert cap.detail.get("note") or cap.error


# --- (c) every Measurement maps to a real band with matching metadata -----

def test_measurements_map_to_rubric_bands(measurements):
    for me in measurements:
        b = rubric.band(me.stage, me.prop)
        assert b is not None, f"no band for {me.stage}:{me.prop}"
        assert me.kind == b.get("kind")
        assert me.rolls_up_to == tuple(b.get("rolls_up_to", ()))
        assert me.unit == b.get("unit", "")
        assert me.owner == b.get("owner", "")


# --- ratio/bool sanity -----------------------------------------------------

def test_ratios_and_bools_sane(measurements):
    m = _by(measurements)
    for key in [("script", "one_point_adherence"), ("script", "claim_support_ratio"),
                ("storyboard", "transition_character"), ("storyboard", "shot_specificity"),
                ("assets", "placeholder_rate"), ("assets", "clearance_rate"),
                ("assets", "relevance_score"), ("style", "type_in_system")]:
        v = m[key].value
        if v is not None:
            assert 0.0 <= v <= 1.0, f"{key} out of [0,1]: {v}"
    for key in [("script", "narrative_arc"), ("script", "cta_quality"),
                ("style", "signature_present"), ("style", "motion_budget_sane"),
                ("storyboard", "signature_beat_placement")]:
        assert m[key].value in (0.0, 1.0)


# --- (d) graceful degradation: empty dir -> value=None+error, no exception -

def test_graceful_degradation_empty_dir(tmp_path):
    ms = text.analyze(EvalContext(tmp_path))
    assert ms, "should still return measurements (with errors) on empty dir"
    got = {(m.stage, m.prop) for m in ms}
    # all owned props should be present as graceful misses
    assert set(OWNED) <= got, f"missing on empty: {sorted(set(OWNED) - got)}"
    for me in ms:
        assert me.value is None, f"{me.stage}:{me.prop} should be None on empty dir"
        assert me.error, f"{me.stage}:{me.prop} should carry an error on empty dir"
        # still maps to a real band
        assert rubric.band(me.stage, me.prop) is not None
