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
    script = _script_engine().write_script(brief, treatment=treatment)
    script = {"schema_version": CONTRACT_VERSION, **script}
    chat_state.atomic_write_json(pdir / "script.json", script)
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


# ----------------------------------------------------------------------
# Project-dir resolution for the conversational write (params stay {topic})
# ----------------------------------------------------------------------
def _resolve_project_dir(topic: str) -> pathlib.Path | None:
    """Best-effort: find the project dir a conversational write should target.

    The registry's write_script job takes only `topic` (no registry change), so we
    look under the pipeline's projects/ for a project that (a) has a research brief
    and (b) matches the topic — newest first. Returns None if none is usable.
    """
    import pipeline  # lazy to avoid an import cycle (pipeline imports this module)
    root = pipeline.PROJECTS_DIR
    if not root.exists():
        return None
    want = pipeline._slug(topic or "")
    best: list[tuple[float, pathlib.Path]] = []
    for d in root.iterdir():
        if not (d / "research_brief.json").exists():
            continue
        proj = chat_state.load_json(d / "project.json", {})
        ptopic = proj.get("topic") or proj.get("brief") or ""
        if want and (pipeline._slug(ptopic) == want or want in d.name):
            best.append((proj.get("updated", 0) or 0, d))
    if not best:
        return None
    best.sort(reverse=True)
    return best[0][1]


class ScriptwriterAdapter(Adapter):
    module_name = "script_engine"   # scriptwriter/script_engine.py

    def run_job(self, job_name: str, progress, **params) -> dict:
        if job_name != "write_script":
            return {"ok": False, "text": f"Marlow has no job named {job_name!r}."}

        from contracts import validate
        who = self.entry.display
        topic = (params.get("topic") or "").strip()

        pdir = _resolve_project_dir(topic)
        if pdir is None:
            msg = (f"Couldn't find a project with a research brief to script for "
                   f"{topic!r}. Run research (or the pipeline) first.")
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg}

        if progress is not None:
            progress.start(self.entry.emoji, who, "drafting the script", topic)
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
        if progress is not None:
            progress.done(who, "finished the script")
        return {"ok": True, "text": _digest(script), "topic": topic,
                "saved": str(pdir / "script.json")}
