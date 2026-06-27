"""studio.engines — REUSE the sibling agent engines (never fork them).

Thin, lazy wrappers over the real engines the v1 pipeline already proved:

  - Sage research      -> topic-researcher/researcher.py  ``run(topic, angle)``
  - Sage fact-check    -> topic-researcher/factcheck.py   ``factcheck(script, brief)``
  - Marlow scriptwriter-> scriptwriter/script_engine.py   ``write_script(brief)``

These engines ship modules with the SAME bare names (``llm``, ``chat_state``,
``search`` …). Importing two of them naively would let whichever loaded first win
``sys.modules['llm']`` and silently cross-wire the other. So we load each engine
with its module graph isolated — the same pattern as ``atlas/adapters/loader.py``
(we reuse the PATTERN; the ENGINES are reused untouched, never copied).

Everything is imported lazily INSIDE the wrappers, so ``import studio`` stays
cheap and offline; nothing here runs until a real production call is made. Tests
mock at these seams (or monkeypatch ``studio.engines.research`` etc.).
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path

from . import config

# Bare module names the siblings define locally and would collide on (superset;
# a name a sibling lacks is simply absent from the snapshot).
_COLLIDING = (
    "llm", "chat_state", "search", "youtube", "compaction", "chat", "run",
    "trends", "researcher", "factcheck", "agent", "script_engine", "roundtable",
)

_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], object] = {}


def load_engine(agent_dir, module_name: str):
    """Load ``<module_name>.py`` from ``agent_dir`` with sibling imports isolated.

    Snapshot ``sys.path`` + the colliding ``sys.modules`` names, drop the local
    names so the engine re-imports ITS OWN siblings, load it under its own name,
    then restore. The loaded module keeps its sibling references in its globals,
    so it stays self-contained after restore. Load-once cached; thread-safe.
    """
    agent_dir = str(Path(agent_dir).resolve())
    key = (agent_dir, module_name)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached

        names = set(_COLLIDING) | {module_name}
        saved_path = list(sys.path)
        saved_mods = {n: sys.modules.get(n) for n in names}
        try:
            for n in names:
                sys.modules.pop(n, None)
            sys.path.insert(0, agent_dir)
            spec = importlib.util.spec_from_file_location(
                module_name, f"{agent_dir}/{module_name}.py")
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not locate {module_name}.py in {agent_dir}")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            spec.loader.exec_module(mod)
        finally:
            sys.path[:] = saved_path
            for n, m in saved_mods.items():
                if m is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = m
        _CACHE[key] = mod
        return mod


# --- the reused engine calls -------------------------------------------------
def research(topic: str, angle: str | None = None) -> dict:
    """Run Sage's real research engine; return the research pack (brief) dict.

    Wraps ``researcher.run(topic, angle, quiet=True)`` which returns
    ``(pack, json_path, md_path)`` — we keep the pack.
    """
    mod = load_engine(config.SAGE_DIR, "researcher")
    pack, _json_path, _md_path = mod.run(topic, angle, quiet=True)
    return pack


def write_script(brief: dict, *, chat_fn=None, **kwargs) -> dict:
    """Run Marlow's real script engine on a research brief; return the script dict.

    Wraps ``script_engine.write_script(brief, ...)`` (one-point-per-scene scenes
    with narration + on_screen_text + claims). ``chat_fn`` overrides the brain
    seam when provided (else the engine uses its own ``llm.chat``).
    """
    mod = load_engine(config.SCRIPTWRITER_DIR, "script_engine")
    if chat_fn is not None:
        return mod.write_script(brief, chat_fn=chat_fn, **kwargs)
    return mod.write_script(brief, **kwargs)


def factcheck(script: dict, brief: dict, *, chat_fn=None) -> dict:
    """Run Sage's real pass-2 fact-check; return the report dict {verdict, summary, claims}.

    Wraps ``factcheck.factcheck(script, brief, quiet=True)``. ``chat_fn`` overrides
    the brain seam when provided.
    """
    mod = load_engine(config.SAGE_DIR, "factcheck")
    if chat_fn is not None:
        return mod.factcheck(script, brief, chat_fn=chat_fn, quiet=True)
    return mod.factcheck(script, brief, quiet=True)


def iris_layouts() -> tuple:
    """Iris's closed LAYOUTS vocab — the canonical archetype vocabulary.

    Reads ``LAYOUTS`` from the art_engine (``art-director/art_engine.py``) via the
    isolated load_engine seam so the art-director's sibling imports (llm, chat_state …)
    are kept isolated from the studio's namespace.
    """
    mod = load_engine(config.ART_DIRECTOR_DIR, "art_engine")
    return tuple(getattr(mod, "LAYOUTS"))


def storyboard(script: dict, pdir=None) -> dict:  # noqa: ARG001 — pdir accepted for interface symmetry
    """Run Iris's storyboard planner; returns {scenes:[{scene_no, layout, ...}]}.

    Calls ``art_engine.build_storyboard(script, None)`` (style_guide=None so Iris
    falls back to her defaults — the studio does not supply a style_guide at this
    stage). The pdir argument is accepted for interface symmetry but not forwarded
    (Iris does not write to disk; the pipeline stage writes storyboard.json).
    """
    mod = load_engine(config.ART_DIRECTOR_DIR, "art_engine")
    return mod.build_storyboard(script, None)


def audio_hf():
    """Cadence's Kokoro/ffmpeg toolchain wrappers (audio-designer/hf_audio.py).

    REUSE, never fork: ``tts`` (Kokoro 24kHz mono), ``concat_wavs`` (lossless
    concat-demuxer), ``transcribe`` (optional whisper word-level), ``probe_duration``,
    and the documentary ``build_mix_recipe`` / ``run_mix``. ``hf_audio`` imports only
    stdlib, so it carries no sibling-name collisions — but we still load it through the
    isolated loader for consistency and load-once caching.
    """
    return load_engine(config.AUDIO_DIR, "hf_audio")
