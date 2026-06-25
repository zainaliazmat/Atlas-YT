"""Adapter for Cadence (audio) — the Audio / Sound Designer.

TWO real jobs, reading the project's upstream artifacts (script, style_guide,
storyboard, and — for the mix — the narration transcript Cadence herself wrote):
- record_narration(topic) -> narration.wav + narration.transcript.json (per-scene tts
  -> lossless concat -> deterministic per-scene timing; the downstream timing authority)
- mix_audio(topic)        -> master.wav + audio_manifest.json (source a cleared bed,
  place the one signature SFX accent, pre-mix the documentary master, emit the manifest)

Cadence sits late: `record_narration` runs in parallel with the Asset Sourcer (before
compose); `mix_audio` runs after compose, before the final-render human gate. Cadence
is GATE-FREE as a pipeline stage — the [y/N] gate lives in her REPL; the before-render
human gate is Atlas's, in the pipeline. So Atlas runs these jobs gate-free here.

THE MASTER-BRIDGE (decoupling, no Composition Engineer edits): the renderer muxes
`tracks[role=="narration"].uri`, so Cadence points that uri at the pre-mixed master —
the documentary mix lands in the final MP4 today. `vo_uri` / `master_uri` back-reference
the pure VO + the mix for the clean follow-up (Mason muxes `master_uri` later).

DECOUPLING: Cadence's engine emits plain dicts and renders via the HyperFrames CLI +
FFmpeg; it NEVER imports atlas. ATLAS owns the contract — it stamps `schema_version`
(via contracts.version_for: narration_transcript "1.0", audio_manifest "1.1") and
validates against the frozen schema HERE, at the boundary (the pipeline does it
per-stage; the conversational path below does it explicitly).

PERSONA `ask` is inherited from base.
"""
from __future__ import annotations

import pathlib

import chat_state
from adapters.base import Adapter
from adapters.loader import load_engine


# ----------------------------------------------------------------------
# The audio engine seam (one place; tests monkeypatch this)
# ----------------------------------------------------------------------
def _engine():
    """Load Cadence's `audio_engine` module (isolated, cached by the loader)."""
    import registry  # lazy: registry imports this module, so avoid a top-level cycle
    ad_dir = registry.get_entry("audio").project_dir
    return load_engine(ad_dir, "audio_engine")


def run_record_narration(pdir: pathlib.Path) -> dict:
    """Read script.json from `pdir`, run Cadence's per-scene tts + concat, write
    narration.wav + the stamped narration.transcript.json. Returns the stamped transcript.

    The caller validates it against the frozen contract (the pipeline per-stage; the
    adapter below explicitly for the conversational path)."""
    from contracts import version_for
    pdir = pathlib.Path(pdir)
    eng = _engine()
    script = chat_state.load_json(pdir / "script.json", {})
    # The emotional score (if the narrative_intent stage ran) sets each scene's TTS pacing;
    # absent, every scene is voiced at 1.0 exactly as before (backward-compatible).
    narrative_intent = chat_state.load_json(pdir / "narrative_intent.json", {}) or None
    out = eng.record_narration(script, pdir=pdir, narrative_intent=narrative_intent)
    transcript = {"schema_version": version_for("narration_transcript"), **out["transcript"]}
    chat_state.atomic_write_json(
        pdir / "audio" / "narration.transcript.json", transcript)
    return transcript


def run_mix_audio(pdir: pathlib.Path) -> dict:
    """Read script + style_guide + storyboard + the transcript from `pdir`, source the
    bed, place the accent, pre-mix master.wav, write the stamped audio_manifest.json.
    Returns the stamped manifest."""
    from contracts import version_for
    pdir = pathlib.Path(pdir)
    eng = _engine()
    script = chat_state.load_json(pdir / "script.json", {})
    style = chat_state.load_json(pdir / "style_guide.json", {}) or None
    storyboard = chat_state.load_json(pdir / "storyboard.json", {}) or None
    transcript = chat_state.load_json(pdir / "audio" / "narration.transcript.json", {})
    if not transcript.get("segments"):
        # Defensive: if the narration stage hasn't run (conversational path), do it now.
        transcript = run_record_narration(pdir)
    # The emotional score drives the bed query, the signature SFX, and the master VO EQ.
    narrative_intent = chat_state.load_json(pdir / "narrative_intent.json", {}) or None
    res = eng.mix_audio(script, style, storyboard, transcript, pdir=pdir,
                        narrative_intent=narrative_intent)
    manifest = {"schema_version": version_for("audio_manifest"), **res["manifest"]}
    chat_state.atomic_write_json(pdir / "audio" / "audio_manifest.json", manifest)
    return manifest


# ----------------------------------------------------------------------
# Digests (LLM-friendly summaries the orchestrator narrates)
# ----------------------------------------------------------------------
def _transcript_summary(transcript: dict) -> str:
    segs = transcript.get("segments", [])
    return (f"{len(segs)} scenes voiced, {transcript.get('total_duration_sec', 0)}s total "
            f"— per-scene timing recorded (the caption/compose clock).")


