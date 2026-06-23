"""The Diagnostician — credit assignment from a scorecard shortfall to ONE owner.

The whole point of the per-artifact decomposition is that a global shortfall
points to a single owning stage. This module reads a scorecard and:

  * groups failures by global dimension and by owning stage,
  * flags the MULTI-STAGE case (a dimension failing across >1 stage) for
    coordination — so two coaches never optimize against each other blind,
  * flags the DECOMPOSITION-GAP case (locals pass, holistic anchor fails) for
    CEO escalation (the rubric is missing a term — a CEO-owned change), and
  * picks ONE primary target for a single improvement iteration: a soft-tier,
    single-owner failure in the highest-weight dimension.

It proposes nothing and changes nothing — it only attributes.
"""
from __future__ import annotations

from typing import Optional

import rubric

# Stages whose output is driven by an editable prompt/persona/playbook — the
# soft tier the improver is allowed to tune. (assets/compose/audiomix have hard
# logic mixed in; phase-1 loop restricts itself to clearly soft-tier stages.)
SOFT_TIER_STAGES = {"script", "style", "storyboard", "narration"}

STAGE_OWNER = {
    "research": "Sage", "script": "Marlow", "factcheck": "Sage",
    "style": "Iris", "storyboard": "Iris", "assets": "Magpie",
    "narration": "Cadence", "compose": "Mason", "audiomix": "Cadence",
    "render": "Mason",
}


def diagnose(scorecard: dict) -> dict:
    """Attribute a scorecard's failures to owning stages + pick a primary target."""
    failures = [r for r in scorecard.get("rows", []) if r["passed"] is False]
    weights = rubric.global_weights()

    by_dimension: dict[str, dict] = {}
    for r in failures:
        for g in r["rolls_up_to"]:
            if g not in weights:   # skip F / ANCHOR here (handled separately)
                continue
            d = by_dimension.setdefault(g, {"dimension": g, "weight": weights[g],
                                            "failures": [], "stages": set()})
            d["failures"].append(r["band_id"])
            d["stages"].add(r["stage"])

    coordination_needed = []
    for g, d in by_dimension.items():
        d["stages"] = sorted(d["stages"])
        d["coordination"] = len(d["stages"]) > 1
        if d["coordination"]:
            coordination_needed.append(g)

    floor_fail = not scorecard.get("floor", {}).get("passed", True)
    decomposition_gap = scorecard.get("decomposition_gap", False)

    return {
        "n_failures": len(failures),
        "by_dimension": by_dimension,
        "coordination_needed": coordination_needed,
        "floor_failed": floor_fail,
        "floor_failures": scorecard.get("floor", {}).get("failures", []),
        "decomposition_gap": decomposition_gap,
        "escalate_to_ceo": _escalations(floor_fail, decomposition_gap),
        "primary_target": pick_primary_target(scorecard),
    }


def _escalations(floor_fail: bool, decomposition_gap: bool) -> list[str]:
    out = []
    if decomposition_gap:
        out.append("decomposition_gap: holistic anchor failed while all locals passed "
                   "-> the rubric is missing a term; CEO must name it (a CEO-owned change).")
    if floor_fail:
        out.append("floor_failure: a hard technical-integrity floor failed "
                   "-> route upstream; not a soft-tier tuning target.")
    return out


def pick_primary_target(scorecard: dict) -> Optional[dict]:
    """Choose ONE failing property to fix this iteration.

    Selection rule (faithful to the credit-assignment design):
      1. only soft-tier, single-owner failures (no coordination conflicts),
      2. exclude hard-floor properties (those route upstream, not tune),
      3. prefer the highest-weight global dimension,
      4. tie-break by the largest single-dimension contribution.
    Returns the target descriptor or None if nothing is a clean soft-tier fix.
    """
    weights = rubric.global_weights()
    failures = [r for r in scorecard.get("rows", []) if r["passed"] is False]

    # which stages have failures in each dimension (for single-owner check)
    dim_stages: dict[str, set] = {}
    for r in failures:
        for g in r["rolls_up_to"]:
            if g in weights:
                dim_stages.setdefault(g, set()).add(r["stage"])

    candidates = []
    for r in failures:
        if r.get("hard"):
            continue
        if r["stage"] not in SOFT_TIER_STAGES:
            continue
        # the dimension(s) this rolls up to must be single-owner
        dims = [g for g in r["rolls_up_to"] if g in weights]
        if not dims:
            continue
        if any(len(dim_stages.get(g, set())) > 1 for g in dims):
            continue  # coordination needed -> not a clean single-owner target
        top_dim = max(dims, key=lambda g: weights[g])
        candidates.append((weights[top_dim], r, top_dim))

    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    weight, r, top_dim = candidates[0]
    return {
        "band_id": r["band_id"],
        "stage": r["stage"],
        "owner": STAGE_OWNER.get(r["stage"], "?"),
        "dimension": top_dim,
        "dimension_weight": weight,
        "measured_value": r["measured_value"],
        "comparator": r["comparator"],
        "band_min": r["band_min"],
        "band_max": r["band_max"],
        "band_target": r["band_target"],
        "note": r["note"],
        "placeholder": r["placeholder"],
    }
