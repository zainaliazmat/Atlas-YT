"""Adapter for Iris (art_director) — the Art Director.

TWO real jobs, both read the project's fact-checked script.json:
- design_style(topic)    -> style_guide.json  (palette, type, motion, textures, fps)
- build_storyboard(topic) -> storyboard.json  (one planned scene per script scene)

Iris SPECIFIES; she never implements — the Composition Engineer (#6) builds the
HTML/CSS/GSAP from these specs, and the Asset Sourcer (#5) resolves the assets Iris
only references by `asset_ref` + a content description.

DECOUPLING: Iris's engine emits the two specs as plain dicts in the frozen shapes and
NEVER imports atlas. ATLAS owns the contract — it stamps `schema_version` (the BUMPED
"1.1" via contracts.version_for, since Iris added render-detail fields) and validates
against the frozen schemas HERE, at the boundary (the pipeline does it per-stage; the
conversational path below does it explicitly).

NO GATE HERE. The [y/N] approval gate lives in Iris's REPL (art-director/chat.py), not
in run_job — so Atlas runs these jobs gate-free from the meeting room.

PERSONA `ask` is inherited from base.
"""
from __future__ import annotations

import pathlib

import chat_state
from adapters.base import Adapter
from adapters.loader import load_engine


# ----------------------------------------------------------------------
# The art engine seam (one place; tests monkeypatch this)
# ----------------------------------------------------------------------
def _art_engine():
    """Load Iris's `art_engine` module (isolated, cached by the loader)."""
    import registry  # lazy: registry imports this module, so avoid a top-level cycle
    ad_dir = registry.get_entry("art_director").project_dir
    return load_engine(ad_dir, "art_engine")


def run_design_style(pdir: pathlib.Path) -> dict:
    """Read script.json from `pdir`, run Iris's engine, stamp + write style_guide.json.

    Returns the stamped style_guide dict. The caller validates it against the frozen
    contract (the pipeline does this per-stage; the adapter below does it explicitly
    for the conversational path).
    """
    from contracts import version_for
    pdir = pathlib.Path(pdir)
    script = chat_state.load_json(pdir / "script.json", {})
    style = _art_engine().design_style(script)
    style = {"schema_version": version_for("style_guide"), **style}
    chat_state.atomic_write_json(pdir / "style_guide.json", style)
    return style


def run_build_storyboard(pdir: pathlib.Path) -> dict:
    """Read script.json (+ the on-disk style_guide.json) from `pdir`, run Iris's engine,
    stamp + write storyboard.json. Returns the stamped storyboard dict."""
    from contracts import version_for
    pdir = pathlib.Path(pdir)
    script = chat_state.load_json(pdir / "script.json", {})
    style_guide = chat_state.load_json(pdir / "style_guide.json", {}) or None
    board = _art_engine().build_storyboard(script, style_guide)
    board = {"schema_version": version_for("storyboard"), **board}
    chat_state.atomic_write_json(pdir / "storyboard.json", board)
    return board


def _style_digest(style: dict) -> str:
    p = style.get("palette", {})
    accents = ", ".join(p.get("accents", []) or []) or "none"
    tex = ", ".join(t.get("name") for t in style.get("textures", [])) or "none"
    return (f"Style set: primary {p.get('primary')}, bg {p.get('bg')}; accents [{accents}] "
            f"+ the {p.get('signature_highlight')} signature; {style.get('fps')}fps; "
            f"budget {style.get('motion', {}).get('max_per_scene')}/scene; textures: {tex}.")


