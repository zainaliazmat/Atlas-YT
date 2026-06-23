"""Tests for the minimal improvement loop — with emphasis on the write boundary
(the privilege asymmetry). The improver must be PHYSICALLY unable to write the
rubric, contracts, or spine; it may write only soft-tier persona/prompt files.

The re-measure is injected (a fake engine), so loop LOGIC is tested offline and
deterministically; the real Marlow re-measure is exercised in live-QA."""
from __future__ import annotations

from pathlib import Path

import pytest

import rubric
from eval import loop
from eval.types import Measurement


ATLAS = Path(__file__).resolve().parents[1]


# --- write boundary: the core safety property -----------------------------

def test_cannot_write_rubric():
    before = (ATLAS / "rubric" / "rubric.json").read_text()
    with pytest.raises(loop.WriteBoundaryError):
        loop.apply_soft_change(ATLAS / "rubric" / "rubric.json", "{}")
    assert (ATLAS / "rubric" / "rubric.json").read_text() == before  # untouched


def test_cannot_write_contracts_or_spine():
    for p in [ATLAS / "contracts" / "script.schema.json",
              ATLAS / "pipeline.py",
              ATLAS / "registry.py",
              ATLAS / "adapters" / "loader.py"]:
        with pytest.raises(loop.WriteBoundaryError):
            loop.apply_soft_change(p, "x")


def test_non_soft_tier_refused(tmp_path):
    with pytest.raises(loop.WriteBoundaryError):
        loop.apply_soft_change(tmp_path / "random.py", "x")
    with pytest.raises(loop.WriteBoundaryError):
        loop.apply_soft_change(tmp_path / "notes.md", "x")  # .md but no soft token


def test_soft_tier_write_allowed(tmp_path):
    p = loop.apply_soft_change(tmp_path / "COACH_ADDENDUM.md", "hello")
    assert Path(p).read_text() == "hello"
    p2 = loop.apply_soft_change(tmp_path / "SOUL.md", "persona")
    assert Path(p2).read_text() == "persona"


def test_can_write_rubric_selfcheck_is_true():
    # can_write_rubric() == True means "rubric write is BLOCKED"
    assert loop.can_write_rubric() is True
    # and the rubric module genuinely exposes no writer
    assert not any(hasattr(rubric, n) for n in ("save", "write", "dump", "set_band"))


# --- proposal + decide logic ----------------------------------------------

def _m(stage, prop, value, detail=None):
    b = rubric.band(stage, prop)
    return Measurement(artifact=b["artifact"], stage=stage, owner=b["owner"], prop=prop,
                       value=value, kind=b["kind"], rolls_up_to=tuple(b["rolls_up_to"]),
                       unit=b.get("unit", ""), detail=detail or {})


def test_propose_fix_direction_lower():
    target = {"band_id": "script:info_density", "comparator": "range",
              "measured_value": 7.0, "band_min": 1.5, "band_max": 4.0, "band_target": None}
    prop = loop.propose_fix(target)
    assert "LOWER" in prop["direction"]
    assert "script:info_density" in prop["addendum"]


def test_decide_accept_on_improvement():
    base = [_m("script", "info_density", 7.0), _m("script", "scene_count", 11)]
    cand = [_m("script", "info_density", 3.0), _m("script", "scene_count", 11)]
    d = loop.decide(base, cand, "script:info_density")
    assert d["accept"] is True
    assert d["target_after"] == 3.0


def test_decide_reject_when_target_still_out_of_band():
    base = [_m("script", "info_density", 7.0)]
    cand = [_m("script", "info_density", 6.0)]  # closer but still > 4.0
    d = loop.decide(base, cand, "script:info_density")
    assert d["accept"] is False


def test_decide_reject_on_regression():
    base = [_m("script", "info_density", 7.0), _m("script", "scene_count", 11)]
    cand = [_m("script", "info_density", 3.0), _m("script", "scene_count", 30)]  # scene_count regressed
    d = loop.decide(base, cand, "script:info_density")
    assert d["accept"] is False
    assert "script:scene_count" in d["regressions"]


# --- full loop with injected fake re-measure ------------------------------

def test_run_loop_accept_with_fake_engine(tmp_path):
    base = [_m("script", "info_density", 7.0), _m("script", "scene_count", 11)]
    target = {"band_id": "script:info_density", "stage": "script", "comparator": "range",
              "measured_value": 7.0, "band_min": 1.5, "band_max": 4.0, "band_target": None}
    # fake engine: the soft tweak "works" -> info_density lands in band
    def remeasure(addendum):
        assert "script:info_density" in addendum  # the soft text was passed in
        return [_m("script", "info_density", 3.0), _m("script", "scene_count", 11)]
    soft = tmp_path / "COACH_ADDENDUM.md"
    res = loop.run_loop(baseline_measurements=base, target=target,
                        remeasure_fn=remeasure, max_iters=1, soft_path=str(soft))
    assert res["accepted"] is True
    assert res["rubric_write_blocked"] is True
    assert soft.read_text()  # accepted -> soft change persisted


def test_run_loop_reject_reverts_soft_change(tmp_path):
    base = [_m("script", "info_density", 7.0)]
    target = {"band_id": "script:info_density", "stage": "script", "comparator": "range",
              "measured_value": 7.0, "band_min": 1.5, "band_max": 4.0, "band_target": None}
    def remeasure(addendum):
        return [_m("script", "info_density", 6.5)]  # no good
    soft = tmp_path / "COACH_ADDENDUM.md"
    res = loop.run_loop(baseline_measurements=base, target=target,
                        remeasure_fn=remeasure, max_iters=1, soft_path=str(soft))
    assert res["accepted"] is False
    assert not soft.exists()  # rejected -> soft change reverted (reversible)
