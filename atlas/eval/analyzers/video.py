"""Video / motion analyzer (Phase-1 evaluation foundation).

Owns the objective VISUAL/MOTION properties of one completed project. Like every
analyzer it MEASURES only; the CEO-owned rubric DECIDES pass/fail. We therefore
read the rubric for *metadata* (kind / rolls_up_to / unit / owner / artifact)
but never for thresholds.

Properties owned (stage:prop):
  compose:motion_energy        -- mean |delta-luma| between frames sampled @~4fps
  compose:cut_rhythm           -- median scene duration from composition_manifest
  compose:av_sync              -- fraction of scenes within 0.25s of narration boundary
  compose:layout_integrity     -- integrity_flags + contrast_failures (must be 0)
  compose:auto_gate_first_pass -- gated_ok / total scenes
  render:final_runtime         -- container duration of video.mp4

Never raises: every failure path returns a Measurement with value=None + error.
"""
from __future__ import annotations

import statistics
from typing import Any, Optional

import rubric
from eval.types import (
    EvalContext,
    Measurement,
    media_duration_sec,
    make_measurement_error,
)

# How many frames @4fps we are willing to sample for motion_energy. A 73s clip
# at 4fps is ~290 frames, comfortably under this; the cap guards a pathological
# long render so the analyzer stays bounded.
_MAX_SAMPLED_FRAMES = 1200
_SAMPLE_FPS = 4.0
_DOWNSCALE_WIDTH = 320
_AV_SYNC_TOL_SEC = 0.25


def _meta(stage: str, prop: str) -> dict[str, Any]:
    """Pull rubric metadata (NOT thresholds) for a property. Falls back to safe
    defaults if the rubric somehow does not score it, so a measurement is always
    well-formed."""
    b = rubric.band(stage, prop)
    if b is None:
        return {
            "artifact": "video.mp4",
            "stage": stage,
            "owner": "holistic",
            "kind": "objective",
            "rolls_up_to": (),
            "unit": "",
        }
    return {
        "artifact": b.get("artifact", "video.mp4"),
        "stage": b.get("stage", stage),
        "owner": b.get("owner", "holistic"),
        "kind": b.get("kind", "objective"),
        "rolls_up_to": tuple(b.get("rolls_up_to", ())),
        "unit": b.get("unit", ""),
    }


def _ok(stage: str, prop: str, value: Optional[float], detail: dict) -> Measurement:
    m = _meta(stage, prop)
    return Measurement(
        artifact=m["artifact"], stage=m["stage"], owner=m["owner"], prop=prop,
        value=value, kind=m["kind"], rolls_up_to=m["rolls_up_to"], unit=m["unit"],
        detail=detail,
    )


def _err(stage: str, prop: str, err: str) -> Measurement:
    m = _meta(stage, prop)
    return make_measurement_error(
        artifact=m["artifact"], stage=m["stage"], owner=m["owner"], prop=prop,
        kind=m["kind"], rolls_up_to=m["rolls_up_to"], err=err, unit=m["unit"],
    )


# ---------------------------------------------------------------------------
# Individual property measurers. Each returns ONE Measurement and never raises.
# ---------------------------------------------------------------------------

