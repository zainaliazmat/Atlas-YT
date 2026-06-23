"""Validation harness — the eval-of-the-eval.

A self-improvement loop is only as trustworthy as the instrument it optimizes
against. Before you chase a band, you must show the band DISCRIMINATES: a
known-good value passes and a known-bad value fails. This module asserts exactly
that for every gated band, and provides the scaffolding for the other two checks
the design requires:

  1. known-good passes / known-bad fails   -> validate_instrument()  (automated)
  2. references pass                        -> report_reference_fit() (descriptive
       in phase 1: bands are placeholders, so the gold fixture is NOT expected to
       pass them all yet — once Vera derives real bands from references, the
       references SHOULD pass, and this report is how you'd confirm it)
  3. CEO confirms a sample                  -> ceo_confirm_queue()    (scaffold)

Re-run validate_instrument() whenever a band changes — that is what stops you
from optimizing toward a silently-broken instrument.
"""
from __future__ import annotations

from typing import Optional

import rubric
from eval import rollup
from eval.types import Measurement

# detail payloads that satisfy the compound secondary conditions for the
# "known-good" probe (so a good primary value isn't failed by a missing detail).
_GOOD_DETAIL: dict[str, dict] = {
    "storyboard:layout_variety": {"max_share": 0.5},
    "storyboard:transition_character": {"match_cut_count": 1},
    "script:words_per_scene": {"flags": []},
    "narration:speech_cadence": {"per_scene_flags": []},
}


def _good_bad(comparator: str, band) -> tuple[Optional[float], Optional[float]]:
    """Synthesize an in-band 'good' and out-of-band 'bad' value for a comparator.
    Returns (None, None) for non-gated (info) bands."""
    if comparator == "info":
        return None, None
    if comparator == "range":
        lo, hi = band["min"], band["max"]
        return (lo + hi) / 2.0, hi + 1.0 + abs(hi)
    if comparator == "gte":
        lo = band["min"]
        return lo + 1.0 + abs(lo), lo - 1.0 - abs(lo)
    if comparator == "lte":
        hi = band["max"]
        return hi - 1.0 - abs(hi), hi + 1.0 + abs(hi)
    if comparator == "eq":
        t = band["target"]
        return t, t + 1.0
    if comparator == "eq_true":
        return 1.0, 0.0
    return None, None


def _probe(band_id: str, band, value: float) -> Optional[bool]:
    """Run a synthetic value through the real gate (same code the scorecard uses)."""
    stage, prop = band_id.split(":", 1)
    m = Measurement(artifact=band.get("artifact", "?"), stage=stage,
                    owner=band.get("owner", "?"), prop=prop, value=value,
                    kind=band["kind"], rolls_up_to=tuple(band["rolls_up_to"]),
                    unit=band.get("unit", ""), detail=_GOOD_DETAIL.get(band_id, {}))
    passed, _ = rollup.gate(m, band)
    return passed


def validate_instrument() -> list[dict]:
    """For every gated band, assert known-good passes and known-bad fails.

    Returns one report row per band: {band_id, comparator, gated, good_value,
    good_ok, bad_value, bad_ok, ok}. `ok` is True when the band discriminates
    (good passed AND bad failed), or when the band is info-only (not gated).
    """
    report = []
    for band_id, band in rubric.bands().items():
        comparator = band["comparator"]
        good, bad = _good_bad(comparator, band)
        if good is None and bad is None:  # info-only band — nothing to gate
            report.append({"band_id": band_id, "comparator": comparator,
                           "gated": False, "ok": True,
                           "good_value": None, "good_ok": None,
                           "bad_value": None, "bad_ok": None})
            continue
        good_pass = _probe(band_id, band, good)
        bad_pass = _probe(band_id, band, bad)
        ok = (good_pass is True) and (bad_pass is False)
        report.append({"band_id": band_id, "comparator": comparator, "gated": True,
                       "good_value": good, "good_ok": good_pass,
                       "bad_value": bad, "bad_ok": bad_pass, "ok": ok})
    return report


def assert_instrument_sound() -> None:
    """Raise AssertionError listing any band that fails to discriminate."""
    bad = [r["band_id"] for r in validate_instrument() if not r["ok"]]
    if bad:
        raise AssertionError(f"rubric bands that do not discriminate good/bad: {bad}")


def report_reference_fit(scorecard: dict) -> dict:
    """Descriptive 'do the references pass?' report over a real scorecard.

    In phase 1 the bands are PLACEHOLDERS, so the gold fixture is expected to
    miss several — that is not an instrument failure, it is the signal that the
    bands need reference-derived calibration (step 1). Once real bands are set,
    re-run this and the references should pass; a reference that still fails then
    is a genuine instrument problem.
    """
    rows = scorecard.get("rows", [])
    passed = [r["band_id"] for r in rows if r["passed"] is True]
    failed_placeholder = [r["band_id"] for r in rows
                          if r["passed"] is False and r.get("placeholder")]
    failed_real = [r["band_id"] for r in rows
                   if r["passed"] is False and not r.get("placeholder")]
    return {
        "reference": scorecard.get("project_dir"),
        "passed": passed,
        "failed_on_placeholder_bands": failed_placeholder,
        "failed_on_real_bands": failed_real,
        "note": ("Placeholder-band failures are EXPECTED pre-calibration — they flag "
                 "bands to derive from references (step 1), not instrument bugs. "
                 "Real-band failures on a reference are genuine and worth a CEO look."),
    }


def ceo_confirm_queue(scorecard: dict) -> dict:
    """What a human should eyeball before trusting this scorecard:
      - judged properties (LLM-scored, noisy — confirm a sample),
      - placeholder-band failures (the band may be wrong, not the video),
      - any decomposition gap (locals pass but the holistic anchor failed)."""
    rows = scorecard.get("rows", [])
    judged = [{"band_id": r["band_id"], "value": r["measured_value"], "passed": r["passed"]}
              for r in rows if r["kind"] == "judged"]
    placeholder_fails = [{"band_id": r["band_id"], "value": r["measured_value"], "note": r["note"]}
                         for r in rows if r["passed"] is False and r.get("placeholder")]
    return {
        "judged_to_confirm": judged,
        "placeholder_band_failures_to_recalibrate": placeholder_fails,
        "decomposition_gap": scorecard.get("decomposition_gap", False),
        "anchored": any(j for j in judged),  # whether judged props exist to anchor
    }
