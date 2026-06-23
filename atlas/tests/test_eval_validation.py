"""Tests for the eval-of-the-eval: every band must discriminate good from bad,
and the CEO-confirm / reference-fit scaffolding must surface the right rows."""
from __future__ import annotations

import rubric
from eval import validation


def test_instrument_is_sound():
    # every gated band passes a known-good and fails a known-bad value
    report = validation.validate_instrument()
    bad = [r["band_id"] for r in report if not r["ok"]]
    assert bad == [], f"non-discriminating bands: {bad}"


def test_assert_instrument_sound_does_not_raise():
    validation.assert_instrument_sound()


def test_every_band_is_covered():
    report = validation.validate_instrument()
    covered = {r["band_id"] for r in report}
    assert covered == set(rubric.bands().keys())


def test_info_band_marked_ungated():
    report = {r["band_id"]: r for r in validation.validate_instrument()}
    assert report["style:palette_distance"]["gated"] is False
    assert report["style:palette_distance"]["ok"] is True


def test_reference_fit_split():
    scorecard = {
        "project_dir": "x",
        "rows": [
            {"band_id": "a:p1", "passed": True, "placeholder": True, "kind": "objective", "measured_value": 1, "note": None},
            {"band_id": "a:p2", "passed": False, "placeholder": True, "kind": "objective", "measured_value": 2, "note": "oob"},
            {"band_id": "a:p3", "passed": False, "placeholder": False, "kind": "objective", "measured_value": 3, "note": "real"},
        ],
    }
    fit = validation.report_reference_fit(scorecard)
    assert fit["passed"] == ["a:p1"]
    assert fit["failed_on_placeholder_bands"] == ["a:p2"]
    assert fit["failed_on_real_bands"] == ["a:p3"]


def test_ceo_confirm_queue():
    scorecard = {
        "decomposition_gap": True,
        "rows": [
            {"band_id": "script:hook_strength", "kind": "judged", "passed": True, "measured_value": 0.6, "note": None, "placeholder": True},
            {"band_id": "a:p2", "kind": "objective", "passed": False, "placeholder": True, "measured_value": 2, "note": "oob"},
            {"band_id": "a:p3", "kind": "objective", "passed": True, "placeholder": False, "measured_value": 3, "note": None},
        ],
    }
    q = validation.ceo_confirm_queue(scorecard)
    assert len(q["judged_to_confirm"]) == 1
    assert len(q["placeholder_band_failures_to_recalibrate"]) == 1
    assert q["decomposition_gap"] is True
