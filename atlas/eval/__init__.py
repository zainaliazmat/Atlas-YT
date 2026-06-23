"""atlas.eval — the Phase-1 evaluation foundation (additive, read-only).

This package MEASURES the quality of a completed video project against the
CEO-owned rubric in atlas/rubric/. It never edits the deterministic spine, the
contracts, or the gates; it only reads a project dir's artifacts and media.

Layout:
  types.py            shared Measurement + EvalContext + media helpers
  analyzers/          deterministic objective analyzers (audio, video, text)
  judged.py           ensembled LLM-judged properties (hook_strength, polish)
  tracking.py         append-only per-property results store
  rollup.py           local-property -> global-dimension roll-up vs the rubric
  inspector.py        the orchestrator: run analyzers -> emit a scorecard
  validation.py       eval-of-the-eval (refs pass / known-bad fails / CEO check)
  diagnose.py         credit assignment: a shortfall -> one owning stage
  loop.py             one minimal, bounded improvement loop (write-boundary safe)

Hard rule: nothing in this package may write atlas/rubric/ or atlas/contracts/.
The rubric is imported read-only; there is no save path.
"""
from __future__ import annotations

from .types import (
    Measurement,
    EvalContext,
    Analyzer,
    ffprobe_json,
    media_duration_sec,
    run_ffmpeg,
    make_measurement_error,
)

__all__ = [
    "Measurement",
    "EvalContext",
    "Analyzer",
    "ffprobe_json",
    "media_duration_sec",
    "run_ffmpeg",
    "make_measurement_error",
]
