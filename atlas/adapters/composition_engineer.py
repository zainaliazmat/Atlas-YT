"""Adapter for Mason (composition_engineer) — the Composition Engineer.

TWO real jobs, reading the project's upstream artifacts (script, style_guide,
storyboard, asset_manifest, narration transcript):
- compose_scenes(topic) -> composition_manifest.json  (per-scene HyperFrames HTML +
  the self-scan/lint/validate/inspect auto-gate + per-scene draft renders)
- render_video(topic)   -> video.mp4  (final assembly: concat scene renders + the
  storyboard transitions at boundaries + narration mux), AFTER the human gate.

Mason BUILDS to spec — he never redesigns (that's Iris). The composition AUTO-gate
(no render until self-scan + lint + validate + inspect pass) is deterministic inside
his engine; the [y/N] JOB gate lives in his REPL; the final-render HUMAN gate lives in
the pipeline. So Atlas runs these jobs gate-free through this adapter.

DECOUPLING: Mason's engine emits the composition_manifest as a plain dict and renders
via the HyperFrames CLI; it NEVER imports atlas. ATLAS owns the contract — it stamps
`schema_version` (composition_manifest stays "1.0" via contracts.version_for) and
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
# The composition engine seam (one place; tests monkeypatch this)
# ----------------------------------------------------------------------
def _engine():
    """Load Mason's `composition_engine` module (isolated, cached by the loader)."""
    import registry  # lazy: registry imports this module, so avoid a top-level cycle
    ce_dir = registry.get_entry("composition_engineer").project_dir
    return load_engine(ce_dir, "composition_engine")


def run_compose(pdir: pathlib.Path) -> dict:
    """Read the 5 artifacts from `pdir`, run Mason's engine (build + auto-gate + draft
    renders), stamp + write composition_manifest.json. Returns the stamped manifest.

    The caller validates it against the frozen contract (the pipeline does this
    per-stage; the adapter below does it explicitly for the conversational path).
    """
    from contracts import version_for
    pdir = pathlib.Path(pdir)
    manifest = _engine().compose(pdir)
    manifest = {"schema_version": version_for("composition_manifest"), **manifest}
    chat_state.atomic_write_json(pdir / "composition_manifest.json", manifest)
    return manifest


def run_render(pdir: pathlib.Path) -> dict:
    """Read composition_manifest.json (+ storyboard + audio) from `pdir`, run Mason's
    final assembly, returns the engine result dict ({ok, video, ...})."""
    pdir = pathlib.Path(pdir)
    return _engine().run_render(pdir)


def _compose_summary(manifest: dict) -> str:
    """The pipeline's auto-gate checks for 'auto-gate PASS' in this string."""
    s = manifest.get("summary", {})
    gate = "auto-gate PASS" if manifest.get("verdict") == "pass" else "auto-gate FAIL"
    extra = ""
    if s.get("integrity_flags"):
        extra += f"; {s['integrity_flags']} asset integrity flag(s) for the human gate"
    if s.get("contrast_failures"):
        extra += f"; {s['contrast_failures']} WCAG warning(s)"
    return (f"{gate} — {s.get('gated_ok', 0)}/{s.get('total', 0)} scenes "
            f"lint+validate+inspect clean; {s.get('rendered', 0)} draft(s){extra}")


def _compose_digest(manifest: dict) -> str:
    lines = [_compose_summary(manifest)]
    for sc in manifest.get("scenes", [])[:12]:
        ok = (sc["self_scan"]["ok"] and
              all((sc["gate"][k] or {}).get("ok", False) for k in ("lint", "validate", "inspect")))
        tag = "✓" if ok else "✗"
        fx = ", ".join(sc.get("effects", [])) or "—"
        star = "  ★" if sc.get("signature_beat") else ""
        lines.append(f"  {tag} scene {sc.get('scene_no')}: {sc.get('layout')} · "
                     f"{sc.get('transition')} · [{fx}] · {sc.get('render_status')}{star}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Pipeline producers (the real compose/render stage workers; (pdir, topic))
# ----------------------------------------------------------------------
def produce_compose(pdir: pathlib.Path, topic: str):
    """REAL producer: Mason's engine composes + auto-gates + draft-renders the scenes.

    Returns an Artifact whose `data` is the composition_manifest (validated by the
    pipeline against the frozen contract) and whose `summary` carries 'auto-gate PASS'
    so the pipeline's autogate check fires correctly.
    """
    from adapters.stubs import Artifact  # lazy: avoid an import cycle
    manifest = run_compose(pdir)
    return Artifact("composition_manifest.json", "composition_manifest", manifest,
                    _compose_summary(manifest))


def produce_render(pdir: pathlib.Path, topic: str):
    """REAL producer: Mason's final assembly -> video.mp4 (binary; no contract)."""
    from adapters.stubs import Artifact  # lazy: avoid an import cycle
    result = run_render(pdir)
    if not result.get("ok"):
        raise RuntimeError(f"final assembly failed: {result.get('error')}")
    tag = " (skipped — MASON_SKIP_RENDER)" if result.get("skipped") else ""
    return Artifact(result.get("video", "video.mp4"), None, None,
                    f"final video assembled{tag}")


# ----------------------------------------------------------------------
class CompositionEngineerAdapter(Adapter):
    module_name = "composition_engine"   # composition-engineer/composition_engine.py

    # job name -> (runner, contract|None, digest, needs-artifact, manifest-name, doing)
    _JOBS = {
        "compose_scenes": (run_compose, "composition_manifest", _compose_digest,
                           "storyboard.json", "composition", "composing the scenes"),
        "render_video": (run_render, None, None, "composition_manifest.json",
                         "render", "assembling the final video"),
    }

    def run_job(self, job_name: str, progress, **params) -> dict:
        spec = self._JOBS.get(job_name)
        if spec is None:
            return {"ok": False, "text": f"Mason has no job named {job_name!r}."}
        runner, contract, digest, needs, manifest_name, doing = spec

        import projects
        from contracts import validate
        who = self.entry.display
        topic = (params.get("topic") or "").strip()
        slug = (params.get("slug") or "").strip()

        pdir = self.resolve_pdir(slug)
        if pdir is None or not (pdir / needs).exists():
            msg = (f"No project with {needs} to {doing}. Run the upstream steps for this "
                   "slug first.")
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg}

        if progress is not None:
            progress.start(self.entry.emoji, who, doing, topic or slug)
        try:
            result = runner(pdir)
        except Exception as exc:  # bad inputs / gate-block / toolchain, said plainly
            if progress is not None:
                progress.fail(who, str(exc))
            return {"ok": False, "text": str(exc)}

        # compose: validate the manifest at the boundary; render: binary (no contract)
        if contract is not None:
            ok, errors = validate(contract, result)
            if not ok:
                msg = f"{contract} failed contract validation: {'; '.join(errors)}"
                if progress is not None:
                    progress.fail(who, msg)
                return {"ok": False, "text": msg,
                        "saved": str(pdir / f"{contract}.json")}
            text = digest(result)
            saved = str(pdir / f"{contract}.json")
        else:
            text = f"Final video assembled: {result.get('video')}." \
                if result.get("ok") else f"Assembly failed: {result.get('error')}"
            if not result.get("ok"):
                if progress is not None:
                    progress.fail(who, result.get("error", "assembly failed"))
                return {"ok": False, "text": text}
            saved = str(pdir / result.get("video", "video.mp4"))

        projects.mark_artifact(slug, manifest_name, saved)
        if progress is not None:
            progress.done(who, f"finished {doing}")
        return {"ok": True, "text": text, "topic": topic, "slug": slug, "saved": saved}
