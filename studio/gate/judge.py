"""studio.gate.judge — the LLM holistic dimension (polish_vs_reference). It does NOT call
the model itself: studio.review.evidence already runs the ensembled, order-randomised,
seeded pairwise vote vs the pack's reference frames. This maps that rate → 0-5 and applies
the ensemble margin (need >= min_votes countable votes, else None so a thin sample never
blocks). Deterministic dims remain the primary blockers."""
from __future__ import annotations

from .types import DimResult, band_score


def score_polish(ev: dict, t: dict) -> DimResult:
    cfg = t["dimensions"]["polish_vs_reference"]
    floor = float(cfg["floor"])
    pol = ev.get("polish_vs_reference") or {}
    rate, n = pol.get("rate"), int(pol.get("n") or 0)
    min_votes = int(cfg.get("min_votes", 3))
    if rate is None or n < min_votes:
        return DimResult("polish_vs_reference", None, floor, None,
                         [f"polish anchor inconclusive ({n} votes < {min_votes})"], {})
    score = band_score(rate, *cfg["band"])
    margin = float(cfg.get("margin", 0.0))
    passed = score >= (floor + margin)
    diags = [] if passed else [f"loses to the reference on {round((1-rate)*n)}/{n} votes — below the polish bar"]
    return DimResult("polish_vs_reference", score, floor, passed, diags,
                     {"rate": rate, "votes": n})
