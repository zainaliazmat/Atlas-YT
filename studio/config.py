"""studio.config — paths, defaults, and the channel + pack registries.

This module is the single source of truth for *where things live* and *what the
defaults are* on the v2 path. It is deliberately tiny and dependency-free so it
can be imported from anywhere (CLI, pipeline, compose, review) without pulling
in heavy siblings.

Two registries are read here (the registry/adapter PATTERN is reused from
``atlas/registry.py``, but applied to PACKS and CHANNELS instead of agents —
see REUSE_MAP.md):

  - PACK registry   — Design Packs (the opinionated look+motion+type+audio
                      systems a video is authored against). Loader lives in
                      ``studio.packs``; this file only resolves the registry
                      location and defaults.
  - CHANNEL registry — per-channel production config (target runtime band,
                      default pack, voice, brand strings, output roots).

Everything is config DATA + path resolution. No production logic.
"""

from __future__ import annotations

from pathlib import Path

# --- Repo / studio roots -----------------------------------------------------
# studio/ is a sibling of atlas/; REPO_ROOT is their common parent.
STUDIO_DIR: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = STUDIO_DIR.parent

# TODO(phase-0): make these overridable via env (STUDIO_* ) without breaking the
# "import is cheap and side-effect-free" rule — resolve env lazily in helpers.
PACKS_DIR: Path = STUDIO_DIR / "packs"             # the loader code package
DESIGN_PACKS_DIR: Path = STUDIO_DIR / "design-packs"  # the pack DATA (one dir per pack)
LIBRARY_DIR: Path = STUDIO_DIR / "library"         # the resolver code package
ASSET_LIBRARY_DIR: Path = REPO_ROOT / "asset-library"  # the shared cached-asset store
PROJECTS_DIR: Path = STUDIO_DIR / "projects"       # HyperFrames-native per-video dirs

# Sibling engine projects we REUSE (never fork) — loaded in isolation at call time.
SAGE_DIR: Path = REPO_ROOT / "topic-researcher"     # research + factcheck engine
SCRIPTWRITER_DIR: Path = REPO_ROOT / "scriptwriter"  # script_engine.write_script
AUDIO_DIR: Path = REPO_ROOT / "audio-designer"       # Kokoro tts/concat/transcribe + mix

# --- Registry file locations -------------------------------------------------
# Design Pack registry: one entry per pack, discovered under DESIGN_PACKS_DIR.
PACK_REGISTRY_PATH: Path = DESIGN_PACKS_DIR / "packs.json"
# Shared Asset Library manifest (the cache index).
ASSET_LIBRARY_MANIFEST: Path = ASSET_LIBRARY_DIR / "library.json"
# TODO(phase-0): channels registry (one entry per YouTube channel/brand).
CHANNEL_REGISTRY_PATH: Path = STUDIO_DIR / "channels.json"

# --- Defaults ----------------------------------------------------------------
# Production format defaults (mirror the golden reference's 1920x1080).
DEFAULT_WIDTH: int = 1920
DEFAULT_HEIGHT: int = 1080
DEFAULT_FPS: int = 30

# Two target runtime bands (short-now / long-future) — see project memory
# "two target formats". TODO(phase-0): wire these into channel config.
RUNTIME_BAND_SHORT = (60.0, 90.0)
RUNTIME_BAND_LONG = (300.0, 480.0)

DEFAULT_PACK: str = "dark-truth-social"   # the pack with a proven end-to-end run
DEFAULT_VOICE: str = "af_heart"  # Kokoro default, matches audio-designer
DEFAULT_ASPECT: str = "16:9"
DEFAULT_PUBLISH_TARGET: str = "youtube"

# Render-cost ceiling for `--unattended` auto-approval of the FINAL gate. Mirrors
# atlas/dispatcher.py's render_budget_sec idea: the final gate may only be auto-cleared
# when the estimated render cost is at/under this ceiling (and the quality gates pass).
# Configurable per channel (channels.json) and per run (--render-budget).
DEFAULT_RENDER_BUDGET_SEC: float = 600.0

