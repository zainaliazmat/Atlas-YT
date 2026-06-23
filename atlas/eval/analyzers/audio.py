"""AUDIO analyzer — objective audio-mix and final-render measurements.

Pure, deterministic, NO LLM. Reads one project's audio artifacts and the muxed
video, returns raw `Measurement`s. Analyzers MEASURE; the CEO-owned rubric
DECIDES pass/fail elsewhere. We read band *metadata* (kind / rolls_up_to / unit
/ owner) so our stage:prop keys stay consistent with the rubric — but we never
read thresholds (min/max/target).

Properties owned here (stage:prop):
  audiomix:integrated_loudness  (LUFS)  ebur128 integrated loudness of master.wav
  audiomix:true_peak            (dBTP)  ebur128 true peak of master.wav
  audiomix:vo_intelligibility   (dB)    VO-to-bed SNR during VO (master - narration)
  audiomix:ducking_depth        (dB)    realized bed reduction in VO vs VO-gap windows
  audiomix:sfx_on_beat          (sec)   |signature SFX onset - cut into #FFD000 scene|
  render:final_loudness         (LUFS)  ebur128 integrated loudness of video.mp4 audio
  render:final_peak             (dBTP)  ebur128 true peak of video.mp4 audio

Every measurement degrades gracefully: on any failure (missing file, parse miss,
ffmpeg rc!=0) we return value=None with a clear `error` string and never raise.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Optional

import rubric

from eval.types import (
    EvalContext,
    Measurement,
    make_measurement_error,
    run_ffmpeg,
)


# ---------------------------------------------------------------------------
# Band-metadata plumbing: build a Measurement whose kind/rolls_up_to/unit/owner
# come straight from the rubric band (keeps stage/prop keys honest).
# ---------------------------------------------------------------------------

def _band(stage: str, prop: str) -> Optional[dict]:
    try:
        return rubric.band(stage, prop)
    except Exception:
        return None


def _meta(stage: str, prop: str) -> dict:
    """Return {kind, rolls_up_to, unit, owner, artifact} from the band, with
    safe fallbacks if the band is somehow missing (we still emit a Measurement)."""
    b = _band(stage, prop)
    if b is None:
        return {
            "kind": "objective",
            "rolls_up_to": (),
            "unit": "",
            "owner": "Cadence",
            "artifact": "",
        }
    return {
        "kind": b["kind"],
        "rolls_up_to": tuple(b["rolls_up_to"]),
        "unit": b.get("unit", ""),
        "owner": b["owner"],
        "artifact": b.get("artifact", ""),
    }


def _ok(stage: str, prop: str, value: Optional[float], *, detail: dict,
        error: Optional[str] = None) -> Measurement:
    m = _meta(stage, prop)
    return Measurement(
        artifact=m["artifact"],
        stage=stage,
        owner=m["owner"],
        prop=prop,
        value=value,
        kind=m["kind"],
        rolls_up_to=m["rolls_up_to"],
        unit=m["unit"],
        detail=detail,
        error=error,
    )


def _err(stage: str, prop: str, err: str, *, detail: Optional[dict] = None) -> Measurement:
    # value=None + error set. Use the shared helper when there's no detail to
    # attach; otherwise build directly (Measurement is frozen).
    if not detail:
        m = _meta(stage, prop)
        return make_measurement_error(
            artifact=m["artifact"], stage=stage, owner=m["owner"], prop=prop,
            kind=m["kind"], rolls_up_to=m["rolls_up_to"], err=err, unit=m["unit"],
        )
    return _ok(stage, prop, None, detail=detail, error=err)


# ---------------------------------------------------------------------------
# ebur128 parsing
# ---------------------------------------------------------------------------

# The ffmpeg ebur128 *Summary* block looks like:
#   Summary:
#     Integrated loudness:
#       I:         -21.8 LUFS
#       Threshold: -32.1 LUFS
#     ...
#     True peak:
#       Peak:       -0.6 dBFS
_RE_INTEGRATED = re.compile(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_RE_TRUE_PEAK = re.compile(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS")


def _ebur128(path: Path) -> tuple[Optional[float], Optional[float], str]:
    """Run ebur128 on `path`, returning (integrated_lufs, true_peak_dbtp, err).

    `err` is "" on success, otherwise a description. We parse from the Summary
    block at the tail of stderr; the per-frame lines also carry an `I:` value so
    we deliberately take the LAST match (the final integrated value)."""
    if not path.is_file():
        return None, None, f"missing media: {path}"
    rc, _out, stderr = run_ffmpeg(
        ["-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"]
    )
    if rc != 0:
        tail = (stderr or "").strip().splitlines()[-3:]
        return None, None, f"ffmpeg rc={rc}: {' | '.join(tail)}"

    # Restrict to the Summary block when present (most reliable), else whole log.
    summary = stderr
    idx = stderr.rfind("Summary:")
    if idx != -1:
        summary = stderr[idx:]

    i_matches = _RE_INTEGRATED.findall(summary)
    p_matches = _RE_TRUE_PEAK.findall(summary)
    integrated = float(i_matches[-1]) if i_matches else None
    true_peak = float(p_matches[-1]) if p_matches else None

    if integrated is None and true_peak is None:
        return None, None, "ebur128: could not parse I/Peak from summary"
    return integrated, true_peak, ""


# ---------------------------------------------------------------------------
# soundfile helpers (lazy import so a missing wav still degrades cleanly)
# ---------------------------------------------------------------------------

def _read_mono(path: Path):
    """Return (samples_1d, samplerate) or raise. Mixes to mono if needed."""
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(str(path), always_2d=False)
    arr = np.asarray(data, dtype=np.float64)
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    return arr, sr


def _rms(x) -> float:
    import numpy as np

    if x is None or len(x) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(np.asarray(x, dtype=np.float64)))))


# ---------------------------------------------------------------------------
# Individual measurements
# ---------------------------------------------------------------------------

def _measure_master_ebur128(ctx: EvalContext) -> tuple[Measurement, Measurement]:
    """audiomix:integrated_loudness + audiomix:true_peak (one ebur128 run)."""
    integrated, peak, err = _ebur128(ctx.master)
    base_detail = {"source": str(ctx.master), "filter": "ebur128=peak=true"}
    if err:
        return (
            _err("audiomix", "integrated_loudness", err, detail=base_detail),
            _err("audiomix", "true_peak", err, detail=base_detail),
        )
    li = _ok(
        "audiomix", "integrated_loudness", integrated,
        detail={**base_detail, "integrated_lufs": integrated, "true_peak_dbtp": peak},
        error=None if integrated is not None else "could not parse integrated loudness",
    )
    tp = _ok(
        "audiomix", "true_peak", peak,
        detail={**base_detail, "integrated_lufs": integrated, "true_peak_dbtp": peak},
        error=None if peak is not None else "could not parse true peak",
    )
    return li, tp


def _measure_vo_intelligibility(ctx: EvalContext) -> Measurement:
    """SNR = 20*log10(rms(narration)/rms(residual)) where residual = master - narration
    (the in-mix, ducked bed). master & narration are sample-aligned 24kHz mono."""
    stage, prop = "audiomix", "vo_intelligibility"
    if not ctx.master.is_file():
        return _err(stage, prop, f"missing media: {ctx.master}")
    if not ctx.narration.is_file():
        return _err(stage, prop, f"missing media: {ctx.narration}")
    try:
        import numpy as np

        m, sr_m = _read_mono(ctx.master)
        n, sr_n = _read_mono(ctx.narration)
        L = int(min(len(m), len(n)))
        if L == 0:
            return _err(stage, prop, "empty audio buffer(s)")
        m = m[:L]
        n = n[:L]
        residual = m - n
        rms_n = _rms(n)
        rms_res = _rms(residual)
        detail = {
            "rms_narration": rms_n,
            "rms_residual_bed": rms_res,
            "samples_used": L,
            "sr_master": sr_m,
            "sr_narration": sr_n,
            "method": "20*log10(rms(narration)/rms(master-narration))",
        }
        if rms_res == 0.0:
            # No bed at all in the mix -> intelligibility is effectively infinite.
            return _err(stage, prop, "residual bed rms is zero (no in-mix bed)", detail=detail)
        if rms_n == 0.0:
            return _err(stage, prop, "narration rms is zero", detail=detail)
        snr = 20.0 * math.log10(rms_n / rms_res)
        return _ok(stage, prop, float(snr), detail={**detail, "snr_db": float(snr)})
    except Exception as e:
        return _err(stage, prop, f"{type(e).__name__}: {e}")


def _measure_ducking_depth(ctx: EvalContext) -> Measurement:
    """Realized depth of bed reduction during VO.

    residual = master - narration is the bed-in-mix. Using transcript segments,
    VO windows are [start_sec, end_sec]; gap windows are the silences BETWEEN
    consecutive segments (where VO is quiet, so the bed should be louder).
    depth = 20*log10(rms_gap / rms_vo). If there are no usable gaps (back-to-back
    narration), value=None with a graceful note — we do NOT fabricate."""
    stage, prop = "audiomix", "ducking_depth"
    transcript = ctx.transcript
    if not transcript:
        return _err(stage, prop, "no transcript available")
    segs = transcript.get("segments") or []
    if not segs:
        return _err(stage, prop, "transcript has no segments")
    if not ctx.master.is_file() or not ctx.narration.is_file():
        return _err(stage, prop, "missing master/narration media")

    try:
        import numpy as np

        m, sr = _read_mono(ctx.master)
        n, sr_n = _read_mono(ctx.narration)
        L = int(min(len(m), len(n)))
        if L == 0 or sr <= 0:
            return _err(stage, prop, "empty audio buffer(s)")
        m = m[:L]
        n = n[:L]
        residual = m - n
        dur = L / float(sr)

        # Sorted, clamped VO windows.
        vo_windows = []
        for s in segs:
            try:
                st = float(s["start_sec"])
                en = float(s["end_sec"])
            except (KeyError, TypeError, ValueError):
                continue
            st = max(0.0, min(st, dur))
            en = max(0.0, min(en, dur))
            if en > st:
                vo_windows.append((st, en))
        vo_windows.sort()

        # Gaps = silences between consecutive VO windows (+ trailing tail).
        gap_windows = []
        prev_end = 0.0
        for st, en in vo_windows:
            if st - prev_end > 0.05:  # >50ms counts as a usable gap
                gap_windows.append((prev_end, st))
            prev_end = max(prev_end, en)
        if dur - prev_end > 0.05:
            gap_windows.append((prev_end, dur))

        def _slice_rms(windows):
            import numpy as np
            chunks = []
            for st, en in windows:
                a = int(round(st * sr))
                b = int(round(en * sr))
                a = max(0, min(a, L))
                b = max(0, min(b, L))
                if b > a:
                    chunks.append(residual[a:b])
            if not chunks:
                return None
            cat = np.concatenate(chunks)
            return _rms(cat)

        detail = {
            "n_vo_windows": len(vo_windows),
            "n_gap_windows": len(gap_windows),
            "duration_sec": dur,
            "method": "20*log10(rms(residual in gaps)/rms(residual in VO))",
        }

        if not gap_windows:
            return _err(
                stage, prop,
                "no VO gaps to measure realized ducking (back-to-back narration)",
                detail=detail,
            )

        rms_vo = _slice_rms(vo_windows)
        rms_gap = _slice_rms(gap_windows)
        detail["rms_residual_vo"] = rms_vo
        detail["rms_residual_gap"] = rms_gap
        if not rms_vo or not rms_gap:
            return _err(stage, prop, "could not compute VO/gap residual rms", detail=detail)
        depth = 20.0 * math.log10(rms_gap / rms_vo)
        return _ok(stage, prop, float(depth), detail={**detail, "ducking_depth_db": float(depth)})
    except Exception as e:
        return _err(stage, prop, f"{type(e).__name__}: {e}")


def _sfx_track(manifest: Optional[dict]) -> Optional[dict]:
    if not manifest:
        return None
    for t in manifest.get("tracks", []) or []:
        if t.get("role") == "sfx":
            return t
    return None


def _measure_sfx_on_beat(ctx: EvalContext) -> Measurement:
    """value = |sfx.at_sec - transcript_segment[scene_no].start_sec|. The sfx
    track names the scene it cuts into (#FFD000 scene); we match by scene_no."""
    stage, prop = "audiomix", "sfx_on_beat"
    sfx = _sfx_track(ctx.audio_manifest)
    if sfx is None:
        return _err(stage, prop, "no sfx track in audio_manifest")
    try:
        at_sec = float(sfx["at_sec"])
    except (KeyError, TypeError, ValueError):
        return _err(stage, prop, "sfx track has no usable at_sec")
    scene_no = sfx.get("scene_no")
    transcript = ctx.transcript
    if not transcript:
        return _err(stage, prop, "no transcript available", detail={"sfx_at_sec": at_sec, "scene_no": scene_no})
    segs = transcript.get("segments") or []
    target = None
    for s in segs:
        if s.get("scene_no") == scene_no:
            target = s
            break
    if target is None:
        return _err(
            stage, prop,
            f"no transcript segment for scene_no={scene_no}",
            detail={"sfx_at_sec": at_sec, "scene_no": scene_no},
        )
    try:
        cut_sec = float(target["start_sec"])
    except (KeyError, TypeError, ValueError):
        return _err(stage, prop, "target segment has no start_sec",
                    detail={"sfx_at_sec": at_sec, "scene_no": scene_no})
    delta = abs(at_sec - cut_sec)
    return _ok(
        stage, prop, float(delta),
        detail={
            "sfx_at_sec": at_sec,
            "scene_no": scene_no,
            "scene_start_sec": cut_sec,
            "sfx_name": sfx.get("name"),
            "method": "abs(sfx.at_sec - segment[scene_no].start_sec)",
        },
    )


def _measure_video_ebur128(ctx: EvalContext) -> tuple[Measurement, Measurement]:
    """render:final_loudness + render:final_peak — ebur128 on the muxed mp4."""
    integrated, peak, err = _ebur128(ctx.video)
    base_detail = {"source": str(ctx.video), "filter": "ebur128=peak=true"}
    if err:
        return (
            _err("render", "final_loudness", err, detail=base_detail),
            _err("render", "final_peak", err, detail=base_detail),
        )
    li = _ok(
        "render", "final_loudness", integrated,
        detail={**base_detail, "integrated_lufs": integrated, "true_peak_dbtp": peak},
        error=None if integrated is not None else "could not parse integrated loudness",
    )
    tp = _ok(
        "render", "final_peak", peak,
        detail={**base_detail, "integrated_lufs": integrated, "true_peak_dbtp": peak},
        error=None if peak is not None else "could not parse true peak",
    )
    return li, tp


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

def analyze(ctx: EvalContext) -> list[Measurement]:
    """Return all 7 audio Measurements for one project. Never raises."""
    out: list[Measurement] = []

    li, tp = _measure_master_ebur128(ctx)
    out.append(li)
    out.append(tp)
    out.append(_measure_vo_intelligibility(ctx))
    out.append(_measure_ducking_depth(ctx))
    out.append(_measure_sfx_on_beat(ctx))
    vli, vtp = _measure_video_ebur128(ctx)
    out.append(vli)
    out.append(vtp)
    return out
