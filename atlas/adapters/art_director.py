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
    treatment = chat_state.load_json(pdir / "creative_treatment.json", {}) or None
    style = _art_engine().design_style(script, treatment=treatment)
    style = {"schema_version": version_for("style_guide"), **style}
    chat_state.atomic_write_json(pdir / "style_guide.json", style)
    return style


def run_design_treatment(pdir: pathlib.Path) -> dict:
    """Read research_brief.json from `pdir`, run Iris's engine, stamp + write
    creative_treatment.json. Returns the stamped treatment dict. Runs AFTER research and
    BEFORE the script; Marlow + Iris's later stages consume it."""
    from contracts import version_for
    pdir = pathlib.Path(pdir)
    brief = chat_state.load_json(pdir / "research_brief.json", {})
    treatment = _art_engine().design_treatment(brief)
    treatment = {"schema_version": version_for("creative_treatment"), **treatment}
    chat_state.atomic_write_json(pdir / "creative_treatment.json", treatment)
    return treatment


def run_design_narrative_intent(pdir: pathlib.Path) -> dict:
    """Read creative_treatment.json (+ research_brief.json) from `pdir`, run Iris's engine,
    stamp + write narrative_intent.json. Returns the stamped intent dict. Runs AFTER the
    treatment and BEFORE the script; Marlow (word/sentence) + Cadence (TTS/EQ/music/SFX)
    consume it. Backward-compatible: absent treatment leaves downstream on prior behavior."""
    from contracts import version_for
    pdir = pathlib.Path(pdir)
    treatment = chat_state.load_json(pdir / "creative_treatment.json", {})
    brief = chat_state.load_json(pdir / "research_brief.json", {})
    intent = _art_engine().design_narrative_intent(treatment, brief)
    intent = {"schema_version": version_for("narrative_intent"), **intent}
    chat_state.atomic_write_json(pdir / "narrative_intent.json", intent)
    return intent


def run_design_motion_mood_board(pdir: pathlib.Path) -> dict:
    """Read narrative_intent.json (+ research_brief.json for the thematic anchor, +
    style_guide.json for the palette if it exists yet) from `pdir`, run Iris's engine,
    stamp + write motion_mood_board.json. Returns the stamped board dict. Runs AFTER the
    narrative_intent and BEFORE the script; Marlow (pacing) + Mason (motion) consume it.
    Backward-compatible: the style guide need not exist yet (the board works from the
    intent alone)."""
    from contracts import version_for
    pdir = pathlib.Path(pdir)
    intent = chat_state.load_json(pdir / "narrative_intent.json", {})
    brief = chat_state.load_json(pdir / "research_brief.json", {})
    style_guide = chat_state.load_json(pdir / "style_guide.json", {})  # may be {} this early
    anchor = brief.get("thematic_anchor", {}) if isinstance(brief, dict) else {}
    board = _art_engine().design_motion_mood_board(intent, anchor, style_guide)
    board = {"schema_version": version_for("motion_mood_board"), **board}
    chat_state.atomic_write_json(pdir / "motion_mood_board.json", board)
    return board


def run_build_storyboard(pdir: pathlib.Path) -> dict:
    """Read script.json (+ the on-disk style_guide.json) from `pdir`, run Iris's engine,
    stamp + write storyboard.json. Returns the stamped storyboard dict."""
    from contracts import version_for
    pdir = pathlib.Path(pdir)
    script = chat_state.load_json(pdir / "script.json", {})
    style_guide = chat_state.load_json(pdir / "style_guide.json", {}) or None
    treatment = chat_state.load_json(pdir / "creative_treatment.json", {}) or None
    board = _art_engine().build_storyboard(script, style_guide, treatment=treatment)
    board = {"schema_version": version_for("storyboard"), **board}
    chat_state.atomic_write_json(pdir / "storyboard.json", board)
    return board


def _treatment_digest(t: dict) -> str:
    beats = t.get("beats", [])
    lines = [f"Creative treatment: rhythm {t.get('rhythm') or '—'}; "
             f"world: {(t.get('visual_world') or '—')[:80]}; {len(beats)} beats."]
    if t.get("emphasis"):
        lines.append(f"  The one idea to land: {t['emphasis'][:100]}")
    for b in beats[:8]:
        lines.append(f"  · {b.get('beat', '?')}: {(b.get('concept') or '')[:70]} "
                     f"[lands '{b.get('emphasis_word', '')}']")
    return "\n".join(lines)


def _intent_digest(intent: dict) -> str:
    vl = intent.get("video_level", {})
    arc = intent.get("emotional_arc", {})
    scenes = intent.get("per_scene_intent", [])
    lines = [f"Narrative intent set: tone {vl.get('tone_profile') or '—'}; "
             f"{len(scenes)} scenes scored.",
             f"  Thesis: {(vl.get('core_thesis') or '—')[:100]}",
             f"  Journey: {(vl.get('emotional_journey') or '—')[:100]}"]
    arc_bits = [f"{p}:{(arc.get(p) or {}).get('dominant_emotion', '—')}"
                f"@{(arc.get(p) or {}).get('intensity', '?')}"
                for p in ("hook", "build", "peak", "breathe", "cta")]
    lines.append("  Arc: " + " → ".join(arc_bits))
    return "\n".join(lines)