def _manifest_summary(manifest: dict) -> str:
    tracks = manifest.get("tracks", [])
    by_role = {"narration": 0, "music": 0, "sfx": 0}
    for t in tracks:
        by_role[t.get("role", "?")] = by_role.get(t.get("role", "?"), 0) + 1
    mx = manifest.get("mix", {})
    master = "master rendered" if manifest.get("master_uri") else "VO-only (no master)"
    bed = mx.get("bed", "?")
    return (f"{len(tracks)} tracks ({by_role['music']} music, {by_role['sfx']} sfx) · "
            f"bed {bed} · {master} · {manifest.get('total_duration_sec', 0)}s")


def _manifest_digest(manifest: dict) -> str:
    lines = [_manifest_summary(manifest)]
    for t in manifest.get("tracks", [])[:8]:
        tag = {"cleared": "✓", "sourced": "~", "placeholder": "·"}.get(t.get("status"), "?")
        duck = f" duck:{t['ducking']}" if t.get("ducking") not in (False, None) else ""
        at = f" @{t['at_sec']}s" if t.get("at_sec") is not None else ""
        flag = f"  ⚑ {t.get('flag')}" if t.get("flag") else ""
        lines.append(f"  {tag} {t.get('role')}: {t.get('gain_db')}dB{duck}{at} · "
                     f"{t.get('license')}{flag}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Pipeline producers (the real stage workers; (pdir, topic))
# ----------------------------------------------------------------------
def produce_narration(pdir: pathlib.Path, topic: str):
    """REAL producer: Cadence voices the script -> narration.wav + transcript. Returns
    an Artifact whose `data` is the transcript (validated as narration_transcript)."""
    from adapters.stubs import Artifact  # lazy: avoid an import cycle
    transcript = run_record_narration(pdir)
    return Artifact("audio/narration.transcript.json", "narration_transcript",
                    transcript, _transcript_summary(transcript))


def produce_audiomix(pdir: pathlib.Path, topic: str):
    """REAL producer: Cadence sources the bed, places the accent, pre-mixes master.wav,
    and emits audio_manifest.json (validated as audio_manifest)."""
    from adapters.stubs import Artifact  # lazy: avoid an import cycle
    manifest = run_mix_audio(pdir)
    return Artifact("audio/audio_manifest.json", "audio_manifest", manifest,
                    _manifest_summary(manifest))


# ----------------------------------------------------------------------
# Project-dir resolution for the conversational jobs (params stay {topic})
# ----------------------------------------------------------------------
def _resolve_project_dir(topic: str, *, needs: str) -> pathlib.Path | None:
    """Best-effort: find the project dir a conversational job should target. `needs` is
    the artifact that must exist (script.json for both jobs). Newest match first."""
    import pipeline  # lazy to avoid an import cycle (pipeline imports this module)
    root = pipeline.PROJECTS_DIR
    if not root.exists():
        return None
    want = pipeline._slug(topic or "")
    best: list[tuple[float, pathlib.Path]] = []
    for d in root.iterdir():
        if not (d / needs).exists():
            continue
        proj = chat_state.load_json(d / "project.json", {})
        ptopic = proj.get("topic") or proj.get("brief") or ""
        if want and (pipeline._slug(ptopic) == want or want in d.name):
            best.append((proj.get("updated", 0) or 0, d))
    if not best:
        return None
    best.sort(reverse=True)
    return best[0][1]


class AudioAdapter(Adapter):
    module_name = "audio_engine"   # audio-designer/audio_engine.py

    # job name -> (runner, contract, digest, needs-artifact, what she's doing)
    _JOBS = {
        "record_narration": (run_record_narration, "narration_transcript",
                             _transcript_summary, "script.json", "recording the narration"),
        "mix_audio": (run_mix_audio, "audio_manifest", _manifest_digest,
                      "script.json", "mixing the audio"),
    }

    def run_job(self, job_name: str, progress, **params) -> dict:
        spec = self._JOBS.get(job_name)
        if spec is None:
            return {"ok": False, "text": f"Cadence has no job named {job_name!r}."}
        runner, contract, digest, needs, doing = spec

        from contracts import validate
        who = self.entry.display
        topic = (params.get("topic") or "").strip()

        pdir = _resolve_project_dir(topic, needs=needs)
        if pdir is None:
            msg = (f"Couldn't find a project with {needs} to {doing} for {topic!r}. "
                   "Run the upstream stages (or the pipeline) first.")
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg}

        if progress is not None:
            progress.start(self.entry.emoji, who, doing, topic)
        try:
            result = runner(pdir)
        except Exception as exc:  # bad inputs / toolchain, said plainly
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
        saved = ("audio/narration.transcript.json" if contract == "narration_transcript"
                 else "audio/audio_manifest.json")
        return {"ok": True, "text": digest(result), "topic": topic,
                "saved": str(pdir / saved)}
