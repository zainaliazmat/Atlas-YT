"""Roundtable-log analyzer — the eval system's window into the creative PROCESS.

Every objective analyzer in this package returns `list[Measurement]` that the
rubric then gates. This module is deliberately DIFFERENT: it reads
`roundtable_log.json` (the artifact scriptwriter/roundtable.py writes when a
specialist's internal Critic→Researcher→Craftsman review runs) and returns a
plain DIAGNOSTICS dict — a SIDE CHANNEL the Inspector attaches to the scorecard.

Why a side channel and not Measurements:
  * The roundtable record is about HOW the work was made, not whether the final
    artifact clears a quality band. There are no `process:*` bands in the
    CEO-owned rubric (and there must not be — the rubric measures OUTPUT). So
    these signals are surfaced for the coaches and the CEO, never gated.
  * It supercharges the coaches (Quill/Flux): instead of seeing only the final
    script, they can see WHERE in the Critic→Researcher→Craftsman chain a
    weakness originated — a lenient Critic, a source-less Researcher, or a
    Craftsman who ignored real findings each call for a DIFFERENT coaching fix.

Graceful: a missing/garbled log yields None (the eval system runs exactly as
before). The log key names track the real `roundtable_log.json` schema:
specialist, criticisms[].severity, research_findings[].source_url/detail_type,
draft_artifact, enhanced_artifact, diff_summary.scenes_modified, error.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _load_log(project_dir: Path) -> Optional[dict]:
    """Read roundtable_log.json from a project dir, or None if absent/unreadable."""
    log_path = Path(project_dir) / "roundtable_log.json"
    if not log_path.is_file():
        return None
    try:
        data = json.loads(log_path.read_text())
    except Exception as exc:  # noqa: BLE001 — a garbled log degrades, never crashes
        logger.warning("roundtable_log.json present but unreadable: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def analyze_roundtable(project_dir: Path) -> Optional[dict]:
    """Turn a roundtable log into process diagnostics for the scorecard.

    Returns a dict of named diagnostics (each `{value, band_id, note}`), or None
    when no log exists. The `band_id`s are `process:*` namespaced and NEVER appear
    in the rubric — they are diagnostic labels, not gated bands.
    """
    log = _load_log(project_dir)
    if log is None:
        logger.info("No roundtable_log.json found — skipping roundtable analysis.")
        return None

    measurements: dict = {}

    # 1. Was the roundtable used at all?
    measurements["roundtable_active"] = {
        "value": True,
        "band_id": "process:roundtable_used",
        "note": f"Roundtable completed in {log.get('duration_seconds', '?')}s",
    }

    # 2. Critic effectiveness — did it find real, severe issues?
    criticisms = log.get("criticisms") or []
    if criticisms:
        severities = [c.get("severity") for c in criticisms]
        critical = severities.count("critical")
        major = severities.count("major")
        measurements["critic_severity_distribution"] = {
            "value": {"critical": critical, "major": major,
                      "moderate": len(criticisms) - critical - major},
            "band_id": "process:critic_severity",
            "note": (f"Critic found {len(criticisms)} issues: "
                     f"{critical} critical, {major} major"),
        }
        # No critical AND no major issues → the Critic may be too soft.
        if critical == 0 and major == 0:
            measurements["critic_leniency_flag"] = {
                "value": "warning",
                "band_id": "process:critic_leniency",
                "note": ("Critic found only moderate issues. Possible leniency — "
                         "review the Critic prompt for sufficient ruthlessness."),
            }

    # 3. Researcher effectiveness — did it find SOURCED material?
    findings = log.get("research_findings") or []
    if findings:
        with_sources = sum(1 for f in findings if (f.get("source_url") or "").strip())
        measurements["researcher_productivity"] = {
            "value": {
                "total_findings": len(findings),
                "finding_types": [f.get("detail_type") for f in findings],
                "findings_with_sources": with_sources,
                "source_rate": round(with_sources / len(findings), 3),
            },
            "band_id": "process:researcher_productivity",
            "note": f"Researcher found {len(findings)} items; {with_sources} with sources",
        }
        if with_sources == 0:
            measurements["researcher_source_gap"] = {
                "value": "warning",
                "band_id": "process:researcher_sources",
                "note": ("Researcher provided findings without sources. The search "
                         "tool may not be functioning."),
            }

    # 4. Craftsman effectiveness — did the rewrite actually change anything?
    draft = log.get("draft_artifact") or {}
    enhanced = log.get("enhanced_artifact") or {}
    diff = log.get("diff_summary") or {}
    if draft and enhanced:
        scenes_changed = int(diff.get("scenes_modified", 0) or 0)
        total_scenes = len(enhanced.get("scenes", []) or [])
        measurements["craftsman_impact"] = {
            "value": {
                "scenes_modified": scenes_changed,
                "total_scenes": total_scenes,
                "change_rate": round(scenes_changed / total_scenes, 3) if total_scenes else 0,
            },
            "band_id": "process:craftsman_impact",
            "note": f"Craftsman modified {scenes_changed}/{total_scenes} scenes",
        }
        # Critic flagged issues but the Craftsman changed nothing → the loop is broken.
        if criticisms and scenes_changed == 0:
            measurements["craftsman_no_op_flag"] = {
                "value": "critical",
                "band_id": "process:craftsman_no_op",
                "note": ("CRITICAL: Critic found issues but Craftsman changed nothing. "
                         "The roundtable loop is broken."),
            }

    # 5. Overall process health.
    measurements["roundtable_process_health"] = {
        "value": _assess_process_health(log),
        "band_id": "process:roundtable_health",
        "note": "Overall assessment of roundtable process quality",
    }

    return measurements


def _assess_process_health(log: dict) -> str:
    """One-line verdict on whether the roundtable operated correctly."""
    issues = []
    if not (log.get("criticisms") or []):
        issues.append("Critic produced no criticisms")
    if not (log.get("research_findings") or []):
        issues.append("Researcher produced no findings")
    if not (log.get("enhanced_artifact") or {}):
        issues.append("Craftsman produced no enhanced artifact")
    draft = log.get("draft_artifact") or {}
    enhanced = log.get("enhanced_artifact") or {}
    if draft and draft == enhanced:
        issues.append("draft and enhanced artifact are identical — no changes made")
    if log.get("error"):
        issues.append(f"recorded error: {log['error']}")
    if issues:
        return "degraded: " + "; ".join(issues)
    return "healthy"


def get_coach_context(project_dir: Path) -> Optional[dict]:
    """Roundtable data formatted specifically for the coaches (Quill/Flux).

    Gives a coach granular insight into the creative PROCESS so it can diagnose
    WHERE in the Critic→Researcher→Craftsman chain a quality issue originated, and
    tailor its addendum (strengthen the Critic, fix the Researcher's search, or
    enforce Craftsman synthesis discipline). Returns None when no log exists.
    """
    log = _load_log(project_dir)
    if log is None:
        return None
    findings = log.get("research_findings") or []
    return {
        "roundtable_used": True,
        "specialist": log.get("specialist"),
        "criticisms": [
            {"severity": c.get("severity"),
             "principle": c.get("principle_violated"),
             "diagnosis": c.get("diagnosis")}
            for c in (log.get("criticisms") or [])
        ],
        "research_quality": {
            "total_findings": len(findings),
            "findings_with_sources": sum(
                1 for f in findings if (f.get("source_url") or "").strip()),
        },
        "craftsman_impact": log.get("diff_summary") or {},
        "process_health": _assess_process_health(log),
    }
