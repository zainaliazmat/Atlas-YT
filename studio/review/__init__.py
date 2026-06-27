"""studio.review — the in-loop multi-critic vision review (PRODUCTION_BIBLE Prompt 5.0).

This is the quality mechanism that REPLACES schema-validity on the v2 path. Where the old
world trusted "the JSON validates / the effect-enum is set", this RENDERS a draft, LOOKS
at the frames, and critiques against measured evidence — the fix for GOLDEN_REFERENCE.md's
anti-pattern #3 (*never looking at frames produces flat output*). It runs IN-LOOP on every
draft, not as a post-hoc score.

The pipeline (``review(slug)``):

  1. EVIDENCE  (``evidence.collect_evidence``) — sample frames at each scene midpoint and
     each transition, measure durations / loudness+clipping / per-scene motion, load
     index.html, and anchor polish-vs-reference. Reuses motion_check + the eval analyzers.
  2. CRITIQUE  (``critics.run_critics``) — seven INDEPENDENT Claude vision critics
     (motion, narrative, brand, legibility, engagement, technical, fact), each grounded
     in the shared evidence.
  3. SYNTHESIZE (``synthesize.synthesize``) — merge + dedupe + rank into one fix list
     [ID|Severity|Scene|Issue|Evidence|Fix|Effort], flag conflicts.
  4. APPLY      (``apply.apply_fixes``) — default: auto-apply Blockers+Majors (Claude
     edits the scene's HTML, gated + reverted on regression), re-render the affected
     scenes, report before/after. The rest escalate to the final-render gate card. Mode
     ``stop`` skips apply and escalates everything for human approval.
  5. PERSIST    (``state.record_review``) — append the critique + applied fixes to the
     project's state.json (the audit trail; the home of the "coach THIS video, later the
     PACK" idea).

The existing ``motion_check`` no-dead-air gate is one measured input to step 1. Every LLM
seam is injectable so the whole review is offline-testable; heavy deps import lazily.
"""

from __future__ import annotations

import os
import time

DEFAULT_MODE = os.environ.get("STUDIO_REVIEW_MODE", "auto").strip().lower()


def review(slug: str, *, mode: str | None = None, video=None,
           vision_fn=None, do_render: bool = True, polish: bool = True) -> dict:
    """Run the full in-loop multi-critic review on a project's draft render.

    ``mode``: ``"auto"`` (default) auto-applies Blockers+Majors then re-renders the
    affected scenes; ``"stop"`` produces the ranked list and escalates everything for
    human approval (no edits, no re-render). Returns the report dict; persists it to
    state.json. Never raises on toolchain gaps — they surface in the report."""
    from . import evidence as ev_mod
    from . import critics as cr_mod
    from . import synthesize as syn_mod
    from . import apply as ap_mod
    from . import state as st_mod

    mode = (mode or DEFAULT_MODE).lower()

    evidence = ev_mod.collect_evidence(slug, video=video, vision_fn=vision_fn, polish=polish)
    findings = cr_mod.run_critics(evidence, vision_fn=vision_fn)
    synthesis = syn_mod.synthesize(findings)

    apply_result = None
    if mode == "auto":
        # re-measure seam for the before/after: cheap (no polish/vision)
        def _evidence_fn(s, video=None):
            return ev_mod.collect_evidence(s, video=video, polish=False)
        apply_result = ap_mod.apply_fixes(
            slug, synthesis, evidence, evidence_fn=_evidence_fn, do_render=do_render)

    entry = st_mod.record_review(slug, ts=time.time(), evidence=evidence,
                                 synthesis=synthesis, apply_result=apply_result, mode=mode)

    return {"slug": slug, "mode": mode, "evidence": evidence, "findings": findings,
            "synthesis": synthesis, "apply": apply_result, "state_entry": entry}


def format_report(report: dict) -> str:
    """Human-readable digest of a review run for the terminal."""
    from .synthesize import format_fix_table

    ev = report.get("evidence", {})
    syn = report.get("synthesis", {})
    counts = syn.get("counts", {})
    lines = []
    lines.append(f"MULTI-CRITIC REVIEW — {report.get('slug')}  (mode={report.get('mode')})")
    if ev.get("video"):
        from pathlib import Path
        lines.append(f"  draft: {Path(ev['video']).name}  "
                     f"({ev.get('render_duration_sec')}s)")
    ld = ev.get("loudness") or {}
    pol = (ev.get("polish_vs_reference") or {}).get("rate")
    lines.append(f"  audio: {ld.get('integrated_lufs')} LUFS  peak {ld.get('true_peak_dbtp')} dBTP  "
                 f"clipping={ld.get('clipping')}   polish-vs-{ev.get('reference')}: {pol}")
    if ev.get("errors"):
        lines.append(f"  ! evidence gaps: {', '.join(ev['errors'])}")
    lines.append("")
    summary = ", ".join(f"{n} {sev}" for sev, n in
                        sorted(counts.items(), key=lambda kv: kv[0])) or "none"
    lines.append(f"  {len(syn.get('fixes', []))} ranked fix(es): {summary}")
    lines.append("")
    lines.append(format_fix_table(syn))

    ap = report.get("apply")
    if ap:
        lines.append("")
        applied = ap.get("applied", [])
        lines.append(f"  AUTO-APPLIED ({len(applied)}): "
                     + (", ".join(f"{a['id']}→scene {a['scene']}" for a in applied) or "none"))
        if ap.get("reverted"):
            lines.append("  ⚠ edits REVERTED — gate regressed; all escalated.")
        lines.append(f"  ESCALATED to gate card: {ap.get('escalated', [])}")
        ba = ap.get("before_after") or {}
        for no, d in ba.items():
            b, a = d.get("before") or {}, d.get("after") or {}
            lines.append(f"    scene {no}: motion {b.get('motion_energy')}→{a.get('motion_energy')}  "
                         f"tail_static {b.get('trailing_static_sec')}→{a.get('trailing_static_sec')}s  "
                         f"status {b.get('status')}→{a.get('status')}")
        rr = ap.get("rerendered") or {}
        if rr:
            lines.append(f"  re-render: ok={rr.get('ok')} "
                         + (f"skipped ({rr.get('error')})" if rr.get('skipped') else ""))
        for e in ap.get("errors", []):
            lines.append(f"    ! {e}")
    return "\n".join(lines)