def _mood_board_digest(board: dict) -> str:
    vl = board.get("video_level", {})
    beats = board.get("beat_map", [])
    sig = board.get("signature_beat_placement", {}).get("beat_id") or "?"
    lines = [f"Motion mood board set: tempo {vl.get('global_tempo') or '—'}, "
             f"texture {vl.get('global_texture') or '—'}; {len(beats)} beats; "
             f"signature #FFD000 on beat {sig}."]
    if vl.get("dominant_motion_philosophy"):
        lines.append(f"  Philosophy: {vl['dominant_motion_philosophy'][:100]}")
    for b in beats[:8]:
        sec = b.get("secondary_effect")
        sec_str = f"+{sec}" if sec and sec != "none" else ""
        lines.append(f"  · {b.get('beat_id', '?')} ({b.get('arc_phase', '?')}): "
                     f"{b.get('pacing_profile', '?')} · {b.get('dominant_effect', '?')}{sec_str} "
                     f"· {b.get('transition_in', '?')} · {b.get('layout_family', '?')}")
    return "\n".join(lines)


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
def produce_treatment(pdir: pathlib.Path, topic: str):
    """REAL producer: Iris's engine writes creative_treatment.json from the research brief."""
    from adapters.stubs import Artifact  # lazy: avoid an import cycle
    treatment = run_design_treatment(pdir)
    n = len(treatment.get("beats", []))
    rhythm = treatment.get("rhythm") or "—"
    return Artifact("creative_treatment.json", "creative_treatment", treatment,
                    f"creative direction set; rhythm {rhythm}; {n} beats")


def produce_narrative_intent(pdir: pathlib.Path, topic: str):
    """REAL producer: Iris's engine writes narrative_intent.json — the emotional score that
    translates the creative_treatment into closed-vocabulary parameters Marlow + Cadence act
    on. Runs between treatment and script."""
    from adapters.stubs import Artifact  # lazy: avoid an import cycle
    intent = run_design_narrative_intent(pdir)
    vl = intent.get("video_level", {})
    n = len(intent.get("per_scene_intent", []))
    return Artifact("narrative_intent.json", "narrative_intent", intent,
                    f"emotional score set; tone {vl.get('tone_profile') or '—'}; "
                    f"{n} scenes scored")


def produce_motion_mood_board(pdir: pathlib.Path, topic: str):
    """REAL producer: Iris's engine writes motion_mood_board.json — the design-first visual
    architecture that governs BOTH Marlow's pacing AND Mason's motion. Runs between
    narrative_intent and script."""
    from adapters.stubs import Artifact  # lazy: avoid an import cycle
    board = run_design_motion_mood_board(pdir)
    vl = board.get("video_level", {})
    n = len(board.get("beat_map", []))
    return Artifact("motion_mood_board.json", "motion_mood_board", board,
                    f"motion architecture set; tempo {vl.get('global_tempo') or '—'}; "
                    f"{n} beats; signature #FFD000 placed")


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


class ArtDirectorAdapter(Adapter):
    module_name = "art_engine"   # art-director/art_engine.py

    # job name -> (runner, contract, digest builder, what she's doing)
    _JOBS = {
        "design_treatment": (run_design_treatment, "creative_treatment", _treatment_digest,
                             "writing the creative treatment"),
        "design_narrative_intent": (run_design_narrative_intent, "narrative_intent",
                                    _intent_digest, "scoring the narrative intent"),
        "design_motion_mood_board": (run_design_motion_mood_board, "motion_mood_board",
                                     _mood_board_digest, "designing the motion mood board"),
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

        import projects
        from contracts import validate
        who = self.entry.display
        topic = (params.get("topic") or "").strip()
        slug = (params.get("slug") or "").strip()

        # The creative-architecture jobs read the brief; style/storyboard read the script.
        needs = "research_brief.json" if job_name in (
            "design_treatment", "design_narrative_intent", "design_motion_mood_board") \
            else "script.json"
        pdir = self.resolve_pdir(slug)
        if pdir is None or not (pdir / needs).exists():
            msg = (f"No project with a {needs} to design from. Run the upstream step for "
                   "this slug first.")
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg}

        # build_storyboard depends on the style guide — design it first if absent.
        if job_name == "build_storyboard" and not (pdir / "style_guide.json").exists():
            if progress is not None:
                progress.start(self.entry.emoji, who, "designing the style first", topic or slug)
            try:
                run_design_style(pdir)
            except Exception as exc:  # noqa: BLE001
                if progress is not None:
                    progress.fail(who, str(exc))
                return {"ok": False, "text": str(exc)}
            projects.mark_artifact(slug, "style_guide", pdir / "style_guide.json")

        if progress is not None:
            progress.start(self.entry.emoji, who, doing, topic or slug)
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
        projects.mark_artifact(slug, contract, pdir / f"{contract}.json")
        if progress is not None:
            progress.done(who, f"finished {doing}")
        return {"ok": True, "text": digest(result), "topic": topic, "slug": slug,
                "saved": str(pdir / f"{contract}.json")}
