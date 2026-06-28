"""studio.gate.scorecard — combine the 0-5 dimensions + the hard compliance checks into a
single verdict + the SPECIFIC reasons a block happened, and the public gate.score() seam.

Blocking rule (mirrors the spec):
  BLOCKED if any compliance check fails (passed is False),
           or any thresholds-flagged compliance check is unavailable (passed is None and
           the threshold marks it blocking),
           or any scored dimension is below its floor (passed is False).
A dimension with score=None is skipped (non-blocking, noted)."""
from __future__ import annotations

from pathlib import Path

from .types import DimResult, ComplianceResult, load_thresholds
from . import dimensions as D
from . import judge as J
from . import compliance as CO


def build_scorecard(dims, compliance, t: dict) -> dict:
    reasons: list[str] = []

    # compliance: hard fails always block; None blocks only if the threshold says so
    comp_cfg = t.get("compliance", {})
    _block_if_unavailable = {"overflow": comp_cfg.get("overflow_blocks", False),
                             "likeness": comp_cfg.get("likeness_blocks", False)}
    comp_rows = []
    for c in compliance:
        # blocking=True only when the check actually fires a hard stop:
        # - passed is False → definite failure
        # - passed is None AND the threshold marks this check blocking when unavailable
        #   (overflow_blocks / likeness_blocks)
        blocking = (c.passed is False) or (c.passed is None and _block_if_unavailable.get(c.name, False))
        if c.passed is False:
            reasons.append(f"COMPLIANCE {c.name}: {c.reason}")
        elif c.passed is None and _block_if_unavailable.get(c.name, False):
            reasons.append(f"COMPLIANCE {c.name}: unavailable and required ({c.reason})")
        comp_rows.append({"name": c.name, "passed": c.passed, "reason": c.reason, "blocking": blocking})

    # dimensions: below-floor blocks
    dim_rows = []
    weighted, wsum = 0.0, 0.0
    for d in dims:
        if d.passed is False:
            why = "; ".join(d.diagnostics) or f"score {d.score} < floor {d.floor}"
            reasons.append(f"{d.name} {d.score}/5 (floor {d.floor}): {why}")
        if d.score is not None:
            w = float(t["dimensions"].get(d.name, {}).get("weight", 0.0))
            weighted += w * d.score
            wsum += w
        dim_rows.append({"name": d.name, "score": d.score, "floor": d.floor,
                         "passed": d.passed, "diagnostics": d.diagnostics, "detail": d.detail})

    # Honor the per-row `blocking` flag (not just passed is False): a compliance check
    # marked required-when-unavailable (overflow_blocks / likeness_blocks) must flip the
    # verdict, not merely log a reason. With the shipped warn-only thresholds those flags
    # are false, so `blocking` reduces to `passed is False` and behavior is unchanged.
    blocked = any(r["blocking"] for r in comp_rows) or \
        any(d.passed is False for d in dims)
    overall = round(weighted / wsum, 3) if wsum else None
    return {"verdict": "BLOCKED" if blocked else "PASS",
            "reasons": reasons, "overall": overall,
            "dimensions": dim_rows, "compliance": comp_rows}


def _all_dimensions(ev: dict, t: dict) -> list[DimResult]:
    return [
        D.score_motion_energy(ev, t),
        D.score_motion_variety(ev, t),
        D.score_content_fidelity(ev, t),
        D.score_dead_air(ev, t),
        D.score_pacing(ev, t),
        D.score_audio(ev, t),
        J.score_polish(ev, t),
    ]


def score(slug: str | None = None, *, video=None, index_html=None, script=None,
          pdir=None, thresholds=None, evidence=None, vision_fn=None, inspect_fn=None,
          polish: bool = True) -> dict:
    """Score a draft. Three input modes:
      - slug=...                         → build evidence via studio.review.evidence
      - evidence={...}                   → use the injected evidence pack (tests / reference)
      - index_html=..., script=..., video=... → minimal evidence for a twin-less artifact
    """
    t = thresholds or load_thresholds()

    if evidence is None and slug is not None:
        from studio.review import evidence as ev_mod
        evidence = ev_mod.collect_evidence(slug, video=video, vision_fn=vision_fn, polish=polish)
        from studio import config
        pdir = pdir or (config.PROJECTS_DIR / slug)
    elif evidence is None:
        # explicit-artifact mode: assemble the minimal pack the dimensions need.
        from studio.review import motion_check as mc
        html = Path(index_html).read_text(encoding="utf-8") if index_html else ""
        scr = {}
        if script:
            import json as _json
            scr = _json.loads(Path(script).read_text(encoding="utf-8"))
        evidence = {"index_html": html, "script": scr, "video": str(video) if video else None,
                    "frames": [], "scenes": scr.get("scenes", []),
                    "global": {}, "motion": {}, "loudness": {},
                    "polish_vs_reference": {"rate": None, "n": 0}, "errors": []}
        if video:
            try:
                evidence["global"] = mc.global_measures(video, {}) or {}
            except Exception:
                pass

    dims = _all_dimensions(evidence, t)
    compliance = CO.run_compliance(evidence, pdir or Path("."), t,
                                   inspect_fn=inspect_fn, vision_fn=vision_fn)
    sc = build_scorecard(dims, compliance, t)
    sc["slug"] = slug
    sc["video"] = evidence.get("video")
    return sc
