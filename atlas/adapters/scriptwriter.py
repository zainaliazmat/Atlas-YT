"""Adapter for Marlow (scriptwriter) — the Scriptwriter.

ONE real job: write_script(topic) -> a tight, one-point-per-scene script drafted
from the project's research_brief.json, via Marlow's `script_engine.write_script`.

DECOUPLING: Marlow's engine emits the script as a plain dict in the frozen shape and
NEVER imports atlas. ATLAS owns the contract — it stamps `schema_version` and
validates against `script.schema.json` here, at the boundary (the pipeline does it
per-stage; the conversational path below does it explicitly).

TRACEABILITY: every factual claim Marlow ships carries a `source_ref` that resolves
to a real `research_brief.sources` entry — the engine guarantees this by construction
(a claim it can't ground is dropped before it's written), so the Fact-Checker's
pass-2 has a real script to interrogate.

PERSONA `ask` is inherited from base.
"""
from __future__ import annotations

import os
import pathlib

import chat_state
from adapters.base import Adapter
from adapters.loader import load_engine


# ----------------------------------------------------------------------
# The script engine seam (one place; tests monkeypatch this)
# ----------------------------------------------------------------------
def _script_engine():
    """Load Marlow's `script_engine` module (isolated, cached by the loader)."""
    import registry  # lazy: registry imports this module, so avoid a top-level cycle
    sw_dir = registry.get_entry("scriptwriter").project_dir
    return load_engine(sw_dir, "script_engine")


def run_write(pdir: pathlib.Path) -> dict:
    """Read the brief from `pdir`, run Marlow's engine, stamp + write script.json.

    Returns the stamped script dict (with `schema_version`). The caller validates it
    against the frozen contract (the pipeline does this per-stage; the adapter below
    does it explicitly for the conversational path).
    """
    from contracts import CONTRACT_VERSION
    pdir = pathlib.Path(pdir)
    brief = chat_state.load_json(pdir / "research_brief.json", {})
    # Atlas's fix re-run leaves a revision hint in project.json; fold it into the brief so
    # Marlow re-grounds/drops the flagged claims instead of regenerating the same script.
    revision = chat_state.load_json(pdir / "project.json", {}).get("revision") or {}
    hint = revision.get("hint")
    if hint:
        brief = {**brief, "revision_hint": hint}
    # The director's creative treatment (if the treatment stage ran) shapes the script's
    # rhythm + emphasis; absent, Marlow writes exactly as before (backward-compatible).
    treatment = chat_state.load_json(pdir / "creative_treatment.json", {}) or None
    # The emotional score (if the narrative_intent stage ran) makes the per-scene emotion
    # + pacing a hard writing instruction; absent, Marlow writes as before.
    narrative_intent = chat_state.load_json(pdir / "narrative_intent.json", {}) or None
    # The motion mood board (if the design-first stage ran) governs the per-beat pacing,
    # duration target, and layout the script writes to; absent, Marlow writes as before.
    motion_mood_board = chat_state.load_json(pdir / "motion_mood_board.json", {}) or None
    # The Creative Roundtable (Marlow's internal Critic→Researcher→Craftsman review) runs
    # on the LIVE path by default; `project_dir` lets the engine drop roundtable_log.json
    # beside the script for the CEO + eval system. Kill switch: MARLOW_ROUNDTABLE=0.
    use_roundtable = os.environ.get("MARLOW_ROUNDTABLE", "1").strip().lower() not in (
        "0", "false", "no", "off")
    script = _script_engine().write_script(brief, treatment=treatment,
                                           narrative_intent=narrative_intent,
                                           motion_mood_board=motion_mood_board,
                                           use_roundtable=use_roundtable,
                                           project_dir=pdir)
    script = {"schema_version": CONTRACT_VERSION, **script}
    chat_state.atomic_write_json(pdir / "script.json", script)
    # Pipeline awareness: surface that the script was roundtable-enhanced (the log is the
    # CEO/eval record of what the Critic flagged and the Craftsman changed).
    log = chat_state.load_json(pdir / "roundtable_log.json", {})
    if log:
        diff = log.get("diff_summary", {})
        note = "no changes" if log.get("error") else \
            f"{diff.get('scenes_modified', 0)} scene(s) rewritten"
        print(f"  · Script enhanced by Marlow's Creative Roundtable — {note}.")
    return script


def _digest(script: dict, limit: int = 12) -> str:
    """Compact digest for the Showrunner to surface: through-line, hook, the shape."""
    n_claims = sum(len(s.get("claims", [])) for s in script.get("scenes", []))
    lines = [f"Script drafted: \"{script.get('working_title', '(untitled)')}\" — "
             f"{script.get('total_scenes', 0)} scenes, "
             f"~{script.get('est_runtime_sec', 0)}s, {n_claims} sourced claims.",
             f"Hook: {script.get('hook', '(none)')}"]
    for s in script.get("scenes", [])[:limit]:
        nc = len(s.get("claims", []))
        cite = f" [{nc} sourced]" if nc else ""
        lines.append(f"  {s.get('scene_no')}. ({s.get('beat', 'point')}) "
                     f"{s.get('point', '')}{cite}")
    if script.get("cta"):
        lines.append(f"Close: {script['cta']}")
    lines.append("Every claim is tagged to a brief source — ready for the fact-check.")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Pipeline producer (the real script stage worker; signature (pdir, topic))
# ----------------------------------------------------------------------
def produce_script(pdir: pathlib.Path, topic: str):
    """REAL producer: Marlow's engine drafts script.json from the on-disk brief.

    Replaces the stub producer for the pipeline's `script` stage. Reads
    research_brief.json from `pdir`, runs the engine, writes a stamped script.json
    (the pipeline validates it against the frozen contract), and returns the Artifact
    the spine records.
    """
    from adapters.stubs import Artifact  # lazy: avoid an import cycle
    script = run_write(pdir)
    n_claims = sum(len(s.get("claims", [])) for s in script.get("scenes", []))
    return Artifact("script.json", "script", script,
                    f"{script.get('total_scenes', 0)} scenes, {n_claims} sourced claims, "
                    f"~{script.get('est_runtime_sec', 0)}s")


class ScriptwriterAdapter(Adapter):
    module_name = "script_engine"   # scriptwriter/script_engine.py

    def run_job(self, job_name: str, progress, **params) -> dict:
        if job_name != "write_script":
            return {"ok": False, "text": f"Marlow has no job named {job_name!r}."}

        import projects
        from contracts import validate
        who = self.entry.display
        topic = (params.get("topic") or "").strip()
        slug = (params.get("slug") or "").strip()

        pdir = self.resolve_pdir(slug)
        if pdir is None or not (pdir / "research_brief.json").exists():
            msg = ("No project with a research brief to script. Start a project "
                   "(start_project) and run research first, then pass that slug.")
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg}

        if progress is not None:
            progress.start(self.entry.emoji, who, "drafting the script", topic or slug)
        try:
            script = run_write(pdir)
        except Exception as exc:  # an unusable brief / ungroundable draft, said plainly
            if progress is not None:
                progress.fail(who, str(exc))
            return {"ok": False, "text": str(exc)}

        ok, errors = validate("script", script)
        if not ok:
            msg = f"Script failed contract validation: {'; '.join(errors)}"
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg, "saved": str(pdir / "script.json")}
        projects.mark_artifact(slug, "script", pdir / "script.json")
        if progress is not None:
            progress.done(who, "finished the script")
        return {"ok": True, "text": _digest(script), "topic": topic, "slug": slug,
                "saved": str(pdir / "script.json")}