def _measure_motion_energy(ctx: EvalContext) -> Measurement:
    stage, prop = "compose", "motion_energy"
    if not ctx.has_media("video"):
        return _err(stage, prop, f"video not found: {ctx.video}")
    try:
        import cv2  # noqa: PLC0415 - optional/heavy dependency, import lazily
        import numpy as np
    except Exception as e:  # pragma: no cover - env dependent
        return _err(stage, prop, f"cv2/numpy unavailable: {type(e).__name__}: {e}")

    cap = None
    try:
        cap = cv2.VideoCapture(str(ctx.video))
        if not cap.isOpened():
            return _err(stage, prop, f"cv2 could not open video: {ctx.video}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        # step in *source* frames to land near _SAMPLE_FPS.
        step = max(1, round(fps / _SAMPLE_FPS)) if fps > 0 else 1

        # Sequential walk: grab() every frame (cheap demux, no full decode) and
        # only retrieve()+decode every `step`-th frame to land near _SAMPLE_FPS.
        # This avoids both per-frame seeks (keyframe re-decode) and decoding
        # frames we never look at -> ~step-x faster than read()-on-everything.
        diffs: list[float] = []
        prev_gray = None
        sampled = 0
        pos = 0
        capped = False
        consecutive_fail = 0
        while True:
            if sampled >= _MAX_SAMPLED_FRAMES:
                capped = True
                break
            if not cap.grab():
                consecutive_fail += 1
                if consecutive_fail > 5:
                    break
                continue
            consecutive_fail = 0
            if pos % step != 0:
                pos += 1
                continue
            pos += 1
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                continue
            sampled += 1
            # Downscale for speed (preserve aspect).
            h, w = frame.shape[:2]
            if w > _DOWNSCALE_WIDTH and w > 0:
                new_h = max(1, int(round(h * _DOWNSCALE_WIDTH / w)))
                frame = cv2.resize(frame, (_DOWNSCALE_WIDTH, new_h),
                                   interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
            if prev_gray is not None:
                diffs.append(float(np.mean(np.abs(gray - prev_gray))))
            prev_gray = gray

        if not diffs:
            return _err(stage, prop,
                        f"no frame diffs computed (sampled={sampled}, "
                        f"fps={fps}, frames={frame_count})")

        value = float(statistics.fmean(diffs))
        variance = float(statistics.pvariance(diffs)) if len(diffs) > 1 else 0.0
        detail = {
            "variance": variance,
            "n_diffs": len(diffs),
            "frames_sampled": sampled,
            "source_fps": round(fps, 3),
            "source_frame_count": frame_count,
            "sample_step_frames": step,
            "downscale_width": _DOWNSCALE_WIDTH,
            "capped": capped,
        }
        return _ok(stage, prop, value, detail)
    except Exception as e:  # never raise
        return _err(stage, prop, f"{type(e).__name__}: {e}")
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def _scene_durations(manifest: dict) -> list[float]:
    out: list[float] = []
    for s in manifest.get("scenes", []) or []:
        d = s.get("duration_sec")
        try:
            out.append(float(d))
        except (TypeError, ValueError):
            continue
    return out


def _measure_cut_rhythm(ctx: EvalContext) -> Measurement:
    stage, prop = "compose", "cut_rhythm"
    m = ctx.composition_manifest
    if not m:
        return _err(stage, prop, "composition_manifest missing or unparseable")
    durations = _scene_durations(m)
    if not durations:
        return _err(stage, prop, "no scene durations in composition_manifest")

    value = float(statistics.median(durations))
    flags = []
    for s in m.get("scenes", []) or []:
        d = s.get("duration_sec")
        try:
            dv = float(d)
        except (TypeError, ValueError):
            continue
        if dv < 1.5 or dv > 12.0:
            flags.append(s.get("scene_no"))

    # IQR over the durations (0.0 when n<2 / degenerate).
    try:
        if len(durations) >= 2:
            qs = statistics.quantiles(durations, n=4, method="inclusive")
            iqr = float(qs[2] - qs[0])
        else:
            iqr = 0.0
    except Exception:
        iqr = 0.0

    detail = {
        "flags": flags,
        "iqr": iqr,
        "n": len(durations),
        "min": float(min(durations)),
        "max": float(max(durations)),
    }
    return _ok(stage, prop, value, detail)


def _segment_boundaries(transcript: dict) -> list[float]:
    """Cumulative narration boundaries = each segment's end_sec, in order."""
    bounds: list[float] = []
    for seg in transcript.get("segments", []) or []:
        e = seg.get("end_sec")
        try:
            bounds.append(float(e))
        except (TypeError, ValueError):
            # fall back to start+? — best effort: skip if no end.
            continue
    return bounds


def _measure_av_sync(ctx: EvalContext) -> Measurement:
    stage, prop = "compose", "av_sync"
    m = ctx.composition_manifest
    t = ctx.transcript
    if not m:
        return _err(stage, prop, "composition_manifest missing or unparseable")
    if not t:
        return _err(stage, prop, "narration transcript missing or unparseable")

    durations = _scene_durations(m)
    if not durations:
        return _err(stage, prop, "no scene durations in composition_manifest")
    narr_bounds = _segment_boundaries(t)
    if not narr_bounds:
        return _err(stage, prop, "no narration segment boundaries in transcript")

    # Cumulative visual scene boundaries (running sum of durations).
    visual_bounds: list[float] = []
    acc = 0.0
    for d in durations:
        acc += d
        visual_bounds.append(acc)

    n = min(len(visual_bounds), len(narr_bounds))
    if n == 0:
        return _err(stage, prop, "no comparable scene/narration boundaries")

    per_scene: list[dict] = []
    within = 0
    max_drift = 0.0
    for i in range(n):
        drift = abs(visual_bounds[i] - narr_bounds[i])
        if drift > max_drift:
            max_drift = drift
        ok = drift <= _AV_SYNC_TOL_SEC
        if ok:
            within += 1
        if i < 12:  # keep detail short
            per_scene.append({
                "scene": i + 1,
                "drift_sec": round(drift, 4),
                "within": ok,
            })

    value = float(within) / float(n)
    detail = {
        "max_drift_sec": float(max_drift),
        "tol_sec": _AV_SYNC_TOL_SEC,
        "n_scenes": n,
        "within": within,
        "per_scene": per_scene,
    }
    return _ok(stage, prop, value, detail)


def _measure_layout_integrity(ctx: EvalContext) -> Measurement:
    stage, prop = "compose", "layout_integrity"
    m = ctx.composition_manifest
    if not m:
        return _err(stage, prop, "composition_manifest missing or unparseable")
    summary = m.get("summary")
    if not isinstance(summary, dict):
        return _err(stage, prop, "composition_manifest has no summary")

    try:
        integrity_flags = int(summary.get("integrity_flags", 0) or 0)
        contrast_failures = int(summary.get("contrast_failures", 0) or 0)
    except (TypeError, ValueError) as e:
        return _err(stage, prop, f"non-numeric integrity/contrast counts: {e}")

    value = float(integrity_flags + contrast_failures)
    detail = {
        "integrity_flags": integrity_flags,
        "contrast_failures": contrast_failures,
    }
    return _ok(stage, prop, value, detail)


def _measure_auto_gate_first_pass(ctx: EvalContext) -> Measurement:
    stage, prop = "compose", "auto_gate_first_pass"
    m = ctx.composition_manifest
    if not m:
        return _err(stage, prop, "composition_manifest missing or unparseable")
    summary = m.get("summary")
    if not isinstance(summary, dict):
        return _err(stage, prop, "composition_manifest has no summary")

    try:
        total = float(summary.get("total"))
        gated_ok = float(summary.get("gated_ok"))
    except (TypeError, ValueError):
        return _err(stage, prop, "summary missing total/gated_ok counts")
    if total <= 0:
        return _err(stage, prop, f"total scenes is zero/invalid: {total}")

    value = gated_ok / total
    detail = {
        "auto_gate": summary.get("auto_gate"),
        "gated_ok": gated_ok,
        "total": total,
    }
    return _ok(stage, prop, value, detail)


def _measure_final_runtime(ctx: EvalContext) -> Measurement:
    stage, prop = "render", "final_runtime"
    if not ctx.has_media("video"):
        return _err(stage, prop, f"video not found: {ctx.video}")
    dur = media_duration_sec(ctx.video)
    if dur is None:
        return _err(stage, prop, "ffprobe could not determine video duration")
    return _ok(stage, prop, float(dur), {"source": "ffprobe.format.duration"})


# ---------------------------------------------------------------------------
# Public surface.
# ---------------------------------------------------------------------------

def analyze(ctx: EvalContext) -> list[Measurement]:
    """Measure all video/motion properties for ONE project. Never raises."""
    measurers = (
        _measure_motion_energy,
        _measure_cut_rhythm,
        _measure_av_sync,
        _measure_layout_integrity,
        _measure_auto_gate_first_pass,
        _measure_final_runtime,
    )
    out: list[Measurement] = []
    for fn in measurers:
        try:
            out.append(fn(ctx))
        except Exception as e:  # defense in depth — measurers already guard
            stage, prop = fn.__name__.replace("_measure_", "").split("__", 1)[0], ""
            out.append(make_measurement_error(
                artifact="video.mp4", stage="compose", owner="holistic",
                prop=fn.__name__.replace("_measure_", ""), kind="objective",
                rolls_up_to=(), err=f"unexpected: {type(e).__name__}: {e}"))
    return out
