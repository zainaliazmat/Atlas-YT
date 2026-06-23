"""Roll-up engine: turn raw Measurements into banded rows + a global scorecard.

This is where the CEO-owned rubric DECIDES pass/fail (the analyzers only
MEASURE). The flow:

  Measurement (raw value)  ->  gate against rubric band  ->  canonical row
  rows  ->  roll up into the 6 weighted global dimensions + the hard floor (F)
        ->  a single quality_score and an overall verdict.

Design notes:
  * The gate is comparator-driven (range/gte/lte/eq/eq_true/info) plus a small,
    explicit registry of compound secondary conditions taken verbatim from the
    rubric-decomposition spec (e.g. layout_variety also needs max_share<=0.60).
  * The floor F is a hard pass/fail, NOT a weighted term: any hard floor failure
    means the run is BLOCKED_BY_FLOOR (it still gets a quality_score for
    diagnostics, but the verdict is the block — consistent with how a fact-check
    `block` can never be averaged away).
  * overall_polish is the holistic ANCHOR, not a weighted dim. If every local
    property passes but the anchor fails, that is flagged as a decomposition gap
    (the rubric is missing a term) — a CEO-owned signal, never an auto-fix.
"""
from __future__ import annotations

import math
from typing import Callable, Optional

import rubric
from eval.types import Measurement

# Compound secondary conditions from rubric-decomposition.md §3. Each maps a
# band id to a predicate over the Measurement's detail dict; the property passes
# only if BOTH the primary comparator AND this secondary hold. Kept explicit and
# small so the rubric's intent is faithfully encoded without hiding logic.
_SECONDARY: dict[str, Callable[[Measurement], bool]] = {
    # layout entropy in band AND no single layout dominates (>60%)
    "storyboard:layout_variety": lambda m: float(m.detail.get("max_share", 1.0)) <= 0.60,
    # hard-cut fraction in band AND at least one match-cut present
    "storyboard:transition_character": lambda m: int(m.detail.get("match_cut_count", 0)) >= 1,
    # mean words/scene in band AND no individual scene wildly off (>45 or <8)
    "script:words_per_scene": lambda m: not m.detail.get("flags"),
    # overall cadence in band AND no per-scene cadence outlier
    "narration:speech_cadence": lambda m: not m.detail.get("per_scene_flags"),
}

_SECONDARY_REASON: dict[str, str] = {
    "storyboard:layout_variety": "a single layout exceeds 60% of scenes",
    "storyboard:transition_character": "no match-cut present",
    "script:words_per_scene": "a scene's word count is an outlier (>45 or <8)",
    "narration:speech_cadence": "a scene's cadence is an outlier (>185 or <110 wpm)",
}


def _primary_pass(comparator: str, value: float, band) -> Optional[bool]:
    """Apply the band's comparator to value. None = not gated (info-only)."""
    if comparator == "info":
        return None
    if comparator == "range":
        return band["min"] <= value <= band["max"]
    if comparator == "gte":
        return value >= band["min"]
    if comparator == "lte":
        return value <= band["max"]
    if comparator == "eq":
        return math.isclose(value, band["target"], abs_tol=1e-6)
    if comparator == "eq_true":
        return value >= 0.5
    return None


def gate(measurement: Measurement, band) -> tuple[Optional[bool], Optional[str]]:
    """Return (passed, reason). passed is None when the value is unmeasured or the
    band is info-only. reason is a short human note (failure cause / skip cause)."""
    if measurement.value is None:
        return None, measurement.error or "unmeasured"
    band_id = f"{measurement.stage}:{measurement.prop}"
    comparator = band["comparator"]
    primary = _primary_pass(comparator, float(measurement.value), band)
    if primary is None:
        return None, "info-only (recorded, not gated)"
    if not primary:
        return False, _fail_reason(comparator, measurement.value, band)
    # primary passed — apply any compound secondary condition
    sec = _SECONDARY.get(band_id)
    if sec is not None and not sec(measurement):
        return False, _SECONDARY_REASON.get(band_id, "secondary condition failed")
    return True, None


def _fail_reason(comparator: str, value, band) -> str:
    if comparator == "range":
        return f"{value} outside [{band['min']}, {band['max']}]"
    if comparator == "gte":
        return f"{value} < {band['min']}"
    if comparator == "lte":
        return f"{value} > {band['max']}"
    if comparator == "eq":
        return f"{value} != {band['target']}"
    if comparator == "eq_true":
        return f"{value} is not true"
    return "out of band"


