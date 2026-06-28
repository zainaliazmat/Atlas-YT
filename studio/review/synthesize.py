"""studio.review.synthesize — merge the seven critics into ONE ranked fix list.

The critics (``studio.review.critics``) each return findings from their own lens; many
overlap (motion AND legibility both flag scene 4's frozen text card). This module is the
pure, deterministic step that turns that union into the artifact a human (or the
auto-apply step) acts on:

  - DEDUPE/MERGE near-duplicate findings (same scene + overlapping issue text) into one,
    keeping the HIGHEST severity, unioning the lenses that raised it (consensus is a
    ranking signal), and combining their evidence + fixes;
  - RANK by severity → consensus (how many lenses agreed) → effort (cheap wins first),
    assigning stable ``R01..`` IDs;
  - FLAG CONFLICTS where two fixes on the same scene pull opposite directions
    (extend vs trim, add-motion vs calm-down, brighten vs darken) so a human resolves
    them instead of the auto-apply step thrashing.

The output shape is the bible's ranked table: ``[ID | Severity | Scene | Issue |
Evidence | Fix | Effort]`` plus the lenses + conflicts. No I/O, no LLM — fully unit-
testable.
"""

from __future__ import annotations

import re

SEVERITY_RANK = {"Blocker": 0, "Major": 1, "Minor": 2, "Nit": 3}
EFFORT_RANK = {"S": 0, "M": 1, "L": 2}

# opposing-intent keyword pairs used for conflict detection on the same scene
_OPPOSING = [
    ({"extend", "lengthen", "longer", "slow", "slower", "hold"},
     {"trim", "shorten", "shorter", "faster", "speed up", "cut down"}),
    ({"add motion", "more motion", "more movement", "animate", "liven"},
     {"reduce motion", "less motion", "calm", "settle", "static", "still"}),
    ({"brighten", "lighter", "raise contrast", "more contrast"},
     {"darken", "dimmer", "lower contrast", "less contrast"}),
    ({"louder", "raise level", "boost audio"},
     {"quieter", "lower level", "duck", "reduce audio"}),
]

_STOP = {"the", "a", "an", "is", "to", "of", "and", "in", "on", "at", "for", "it",
         "this", "that", "with", "too", "before", "its", "by", "be", "are", "scene"}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP}


def _similar(a: dict, b: dict, *, threshold: float = 0.5) -> bool:
    """Two findings describe the same problem if they share a scene (or both scene-less)
    and their issue tokens overlap past a Jaccard threshold."""
    if a.get("scene") != b.get("scene"):
        return False
    ta, tb = _tokens(a.get("issue", "")), _tokens(b.get("issue", ""))
    if not ta or not tb:
        return False
    jacc = len(ta & tb) / len(ta | tb)
    return jacc >= threshold


def _merge(group: list[dict]) -> dict:
    """Collapse a group of near-duplicate findings into one merged finding."""
    best = min(group, key=lambda f: SEVERITY_RANK.get(f["severity"], 9))
    lenses = sorted({f["lens"] for f in group})
    effort = min((f.get("effort", "M") for f in group), key=lambda e: EFFORT_RANK.get(e, 9))
    evidence = " | ".join(dict.fromkeys(f["evidence"] for f in group if f.get("evidence")))
    fixes = " | ".join(dict.fromkeys(f["fix"] for f in group if f.get("fix")))
    return {
        "severity": best["severity"],
        "scene": best.get("scene"),
        "issue": best["issue"],
        "evidence": evidence,
        "fix": fixes or best.get("fix", ""),
        "effort": effort,
        "lenses": lenses,
        "consensus": len(lenses),
    }


def _dedupe(findings: list[dict]) -> list[dict]:
    groups: list[list[dict]] = []
    for f in findings:
        for g in groups:
            if any(_similar(f, member) for member in g):
                g.append(f)
                break
        else:
            groups.append([f])
    return [_merge(g) for g in groups]


def _detect_conflicts(fixes: list[dict]) -> list[dict]:
    """Pairs of same-scene fixes whose text matches an opposing-intent keyword pair."""
    conflicts = []
    for i in range(len(fixes)):
        for j in range(i + 1, len(fixes)):
            a, b = fixes[i], fixes[j]
            if a.get("scene") != b.get("scene") or a.get("scene") is None:
                continue
            ta = (a["fix"] + " " + a["issue"]).lower()
            tb = (b["fix"] + " " + b["issue"]).lower()
            for left, right in _OPPOSING:
                a_left = any(k in ta for k in left)
                a_right = any(k in ta for k in right)
                b_left = any(k in tb for k in left)
                b_right = any(k in tb for k in right)
                if (a_left and b_right) or (a_right and b_left):
                    conflicts.append({"scene": a["scene"], "between": [a["id"], b["id"]],
                                      "note": f"{a['id']} and {b['id']} pull opposite "
                                              f"directions on scene {a['scene']}"})
                    break
    return conflicts


def synthesize(findings: list[dict]) -> dict:
    """Merge + rank the critics' union into the ranked fix list + conflicts.

    Returns ``{"fixes": [...ranked, with stable IDs...], "conflicts": [...],
    "counts": {severity: n}}``."""
    merged = _dedupe(findings)
    merged.sort(key=lambda f: (SEVERITY_RANK.get(f["severity"], 9),
                               -f["consensus"],
                               EFFORT_RANK.get(f["effort"], 9)))
    for i, f in enumerate(merged, 1):
        f["id"] = f"R{i:02d}"
    conflicts = _detect_conflicts(merged)
    counts: dict[str, int] = {}
    for f in merged:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return {"fixes": merged, "conflicts": conflicts, "counts": counts}


def format_fix_table(synthesis: dict) -> str:
    """The bible's ranked table, rendered for the terminal."""
    fixes = synthesis.get("fixes", [])
    lines = [f"  {'ID':<4} {'Severity':<8} {'Sc':>3} {'Eff':>3}  {'Lenses':<22} Issue → Fix"]
    for f in fixes:
        sc = f["scene"] if f["scene"] is not None else "-"
        lenses = ",".join(f["lenses"])[:21]
        issue = (f["issue"][:60] + "…") if len(f["issue"]) > 61 else f["issue"]
        fix = (f["fix"][:60] + "…") if len(f["fix"]) > 61 else f["fix"]
        lines.append(f"  {f['id']:<4} {f['severity']:<8} {str(sc):>3} {f['effort']:>3}  "
                     f"{lenses:<22} {issue}")
        lines.append(f"  {'':<4} {'':<8} {'':>3} {'':>3}  {'':<22} → {fix}")
    if synthesis.get("conflicts"):
        lines.append("")
        lines.append("  ⚠ conflicts (resolve by hand):")
        for c in synthesis["conflicts"]:
            lines.append(f"    - {c['note']}")
    return "\n".join(lines)
