"""Tests for the AUDIO analyzer (eval/analyzers/audio.py).

Run from the atlas/ dir:
    ../venv/bin/python -m pytest tests/test_eval_audio.py -q
"""
from __future__ import annotations

import math
import pathlib
import shutil

import pytest

import rubric
from eval.types import EvalContext
from eval.analyzers import audio


FIX = (
    pathlib.Path(__file__).resolve().parents[1]
    / "projects"
    / "gpt-4o-vs-claude-vs-gemini-vs-deepseek-comparison--20260621-013345-67a3"
)

# The 7 (stage, prop) keys this analyzer owns.
OWNED = [
    ("audiomix", "integrated_loudness"),
    ("audiomix", "true_peak"),
    ("audiomix", "vo_intelligibility"),
    ("audiomix", "ducking_depth"),
    ("audiomix", "sfx_on_beat"),
    ("render", "final_loudness"),
    ("render", "final_peak"),
]

_HAS_FFMPEG = shutil.which("ffmpeg") is not None
ffmpeg_required = pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not available")


def _by_key(meas):
    return {(m.stage, m.prop): m for m in meas}


# ---------------------------------------------------------------------------
# (a) all 7 props returned, no exceptions
# ---------------------------------------------------------------------------

@ffmpeg_required
def test_fixture_returns_all_seven_props():
    assert FIX.is_dir(), f"fixture missing: {FIX}"
    meas = audio.analyze(EvalContext(FIX))
    keys = {(m.stage, m.prop) for m in meas}
    assert keys == set(OWNED)
    assert len(meas) == 7


# ---------------------------------------------------------------------------
# (b) plausibility of measured values on the gold fixture
# ---------------------------------------------------------------------------

@ffmpeg_required
def test_fixture_value_plausibility():
    by = _by_key(audio.analyze(EvalContext(FIX)))

    il = by[("audiomix", "integrated_loudness")]
    assert il.value is not None and il.error is None
    assert -40.0 < il.value < 0.0, il.value

    tp = by[("audiomix", "true_peak")]
    assert tp.value is not None and tp.error is None
    assert -30.0 < tp.value < 3.0, tp.value

    vo = by[("audiomix", "vo_intelligibility")]
    assert vo.value is not None and vo.error is None
    assert math.isfinite(vo.value), vo.value

    sfx = by[("audiomix", "sfx_on_beat")]
    assert sfx.value is not None and sfx.error is None
    assert sfx.value >= 0.0
    # fixture sfx at ~47.85s, scene-8 start ~47.85s -> near zero
    assert sfx.value < 0.5, sfx.value

    fl = by[("render", "final_loudness")]
    assert fl.value is not None and fl.error is None
    assert -40.0 < fl.value < 0.0, fl.value

    fp = by[("render", "final_peak")]
    assert fp.value is not None and fp.error is None
    assert -30.0 < fp.value < 3.0, fp.value


@ffmpeg_required
def test_fixture_ducking_back_to_back_segments():
    """The gold fixture has back-to-back narration (no VO gaps); ducking_depth
    must degrade gracefully to value=None with an explanatory error/note."""
    by = _by_key(audio.analyze(EvalContext(FIX)))
    dd = by[("audiomix", "ducking_depth")]
    if dd.value is None:
        assert dd.error
        assert dd.detail.get("n_gap_windows", 0) == 0
    else:
        # If a future fixture has gaps, value must be finite.
        assert math.isfinite(dd.value)


# ---------------------------------------------------------------------------
# (c) every measurement maps to a real rubric band, and band metadata matches
# ---------------------------------------------------------------------------

@ffmpeg_required
def test_measurements_match_rubric_bands():
    for m in audio.analyze(EvalContext(FIX)):
        b = rubric.band(m.stage, m.prop)
        assert b is not None, f"no band for {m.stage}:{m.prop}"
        assert m.kind == b["kind"]
        assert m.rolls_up_to == tuple(b["rolls_up_to"])
        assert m.unit == b.get("unit", "")
        assert m.owner == b["owner"]


# ---------------------------------------------------------------------------
# (d) graceful degradation on an empty project dir
# ---------------------------------------------------------------------------

def test_graceful_degradation_empty_dir(tmp_path):
    meas = audio.analyze(EvalContext(tmp_path))
    keys = {(m.stage, m.prop) for m in meas}
    assert keys == set(OWNED)
    for m in meas:
        assert m.value is None
        assert m.error
        # band metadata still resolves even with no media present.
        b = rubric.band(m.stage, m.prop)
        assert b is not None
        assert m.kind == b["kind"]
        assert m.rolls_up_to == tuple(b["rolls_up_to"])