def _board_digest(board: dict) -> str:
    sig = next((s.get("scene_no") for s in board.get("scenes", [])
                if s.get("signature_beat")), None)
    lines = [f"Storyboard: {board.get('total_scenes', 0)} scenes; the signature "
             f"#FFD000 beat lands on scene {sig}."]
    for s in board.get("scenes", [])[:12]:
        fx = ", ".join(e.get("name") for e in s.get("effects", [])) or "—"
        star = "  ★" if s.get("signature_beat") else ""
        lines.append(f"  {s.get('scene_no')}. {s.get('layout')} · {s.get('transition')} "
                     f"· [{fx}]{star}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Pipeline producers (the real style/storyboard stage workers; (pdir, topic))
# ----------------------------------------------------------------------
def produce_style(pdir: pathlib.Path, topic: str):
    """REAL producer: Iris's engine designs style_guide.json from the on-disk script."""
    from adapters.stubs import Artifact  # lazy: avoid an import cycle
    style = run_design_style(pdir)
    n_tex = len(style.get("textures", []))
    return Artifact("style_guide.json", "style_guide", style,
                    f"palette set; {style.get('fps')}fps; {n_tex} textures; "
                    "signature #FFD000 reserved")


def produce_storyboard(pdir: pathlib.Path, topic: str):
    """REAL producer: Iris's engine boards storyboard.json from script + style guide."""
    from adapters.stubs import Artifact  # lazy: avoid an import cycle
    board = run_build_storyboard(pdir)
    sig = next((s.get("scene_no") for s in board.get("scenes", [])
                if s.get("signature_beat")), None)
    return Artifact("storyboard.json", "storyboard", board,
                    f"{board.get('total_scenes', 0)} scenes; signature beat on scene {sig}")


# ----------------------------------------------------------------------
# Project-dir resolution for the conversational jobs (params stay {topic})
# ----------------------------------------------------------------------
def _resolve_project_dir(topic: str) -> pathlib.Path | None:
    """Best-effort: find the project dir a conversational job should target.

    Both jobs take only `topic` (no registry change), so we look under the pipeline's
    projects/ for a project that has a script.json and matches the topic — newest
    first. Returns None if none is usable.
    """
    import pipeline  # lazy to avoid an import cycle (pipeline imports this module)
    root = pipeline.PROJECTS_DIR
    if not root.exists():
        return None
    want = pipeline._slug(topic or "")
    best: list[tuple[float, pathlib.Path]] = []
    for d in root.iterdir():
        if not (d / "script.json").exists():
            continue
        proj = chat_state.load_json(d / "project.json", {})
        ptopic = proj.get("topic") or proj.get("brief") or ""
        if want and (pipeline._slug(ptopic) == want or want in d.name):
            best.append((proj.get("updated", 0) or 0, d))
    if not best:
        return None
    best.sort(reverse=True)
    return best[0][1]


class ArtDirectorAdapter(Adapter):
    module_name = "art_engine"   # art-director/art_engine.py

    # job name -> (runner, contract, digest builder, what she's doing)
    _JOBS = {
        "design_style": (run_design_style, "style_guide", _style_digest,
                         "designing the style"),
        "build_storyboard": (run_build_storyboard, "storyboard", _board_digest,
                             "building the storyboard"),
    }

    def run_job(self, job_name: str, progress, **params) -> dict:
        spec = self._JOBS.get(job_name)
        if spec is None:
            return {"ok": False, "text": f"Iris has no job named {job_name!r}."}
        runner, contract, digest, doing = spec

        from contracts import validate
        who = self.entry.display
        topic = (params.get("topic") or "").strip()

        pdir = _resolve_project_dir(topic)
        if pdir is None:
            msg = (f"Couldn't find a project with a script to design for {topic!r}. "
                   "Run the script (or the pipeline) first.")
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg}

        # build_storyboard depends on the style guide — design it first if absent.
        if job_name == "build_storyboard" and not (pdir / "style_guide.json").exists():
            if progress is not None:
                progress.start(self.entry.emoji, who, "designing the style first", topic)
            try:
                run_design_style(pdir)
            except Exception as exc:  # noqa: BLE001
                if progress is not None:
                    progress.fail(who, str(exc))
                return {"ok": False, "text": str(exc)}

        if progress is not None:
            progress.start(self.entry.emoji, who, doing, topic)
        try:
            result = runner(pdir)
        except Exception as exc:  # an unusable script, said plainly
            if progress is not None:
                progress.fail(who, str(exc))
            return {"ok": False, "text": str(exc)}

        ok, errors = validate(contract, result)
        if not ok:
            msg = f"{contract} failed contract validation: {'; '.join(errors)}"
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg, "saved": str(pdir / f"{contract}.json")}
        if progress is not None:
            progress.done(who, f"finished {doing}")
        return {"ok": True, "text": digest(result), "topic": topic,
                "saved": str(pdir / f"{contract}.json")}