def measurement_to_row(m: Measurement) -> dict:
    """Gate one Measurement against its rubric band -> a canonical tracking row.

    A Measurement whose (stage,prop) has no band is recorded as ungated
    (passed=None, note explains) rather than dropped — so an analyzer drift is
    visible instead of silent.
    """
    band_id = f"{m.stage}:{m.prop}"
    band = rubric.band(m.stage, m.prop)
    row = {
        "artifact": m.artifact,
        "stage": m.stage,
        "prop": m.prop,
        "band_id": band_id,
        "kind": m.kind,
        "measured_value": (float(m.value) if isinstance(m.value, (int, float)) and not isinstance(m.value, bool)
                           else m.value),
        "rolls_up_to": list(m.rolls_up_to),
        "error": m.error,
        "note": None,
        "comparator": None,
        "band_min": None,
        "band_max": None,
        "band_target": None,
        "passed": None,
        "placeholder": False,
        "hard": False,
    }
    if band is None:
        row["note"] = "no rubric band for this property (analyzer/rubric drift)"
        return row
    row["comparator"] = band["comparator"]
    row["band_min"] = band.get("min")
    row["band_max"] = band.get("max")
    row["band_target"] = band.get("target")
    row["placeholder"] = bool(band.get("placeholder", False))
    row["hard"] = bool(band.get("hard", False))
    passed, reason = gate(m, band)
    row["passed"] = passed
    if reason:
        row["note"] = reason
    return row


def build_scorecard(measurements: list[Measurement]) -> dict:
    """Roll measurements up into rows + global dimensions + floor + verdict."""
    rows = [measurement_to_row(m) for m in measurements]

    weights = dict(rubric.global_weights())
    dims: dict[str, dict] = {
        g: {"name": rubric.global_dimensions()[g]["name"], "weight": weights[g],
            "passed": 0, "gated": 0, "contributors": [], "score": None}
        for g in weights
    }
    floor_ids = set(rubric.floor_properties())
    floor = {"passed": True, "failures": [], "checked": 0}
    anchor = {"present": False, "value": None, "passed": None, "band_id": None}

    gated = passed_n = failed_n = ungated = errors = 0

    for row in rows:
        if row["error"]:
            errors += 1
        bid = row["band_id"]
        is_floor = bid in floor_ids
        rolls = row["rolls_up_to"]

        # the holistic anchor (overall_polish) — recorded, never weighted
        if "ANCHOR" in rolls:
            anchor.update(present=True, value=row["measured_value"],
                          passed=row["passed"], band_id=bid)
            continue

        # hard floor F
        if is_floor or "F" in rolls:
            if row["passed"] is not None:
                floor["checked"] += 1
                if row["passed"] is False and row["hard"]:
                    floor["passed"] = False
                    floor["failures"].append({"band_id": bid, "note": row["note"]})
            # floor props may ALSO carry a weighted dim (rare); fall through only
            # for any non-F dims they roll up to
        # weighted global dimensions
        for g in rolls:
            if g in dims:
                if row["passed"] is None:
                    continue
                dims[g]["gated"] += 1
                dims[g]["contributors"].append(bid)
                if row["passed"]:
                    dims[g]["passed"] += 1

        # tallies (exclude anchor; floor counted in its own block but also here)
        if row["passed"] is True:
            gated += 1; passed_n += 1
        elif row["passed"] is False:
            gated += 1; failed_n += 1
        else:
            ungated += 1

    # dimension scores = fraction of gated contributors that passed
    present_weight = 0.0
    weighted_sum = 0.0
    for g, d in dims.items():
        if d["gated"] > 0:
            d["score"] = d["passed"] / d["gated"]
            present_weight += d["weight"]
            weighted_sum += d["weight"] * d["score"]
    quality_score = (weighted_sum / present_weight) if present_weight > 0 else None

    # decomposition-gap diagnostic: every gated local passed, but the anchor failed
    all_locals_pass = (failed_n == 0 and gated > 0)
    decomposition_gap = bool(all_locals_pass and anchor["present"] and anchor["passed"] is False)

    if not floor["passed"]:
        overall = "BLOCKED_BY_FLOOR"
    elif failed_n == 0 and gated > 0:
        overall = "PASS"
    else:
        overall = "FAIL"

    return {
        "rubric_version": rubric.rubric_version(),
        "rows": rows,
        "dimensions": dims,
        "floor": floor,
        "anchor": anchor,
        "quality_score": quality_score,
        "overall": overall,
        "decomposition_gap": decomposition_gap,
        "summary": {"gated": gated, "passed": passed_n, "failed": failed_n,
                    "ungated": ungated, "errors": errors, "total_rows": len(rows)},
    }
