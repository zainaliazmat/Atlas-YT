"""Tests for the video/motion analyzer (eval/analyzers/video.py).

Gold fixture: a finished comparison project (1920x1080@30fps, ~72.5s, 11 scenes,
auto_gate PASS, zero integrity/contrast flags). cv2/video tests are skipped if
the fixture video is absent.
"""
from __future__ import annotations

import math
import pathlib

import pytest

import rubric
from eval.types import EvalContext, Measurement
from eval.analyzers import video

FIX = (pathlib.Path(__file__).resolve().parents[1]
       / "projects"
       / "gpt-4o-vs-claude-vs-gemini-vs-deepseek-comparison--20260621-013345-67a3")

HAS_VIDEO = (FIX / "video.mp4").is_file()
needs_video = pytest.mark.skipif(not HAS_VIDEO,
                                 reason="gold fixture video.mp4 not present")

OWNED = [
    ("compose", "motion_energy"),
    ("compose", "cut_rhythm"),
    ("compose", "av_sync"),
    ("compose", "layout_integrity"),
    ("compose", "auto_gate_first_pass"),
    ("render", "final_runtime"),
]


def _by_prop(ms: list[Measurement]) -> dict[str, Measurement]:
    return {m.prop: m for m in ms}


@pytest.fixture(scope="module")
def gold_measurements() -> list[Measurement]:
    """analyze() the gold fixture ONCE (motion_energy decodes the whole clip,
    so we share it across the value tests instead of re-decoding per test)."""
    return video.analyze(EvalContext(FIX))


@pytest.fixture(scope="module")
def gold(gold_measurements) -> dict[str, Measurement]:
    return _by_prop(gold_measurements)


# --- (a) returns all 6, no exceptions --------------------------------------

@needs_video
def test_returns_all_six_props_no_exception(gold_measurements):
    ms = gold_measurements
    assert isinstance(ms, list)
    props = {m.prop for m in ms}
    assert props == {p for _, p in OWNED}
    assert len(ms) == 6


# --- (b) real values are plausible -----------------------------------------

@needs_video
def test_motion_energy_value_and_variance(gold):
    m = gold["motion_energy"]
    assert m.error is None, m.error
    assert m.value is not None
    assert math.isfinite(m.value)
    assert m.value > 0.0
    assert "variance" in m.detail
    assert math.isfinite(float(m.detail["variance"]))
    assert m.detail["n_diffs"] > 0


@needs_video
def test_cut_rhythm_median_plausible(gold):
    m = gold["cut_rhythm"]
    assert m.error is None, m.error
    # scenes are ~5-7s each (one outlier at ~12.4s) -> median ~5-7
    assert 5.0 <= m.value <= 7.0
    assert m.detail["n"] == 11
    assert "flags" in m.detail
    # scene 6 is ~12.4s -> should be flagged as > 12s
    assert 6 in m.detail["flags"]


@needs_video
def test_av_sync_fraction_in_range(gold):
    m = gold["av_sync"]
    assert m.error is None, m.error
    assert 0.0 <= m.value <= 1.0
    assert "max_drift_sec" in m.detail
    assert m.detail["max_drift_sec"] >= 0.0


@needs_video
def test_auto_gate_first_pass_is_one(gold):
    m = gold["auto_gate_first_pass"]
    assert m.error is None, m.error
    assert m.value == 1.0
    assert m.detail["auto_gate"] == "PASS"


@needs_video
def test_layout_integrity_is_zero(gold):
    m = gold["layout_integrity"]
    assert m.error is None, m.error
    assert m.value == 0.0


@needs_video
def test_final_runtime_about_72s(gold):
    m = gold["final_runtime"]
    assert m.error is None, m.error
    assert 70.0 < m.value < 75.0


# --- (c) every measurement maps to a real band w/ matching metadata --------

@needs_video
def test_measurements_match_rubric_bands(gold_measurements):
    ms = gold_measurements
    for m in ms:
        b = rubric.band(m.stage, m.prop)
        assert b is not None, f"no rubric band for {m.stage}:{m.prop}"
        assert m.kind == b["kind"]
        assert m.rolls_up_to == tuple(b["rolls_up_to"])
        assert m.unit == b["unit"]
        assert m.owner == b["owner"]
        assert m.artifact == b["artifact"]


# --- (d) graceful degradation: empty dir -> all None + error ----------------

def test_graceful_degradation_empty_dir(tmp_path):
    ms = video.analyze(EvalContext(tmp_path))
    assert len(ms) == 6
    props = {m.prop for m in ms}
    assert props == {p for _, p in OWNED}
    for m in ms:
        assert m.value is None, f"{m.prop} should be None on empty dir"
        assert m.error, f"{m.prop} should carry an error on empty dir"
    # still maps to real bands even when degraded
    for m in ms:
        assert rubric.band(m.stage, m.prop) is not None
