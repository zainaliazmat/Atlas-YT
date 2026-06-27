"""Adapter for Magpie (asset_sourcer) — the Asset Sourcer & Licensing specialist.

ONE real job, reads the project's storyboard.json (+ style_guide.json):
- source_assets(topic) -> asset_manifest.json  (every shot's asset, with its license)

Magpie sits after the Art Director, in parallel with Audio. She reads the storyboard's
`shots[].asset_ref` + `content`, sources a provably-reusable asset from an allowlist of
public-domain / CC archives, downloads it LOCAL (HyperFrames forbids render-time
fetches), and records source + license + attribution + status. When a shot can't be
cleared she ships a flagged LOCAL placeholder — never an unlicensed asset.

DECOUPLING: Magpie's engine emits the manifest as a plain dict in the frozen shape and
NEVER imports atlas. ATLAS owns the contract — it stamps `schema_version`
(asset_manifest stays "1.0" via contracts.version_for) and validates against the frozen
schema HERE, at the boundary (the pipeline does it per-stage; the conversational path
below does it explicitly).

NO GATE HERE. There is no [y/N] gate in run_job — the gate lives in Magpie's REPL
(asset-sourcer/chat.py), so Atlas runs this job gate-free from the meeting room.

PERSONA `ask` is inherited from base.
"""
from __future__ import annotations

import pathlib

import chat_state
from adapters.base import Adapter
from adapters.loader import load_engine


# ----------------------------------------------------------------------
# The source engine seam (one place; tests monkeypatch this)
# ----------------------------------------------------------------------
def _source_engine():
    """Load Magpie's `source_engine` module (isolated, cached by the loader)."""
    import registry  # lazy: registry imports this module, so avoid a top-level cycle
    as_dir = registry.get_entry("asset_sourcer").project_dir
    return load_engine(as_dir, "source_engine")


def run_source_assets(pdir: pathlib.Path) -> dict:
    """Read storyboard.json (+ style_guide.json) from `pdir`, run Magpie's engine,
    stamp + write asset_manifest.json. Returns the stamped manifest dict.

    The caller validates it against the frozen contract (the pipeline does this
    per-stage; the adapter below does it explicitly for the conversational path).
    """
    from contracts import version_for
    pdir = pathlib.Path(pdir)
    eng = _source_engine()
    storyboard = chat_state.load_json(pdir / "storyboard.json", {})
    style_guide = chat_state.load_json(pdir / "style_guide.json", {}) or None
    client = eng.sources.SourceClient()   # the network seam (graceful when offline)
    manifest = eng.source_assets(storyboard, style_guide, client=client, pdir=pdir)
    manifest = {"schema_version": version_for("asset_manifest"), **manifest}
    chat_state.atomic_write_json(pdir / "asset_manifest.json", manifest)
    return manifest


def _manifest_stats(manifest: dict) -> dict:
    assets = manifest.get("assets", [])
    by = {"cleared": 0, "sourced": 0, "placeholder": 0}
    for a in assets:
        s = a.get("status", "placeholder")
        by[s] = by.get(s, 0) + 1
    return {"total": len(assets), **by}


def _manifest_digest(manifest: dict) -> str:
    st = _manifest_stats(manifest)
    lines = [f"Sourced {st['total']} assets — {st['cleared']} cleared, "
             f"{st['sourced']} sourced (licensed, clearance incomplete), "
             f"{st['placeholder']} placeholder."]
    for a in manifest.get("assets", [])[:12]:
        tag = {"cleared": "✓", "sourced": "~", "placeholder": "·"}.get(a.get("status"), "?")
        flag = f"  ⚑ {a.get('flag')}" if a.get("flag") else ""
        lines.append(f"  {tag} {a.get('asset_id')} (sc{a.get('scene_no')}) "
                     f"{a.get('type')} · {a.get('source')} · {a.get('license')}{flag}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Pipeline producer (the real assets stage worker; (pdir, topic))
# ----------------------------------------------------------------------
def produce_assets(pdir: pathlib.Path, topic: str):
    """REAL producer: Magpie's engine sources asset_manifest.json from the on-disk
    storyboard (+ style guide)."""
    from adapters.stubs import Artifact  # lazy: avoid an import cycle
    manifest = run_source_assets(pdir)
    st = _manifest_stats(manifest)
    return Artifact("asset_manifest.json", "asset_manifest", manifest,
                    f"{st['total']} assets — {st['cleared']} cleared, "
                    f"{st['sourced']} sourced, {st['placeholder']} placeholder")


class AssetSourcerAdapter(Adapter):
    module_name = "source_engine"   # asset-sourcer/source_engine.py

    def run_job(self, job_name: str, progress, **params) -> dict:
        if job_name != "source_assets":
            return {"ok": False, "text": f"Magpie has no job named {job_name!r}."}

        import projects
        from contracts import validate
        who = self.entry.display
        topic = (params.get("topic") or "").strip()
        slug = (params.get("slug") or "").strip()

        pdir = self.resolve_pdir(slug)
        if pdir is None or not (pdir / "storyboard.json").exists():
            msg = ("No project with a storyboard to source assets for. Run the storyboard "
                   "for this slug first.")
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg}

        if progress is not None:
            progress.start(self.entry.emoji, who, "sourcing + clearing assets", topic or slug)
        try:
            manifest = run_source_assets(pdir)
        except Exception as exc:  # an unusable storyboard, said plainly
            if progress is not None:
                progress.fail(who, str(exc))
            return {"ok": False, "text": str(exc)}

        ok, errors = validate("asset_manifest", manifest)
        if not ok:
            msg = f"asset_manifest failed contract validation: {'; '.join(errors)}"
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg, "saved": str(pdir / "asset_manifest.json")}
        projects.mark_artifact(slug, "asset_manifest", pdir / "asset_manifest.json")
        if progress is not None:
            progress.done(who, "finished sourcing the assets")
        return {"ok": True, "text": _manifest_digest(manifest), "topic": topic, "slug": slug,
                "saved": str(pdir / "asset_manifest.json")}