# Fallback channel record when channels.json is absent or a channel is unknown — keeps
# "one channel today, many later" a pure config change.
DEFAULT_CHANNEL: dict = {
    "default_pack": DEFAULT_PACK,
    "aspect": DEFAULT_ASPECT,
    "publish_target": DEFAULT_PUBLISH_TARGET,
    "voice": DEFAULT_VOICE,
    "render_budget_sec": DEFAULT_RENDER_BUDGET_SEC,
}


def _read_json(path: Path) -> dict:
    """Tiny dependency-free JSON read; returns {} on any problem (config is best-effort)."""
    import json
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_channel_registry(path: Path | None = None) -> dict:
    """Read channels.json into ``{channel_id: {default_pack, aspect, publish_target,
    voice, render_budget_sec}}``. Never raises on a missing/corrupt file — returns ``{}``
    so callers can fall back to DEFAULT_CHANNEL (the registry PATTERN, applied to
    channels; mirrors atlas/registry.py)."""
    path = path or CHANNEL_REGISTRY_PATH
    if not path.is_file():
        return {}
    doc = _read_json(path)
    channels = doc.get("channels", doc)  # tolerate {"channels": {...}} or a bare map
    return channels if isinstance(channels, dict) else {}


def load_pack_registry(path: Path | None = None) -> dict:
    """Read the Design Pack registry (design-packs/packs.json) into ``{pack_id: entry}``.
    Thin path resolver only — real pack LOADING (tokens, partials) lives in
    ``studio.packs``. Read directly here (no packs import) to stay import-cheap and avoid
    the packs→config circular."""
    path = path or PACK_REGISTRY_PATH
    if not path.is_file():
        return {}
    doc = _read_json(path)
    return {p["id"]: p for p in doc.get("packs", []) if isinstance(p, dict) and p.get("id")}


def resolve_channel(channel: str | None, *, registry: dict | None = None) -> dict:
    """Resolve a channel id to its record, falling back to DEFAULT_CHANNEL for unknown /
    absent channels so a missing channels.json never blocks a run. Returns a NEW dict
    (DEFAULT_CHANNEL overlaid with the channel's overrides)."""
    reg = registry if registry is not None else load_channel_registry()
    rec = dict(DEFAULT_CHANNEL)
    if channel and channel in reg and isinstance(reg[channel], dict):
        rec.update(reg[channel])
    rec.setdefault("render_budget_sec", DEFAULT_RENDER_BUDGET_SEC)
    return rec


def resolve_run_config(*, channel: str | None = None, pack: str | None = None,
                       voice: str | None = None, render_budget_sec: float | None = None,
                       registry: dict | None = None) -> dict:
    """Resolve the effective per-run config from channel + explicit overrides. ``--pack``
    / ``voice`` / ``render_budget_sec`` override the channel's defaults. Returns
    ``{channel, pack_id, voice, aspect, publish_target, render_budget_sec}``."""
    rec = resolve_channel(channel, registry=registry)
    return {
        "channel": channel or "main",
        "pack_id": pack or rec.get("default_pack") or DEFAULT_PACK,
        "voice": voice or rec.get("voice") or DEFAULT_VOICE,
        "aspect": rec.get("aspect") or DEFAULT_ASPECT,
        "publish_target": rec.get("publish_target") or DEFAULT_PUBLISH_TARGET,
        "render_budget_sec": float(render_budget_sec if render_budget_sec is not None
                                   else rec.get("render_budget_sec", DEFAULT_RENDER_BUDGET_SEC)),
    }


def project_dir(slug: str) -> Path:
    """On-disk working directory for a production ``slug`` (no side effects — creation
    belongs to the pipeline)."""
    return PROJECTS_DIR / slug
