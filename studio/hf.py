"""studio.hf — thin wrappers around ``npx hyperframes@0.7.10``.

The single seam between studio/ and the HyperFrames toolchain. Everything that
shells out to the CLI (lint / validate / inspect / render / tts) goes through
here so the version pin and subprocess discipline live in one place.

Relationship to the v1 wrappers (REUSE_MAP.md):
  - composition-engineer/hf_tools.py and audio-designer/hf_audio.py already
    implement battle-tested, error-contained, timeout-bounded wrappers (gate
    short-circuit + transient-Chrome retry, the pure documentary mix recipe,
    Kokoro tts/concat/transcribe). They are REUSE — studio.hf is a thin
    re-export/adapter layer over them, NOT a reimplementation.
  - The ONE deliberate difference: studio pins ``hyperframes@0.7.10``
    (studio.HYPERFRAMES_VERSION) whereas the v1 wrappers call 0.6.115. The
    version is centralized here so the bump is a one-line change and both the
    compose-side and audio-side wrappers agree.

Discipline these wrappers preserve (from the v1 originals):
  - never raise — every call returns a structured ``{"ok": bool, ...}`` dict;
  - bounded timeouts per command;
  - parse the first JSON object out of stdout (tolerate Chrome preamble +
    trailing telemetry);
  - read-only gate checks are retried on transient Chrome contention.

No real logic yet — signatures + docstrings only.
"""

from __future__ import annotations

from pathlib import Path

from . import HYPERFRAMES_VERSION  # the v2 pin: "0.7.10"

# Command timeouts (seconds) — mirror the proven v1 budgets; centralized here.
LINT_TIMEOUT = 90
VALIDATE_TIMEOUT = 150
INSPECT_TIMEOUT = 180
RENDER_TIMEOUT = 600
TTS_TIMEOUT = 600


def toolchain_available() -> bool:
    """Return True iff the HyperFrames CLI (and ffmpeg, where needed) is usable.

    TODO: wrap composition-engineer/hf_tools.toolchain_available (+ the audio
    side) and additionally confirm the @0.7.10 pin resolves.
    """
    raise NotImplementedError("studio.hf.toolchain_available")


def lint(scene_dir: Path) -> dict:
    """Static structure + determinism check. TODO: wrap hf_tools.run_lint @0.7.10."""
    raise NotImplementedError("studio.hf.lint")


def validate(scene_dir: Path) -> dict:
    """Headless-Chrome load: console errors + contrast. TODO: wrap run_validate."""
    raise NotImplementedError("studio.hf.validate")


def inspect(scene_dir: Path, *, strict: bool = False) -> dict:
    """Layout overflow/overlap + motion verification. TODO: wrap run_inspect."""
    raise NotImplementedError("studio.hf.inspect")


def gate(scene_dir: Path, *, motion_strict: bool = False) -> dict:
    """Composition auto-gate: lint → validate → inspect (short-circuit + retry).

    TODO: wrap hf_tools.run_gate; this is the gate used by studio.review in-loop
    on the DRAFT render, not only at the end.
    """
    raise NotImplementedError("studio.hf.gate")


def render(scene_dir: Path, *, draft: bool = True) -> dict:
    """Render a composition (draft by default). TODO: wrap hf_tools.run_render."""
    raise NotImplementedError("studio.hf.render")


def tts(text: str, out_path: Path, *, voice: str = "af_heart", speed: float = 1.0) -> dict:
    """Kokoro TTS to WAV. TODO: wrap audio-designer/hf_audio.tts @0.7.10."""
    raise NotImplementedError("studio.hf.tts")
