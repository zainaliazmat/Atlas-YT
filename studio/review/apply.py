"""studio.review.apply — auto-apply Blockers+Majors, then re-render the affected scenes.

This is the CEO's "2B" choice: each Blocker/Major fix is applied by having Claude EDIT the
relevant scene's block in ``index.html`` directly (general — handles any fix), wrapped in
hard guardrails so an unattended edit can never ship a broken video:

  1. SELECT — only ``Blocker``/``Major`` fixes that name a scene and are NOT tangled in an
     unresolved conflict; everything else escalates to the final-render gate card.
  2. EDIT — locate the fix's scene block (the Nth ``<div class="scene clip">`` …
     ``</div>`` by tag depth) and ask the editor seam to rewrite ONLY that block per the
     fix. Edits are spliced cumulatively into the file.
  3. GUARDRAIL — re-run the composition gate (lint→validate→inspect) on the edited file.
     If it regresses, REVERT to the pristine ``index.html`` and escalate every edit.
  4. RE-RENDER — re-render the composition (single ``index.html`` → one mp4) and re-sample
     ONLY the affected scene windows to report a focused before/after (motion energy,
     trailing static, the frame).

Every external action is an INJECTABLE seam — ``editor_fn`` (LLM HTML edit), ``gate_fn``
(composition gate), ``render_fn`` (re-render), ``evidence_fn`` (re-measure) — so the whole
step is offline-testable with fakes and degrades gracefully when the toolchain is absent
(missing render → edits still applied + gated, re-render reported as skipped). Defaults
wire to atlas/llm + composition-engineer/hf_tools, the same battle-tested wrappers the v1
path uses (REUSE_MAP.md).
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import config

AUTO_SEVERITIES = ("Blocker", "Major")


# ======================================================================
# scene-block location (Nth `<div class="scene clip">` by tag depth)
# ======================================================================
def scene_block_spans(html: str) -> list[tuple[int, int]]:
    """Return ``[(start, end)]`` character spans of each scene block — any element
    (``<section>``/``<div>``/…) carrying ``class="… scene … clip …"`` — through its
    matching close tag, in document order. Pure + testable: it reads the opener's tag
    name and depth-counts THAT tag's open/close from each opener, so a block with nested
    children of the same tag still closes correctly."""
    spans: list[tuple[int, int]] = []
    opener_re = re.compile(
        r'<([a-zA-Z][\w-]*)[^>]*class="[^"]*\bscene\b[^"]*\bclip\b[^"]*"[^>]*>')
    for m in opener_re.finditer(html):
        tag = m.group(1).lower()
        tag_re = re.compile(rf"<{tag}\b|</{tag}\s*>", re.IGNORECASE)
        depth = 0
        pos = m.end()
        for t in tag_re.finditer(html, m.start()):
            if t.group(0).lower().startswith("<" + tag) and not t.group(0).startswith("</"):
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    pos = t.end()
                    break
        spans.append((m.start(), pos))
    return spans


def nth_scene_block(html: str, scene_no: int) -> tuple[int, int] | None:
    """Span of the block for scene ``scene_no`` (1-indexed), or None if out of range."""
    spans = scene_block_spans(html)
    if 1 <= scene_no <= len(spans):
        return spans[scene_no - 1]
    return None


# ======================================================================
# default seams (lazy — keep `import studio` cheap)
# ======================================================================
def _default_editor(block_html: str, fix: dict, scene: dict) -> str:
    """Ask the text LLM to rewrite ONE scene block per a fix, returning the new block.
    Reuses atlas/llm.chat (HTML editing is a text task). Returns the block UNCHANGED on
    any failure so the caller can detect a no-op and escalate."""
    import sys
    atlas = str((config.REPO_ROOT / "atlas").resolve())
    if atlas not in sys.path:
        sys.path.insert(0, atlas)
    try:
        import llm  # atlas/llm.py
    except Exception:
        return block_html
    system = (
        "You are a careful motion-graphics engineer editing a single HyperFrames scene "
        "block (HTML + GSAP). Apply ONLY the requested fix. Preserve the scene's id/"
        "class/data attributes, its GSAP timeline registration, and seek-safety. Do NOT "
        "introduce Math.random, Date.now, new Date, or fetch. Reply with ONLY the revised "
        "<div class=\"scene clip\">…</div> block — no prose, no code fences.")
    user = (f"FIX TO APPLY: {fix.get('fix')}\n"
            f"WHY (evidence): {fix.get('evidence')}\n"
            f"Scene {fix.get('scene')} on-screen text: {scene.get('on_screen_text')!r}\n\n"
            f"CURRENT BLOCK:\n{block_html}\n\n"
            "Return the full revised block.")
    try:
        reply = llm.chat(system, user)
    except Exception:
        return block_html
    reply = reply.strip()
    fence = re.search(r"```(?:html)?\s*(.+?)```", reply, re.DOTALL)
    if fence:
        reply = fence.group(1).strip()
    # sanity: the reply must still be a single scene-clip block element (any tag —
    # the real composition uses <section class="scene clip">, not <div>), and must not
    # have smuggled in nondeterminism.
    banned = ("math.random", "date.now", "new date(", "fetch(", "xmlhttprequest")
    looks_like_block = (reply.startswith("<") and "scene" in reply and "clip" in reply)
    if looks_like_block and not any(b in reply.lower() for b in banned):
        return reply
    return block_html


def _default_gate(pdir: Path) -> dict:
    """Composition gate via composition-engineer/hf_tools.run_gate (REUSE)."""
    import sys
    ce = str((config.REPO_ROOT / "composition-engineer").resolve())
    if ce not in sys.path:
        sys.path.insert(0, ce)
    try:
        import hf_tools  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"ok": None, "error": f"hf_tools unavailable: {exc}", "skipped": True}
    try:
        res = hf_tools.run_gate(pdir)
        # run_gate returns {lint, validate, inspect} (no top-level ok); it short-circuits
        # to None on the first hard fail. Overall pass = all three present and ok.
        subs = [res.get("lint"), res.get("validate"), res.get("inspect")]
        ok = all(isinstance(s, dict) and s.get("ok") for s in subs)
        return {"ok": ok, "detail": res}
    except Exception as exc:  # noqa: BLE001
        return {"ok": None, "error": str(exc), "skipped": True}


def _default_render(pdir: Path) -> dict:
    """Re-render the draft via composition-engineer/hf_tools.run_render (REUSE)."""
    import sys
    ce = str((config.REPO_ROOT / "composition-engineer").resolve())
    if ce not in sys.path:
        sys.path.insert(0, ce)
    try:
        import hf_tools  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"ok": None, "error": f"hf_tools unavailable: {exc}", "skipped": True,
                "video": None}
    try:
        res = hf_tools.run_render(pdir)
        return {"ok": bool(res.get("ok")), "video": res.get("output"), "detail": res}
    except Exception as exc:  # noqa: BLE001
        return {"ok": None, "error": str(exc), "skipped": True, "video": None}


# ======================================================================
# before/after on affected scenes only
# ======================================================================
def _scene_snapshot(evidence: dict, scene_nos: set[int]) -> dict:
    """Pull the measured spine (motion energy, trailing static, status) for just the
    affected scenes out of an evidence pack — the unit of the before/after diff."""
    out = {}
    for s in evidence.get("scenes", []):
        if s["scene_no"] in scene_nos:
            m = s.get("motion") or {}
            out[s["scene_no"]] = {
                "motion_energy": m.get("motion_energy"),
                "trailing_static_sec": m.get("trailing_static_sec"),
                "animating_at_cut": m.get("animating_at_cut"),
                "status": m.get("status"),
                "duration_sec": s.get("duration_sec"),
            }
    return out


# ======================================================================
# the auto-apply step
# ======================================================================
def select_auto_fixes(synthesis: dict) -> tuple[list[dict], list[dict]]:
    """Split the ranked fixes into (auto-apply, escalate). Auto = Blocker/Major with a
    concrete scene and NOT entangled in an unresolved conflict; everything else
    escalates to the gate card."""
    conflicted_ids = set()
    for c in synthesis.get("conflicts", []):
        conflicted_ids.update(c.get("between", []))
    auto, escalate = [], []
    for f in synthesis.get("fixes", []):
        if (f["severity"] in AUTO_SEVERITIES and f.get("scene") is not None
                and f["id"] not in conflicted_ids):
            auto.append(f)
        else:
            escalate.append(f)
    return auto, escalate


def apply_fixes(slug: str, synthesis: dict, evidence_before: dict, *,
                editor_fn=None, gate_fn=None, render_fn=None, evidence_fn=None,
                do_render: bool = True) -> dict:
    """Auto-apply Blocker/Major fixes to index.html, guardrail with the gate, re-render,
    and report a before/after on the affected scenes. Never raises.

    Returns ``{applied, escalated, rerendered, before_after, reverted, errors}``."""
    editor_fn = editor_fn or _default_editor
    gate_fn = gate_fn or _default_gate
    render_fn = render_fn or _default_render

    pdir = config.PROJECTS_DIR / slug
    index_path = pdir / "index.html"
    errors: list[str] = []

    auto, escalate = select_auto_fixes(synthesis)
    escalated = [f["id"] for f in escalate]

    if not auto:
        return {"applied": [], "escalated": escalated, "rerendered": None,
                "before_after": {}, "reverted": False, "errors": errors,
                "note": "no auto-applicable Blocker/Major fixes"}

    if not index_path.is_file():
        return {"applied": [], "escalated": [f["id"] for f in synthesis.get("fixes", [])],
                "rerendered": None, "before_after": {}, "reverted": False,
                "errors": ["no index.html — cannot auto-apply"]}

    original = index_path.read_text(encoding="utf-8")
    backup = index_path.with_suffix(".html.prereview")
    backup.write_text(original, encoding="utf-8")

    # baseline gate BEFORE any edit: the guardrail reverts only on a true REGRESSION
    # (baseline passed, post-edit fails) — not when the draft was already gate-red.
    baseline_gate = gate_fn(pdir)

    scenes_by_no = {s["scene_no"]: s for s in evidence_before.get("scenes", [])}
    html = original
    applied: list[dict] = []
    for fix in auto:
        span = nth_scene_block(html, fix["scene"])
        if span is None:
            errors.append(f"{fix['id']}: scene {fix['scene']} block not found — escalated")
            escalated.append(fix["id"])
            continue
        block = html[span[0]:span[1]]
        new_block = editor_fn(block, fix, scenes_by_no.get(fix["scene"], {}))
        if new_block == block:
            errors.append(f"{fix['id']}: editor made no change — escalated")
            escalated.append(fix["id"])
            continue
        html = html[:span[0]] + new_block + html[span[1]:]
        applied.append({"id": fix["id"], "scene": fix["scene"],
                        "severity": fix["severity"], "fix": fix["fix"]})

    if not applied:
        return {"applied": [], "escalated": escalated, "rerendered": None,
                "before_after": {}, "reverted": False, "errors": errors,
                "note": "no edits took"}

    index_path.write_text(html, encoding="utf-8")

    # GUARDRAIL: re-gate; revert everything only on a true REGRESSION (baseline passed
    # but the edits broke it). If the draft was already gate-red, we don't block the
    # edits on a failure they didn't introduce.
    gate = gate_fn(pdir)
    regressed = (baseline_gate.get("ok") is True) and (gate.get("ok") is False)
    if regressed:
        index_path.write_text(original, encoding="utf-8")
        return {"applied": [], "escalated": [f["id"] for f in synthesis.get("fixes", [])],
                "rerendered": None, "before_after": {}, "reverted": True,
                "errors": errors + ["gate regressed after edits — reverted all"],
                "gate": gate, "baseline_gate": baseline_gate}

    affected = {f["scene"] for f in applied}
    before = _scene_snapshot(evidence_before, affected)

    rerendered = None
    after = {}
    if do_render:
        render = render_fn(pdir)
        rerendered = render
        if render.get("ok") and evidence_fn is not None:
            try:
                ev_after = evidence_fn(slug, video=render.get("video"))
                after = _scene_snapshot(ev_after, affected)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"post-render re-measure failed: {exc}")
        elif render.get("skipped"):
            errors.append("re-render skipped (toolchain unavailable)")

    before_after = {str(no): {"before": before.get(no), "after": after.get(no)}
                    for no in sorted(affected)}
    return {"applied": applied, "escalated": escalated, "rerendered": rerendered,
            "before_after": before_after, "reverted": False, "errors": errors,
            "gate": gate, "backup": str(backup)}
