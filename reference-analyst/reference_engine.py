#!/usr/bin/env python3
"""
reference_engine.py -- the Reference Analyst's engine.

Pure + injectable (no project imports; no network beyond LOCAL FFmpeg):
given one or more reference videos it measures *objective* quality
properties with FFmpeg + OpenCV, saves representative frames for the
*judged* properties (handed to an injectable vision seam / the CEO), and
emits a RUBRIC -- the measurable target the rest of the pipeline aims at.

Feed more videos -> targets become shared BANDS (the common DNA), not the
quirks of any single clip.

Convention match (per PROJECT_CONTEXT):
  - objective half runs offline + deterministically (FFmpeg/cv2 only)
  - the LLM/vision judgement is a seam argument (`vision_fn`)
  - every external call is wrapped; failure degrades, never crashes

A NOTE ON THE JUDGED LAYER (Vera's correction to the generic engine): a
*reference* video has no script, so there is nothing to score visual/narration
ALIGNMENT against -- alignment is about evaluating the system's OWN generated
output, which is out of scope here. So the judged layer is a STYLE PROFILE: the
visual style, typography character, motion feel, mood, and observed layout types
that become style *targets*. (See `JUDGED_NEEDS`.)
"""
from __future__ import annotations
import json, os, re, subprocess, statistics, tempfile
from typing import Callable, Optional

import numpy as np
try:
    import cv2
except Exception:                      # degrade visual metrics if cv2 absent
    cv2 = None

RUBRIC_VERSION = "reference_rubric/1.0"

# The judged layer's targets for a REFERENCE video: a style profile, NOT alignment.
# (visual_narration_alignment is deliberately dropped — see the module docstring.)
JUDGED_NEEDS = ["visual_style", "typography_character", "motion_feel", "mood",
                "layout_types"]


# ----------------------------- helpers --------------------------------------
def _run(cmd: list[str], timeout: int = 180) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", str(e)

def _f(x):
    try:
        return round(float(x), 2)
    except Exception:
        return None

def _dig(obj, keys):
    for k in keys:
        obj = obj.get(k) if isinstance(obj, dict) else None
        if obj is None:
            return None
    return obj

def _band(vals, pad: float = 0.15):
    vals = [v for v in vals if isinstance(v, (int, float))]
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    if lo == hi:                        # single video -> pad to a soft band
        span = abs(lo) * pad or 0.1
        lo, hi = lo - span, hi + span
    return [round(lo, 3), round(hi, 3)]

def _t(values):
    """A target = central value + an acceptable band (what the Coach chases)."""
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return {"value": None, "band": None}
    return {"value": round(statistics.mean(vals), 3), "band": _band(vals)}


# --------------------------- FFmpeg probes ----------------------------------
def _probe(path: str) -> dict:
    info = {"duration_sec": None, "fps": None, "width": None, "height": None, "has_audio": False}
    rc, out, _ = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                       "-show_entries", "stream=r_frame_rate,width,height",
                       "-show_entries", "format=duration", "-of", "json", path])
    if rc == 0:
        d = json.loads(out or "{}")
        st = (d.get("streams") or [{}])[0]
        info["width"], info["height"] = st.get("width"), st.get("height")
        try:
            n, dn = st.get("r_frame_rate", "0/1").split("/")
            info["fps"] = round(float(n) / float(dn), 2) if float(dn) else None
        except Exception:
            pass
        info["duration_sec"] = _f(d.get("format", {}).get("duration"))
    rc, out, _ = _run(["ffprobe", "-v", "error", "-select_streams", "a",
                       "-show_entries", "stream=index", "-of", "json", path])
    if rc == 0:
        info["has_audio"] = bool(json.loads(out or "{}").get("streams"))
    return info

def _detect_cuts(path: str, threshold: float = 0.30) -> list[float]:
    rc, _, err = _run(["ffmpeg", "-i", path, "-filter:v",
                       f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"])
    return [float(m) for m in re.findall(r"pts_time:([\d.]+)", err)]

def _loudness(path: str) -> dict:
    rc, _, err = _run(["ffmpeg", "-i", path, "-af", "loudnorm=print_format=json",
                       "-f", "null", "-"])
    out = {"integrated_lufs": None, "loudness_range": None, "true_peak_db": None}
    blocks = re.findall(r"\{[^{}]+\}", err)
    if blocks:
        try:
            j = json.loads(blocks[-1])
            out["integrated_lufs"] = _f(j.get("input_i"))
            out["loudness_range"]  = _f(j.get("input_lra"))
            out["true_peak_db"]    = _f(j.get("input_tp"))
        except Exception:
            pass
    return out

def _silences(path: str, dur: float, noise: str = "-30dB", min_d: float = 0.3) -> dict:
    rc, _, err = _run(["ffmpeg", "-i", path, "-af",
                       f"silencedetect=noise={noise}:d={min_d}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: ([\-\d.]+)", err)]
    ends   = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    pauses = [max(0.0, e - s) for s, e in zip(starts, ends)]
    sil = sum(pauses)
    speech = round(min(1.0, max(0.0, 1 - sil / dur)), 3) if dur else None
    return {"speech_ratio": speech,
            "avg_pause_sec": round(statistics.mean(pauses), 2) if pauses else 0.0,
            "n_pauses": len(pauses)}


# --------------------------- OpenCV frame work ------------------------------
def _sample_frames(path: str, n: int = 24):
    if cv2 is None:
        return []
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    out = []
    if total > 1:
        for i in np.linspace(0, total - 1, min(n, total)).astype(int):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
            ok, f = cap.read()
            if ok:
                out.append(f)
    else:                                # unknown length -> stream + subsample
        frames = []
        while True:
            ok, f = cap.read()
            if not ok:
                break
            frames.append(f)
        if frames:
            for i in np.linspace(0, len(frames) - 1, min(n, len(frames))).astype(int):
                out.append(frames[int(i)])
    cap.release()
    return out

def _palette(frames, k: int = 5) -> dict:
    if cv2 is None or not frames:
        return {"palette": [], "saturation": None, "brightness": None}
    px = np.vstack([cv2.resize(f, (48, 48)).reshape(-1, 3) for f in frames]).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
    _, labels, centers = cv2.kmeans(px, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.flatten(), minlength=k)
    palette = []
    for i in np.argsort(-counts):
        b, g, r = centers[i].astype(int)
        palette.append({"hex": f"#{r:02x}{g:02x}{b:02x}",
                        "weight": round(float(counts[i]) / counts.sum(), 3)})
    hsv = cv2.cvtColor(px.reshape(1, -1, 3).astype(np.uint8), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    return {"palette": palette,
            "saturation": round(float(hsv[:, 1].mean()) / 255, 3),
            "brightness": round(float(hsv[:, 2].mean()) / 255, 3)}

def _motion(frames) -> Optional[float]:
    if cv2 is None or len(frames) < 2:
        return None
    g = [cv2.cvtColor(cv2.resize(f, (96, 54)), cv2.COLOR_BGR2GRAY).astype(np.int16) for f in frames]
    diffs = [np.abs(g[i + 1] - g[i]).mean() for i in range(len(g) - 1)]
    return round(float(np.mean(diffs)) / 255, 4)


# --------------------- one video -> objective measurements ------------------
def analyze_video(path: str, frames_dir: Optional[str] = None) -> dict:
    pr = _probe(path)
    dur = pr["duration_sec"] or 0.0
    cuts = _detect_cuts(path)
    bounds = [0.0] + cuts + ([dur] if dur else [])
    shots = [b - a for a, b in zip(bounds, bounds[1:]) if b > a]
    pacing = {"shot_count": len(shots) if shots else 1,
              "avg_shot_sec": round(statistics.mean(shots), 2) if shots else dur,
              "cuts_per_min": round(len(cuts) / dur * 60, 2) if dur else 0.0}

    frames = _sample_frames(path)
    color = _palette(frames)
    motion = _motion(frames)

    audio = {"integrated_lufs": None, "loudness_range": None, "true_peak_db": None,
             "speech_ratio": None, "avg_pause_sec": None}
    if pr["has_audio"]:
        audio.update(_loudness(path))
        audio.update(_silences(path, dur))

    saved = []
    if frames_dir and cv2 is not None and frames:
        os.makedirs(frames_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(path))[0]
        for i, f in enumerate(frames[:: max(1, len(frames) // 6)][:6]):
            p = os.path.join(frames_dir, f"{stem}_f{i}.jpg")
            cv2.imwrite(p, f)
            saved.append(p)

    return {"video": os.path.basename(path), "container": pr, "pacing": pacing,
            "motion_score": motion, "color": color, "audio": audio, "frames": saved}


# --------------------- open questions for the CEO ---------------------------
def _open_questions(targets) -> list[dict]:
    qs = []
    aps = _dig(targets, ["pacing", "avg_shot_sec", "value"])
    if aps is not None:
        qs.append({"id": "pace", "sets": "pacing.avg_shot_sec",
                   "plain": f"The reference cuts about every {aps:.1f}s. "
                            f"Do you want that same snappiness, or more breathing room?"})
    sr = _dig(targets, ["audio", "speech_ratio", "value"])
    if sr is not None:
        qs.append({"id": "talk", "sets": "audio.speech_ratio",
                   "plain": f"It's talking ~{int(sr*100)}% of the time. Wall-to-wall "
                            f"narration, or more room for music and visual beats?"})
    qs.append({"id": "style", "sets": "judged.style_match",
               "plain": "Looking at the frames I saved -- what about the look should we "
                        "keep? (palette, text style, how kinetic it feels)"})
    return qs


# --------------------- analyses -> targets / judged (reusable) ---------------
# Extracted from build_rubric so the durable, MERGING rubric store (rubric_store.py)
# can recompute shared bands over the union of OLD + NEW per-video analyses by reusing
# the exact same band logic. Single source of truth for the rubric shape.
def build_targets(analyses: list[dict]) -> dict:
    """Roll a list of per-video `analyze_video` results up into banded targets.

    Each {value, band} is an optimization target: feeding MORE references widens the
    inputs to `_t`/`_band`, so the bands become the videos' shared DNA, not one clip's
    quirks. (More videos -> tighter, more representative bands.)
    """
    g = lambda *ks: [_dig(a, ks) for a in analyses]
    return {
        "pacing": {"avg_shot_sec": _t(g("pacing", "avg_shot_sec")),
                   "cuts_per_min": _t(g("pacing", "cuts_per_min")),
                   "shot_count":   _t(g("pacing", "shot_count"))},
        "motion": {"kinetic_score": _t(g("motion_score"))},
        "color":  {"saturation": _t(g("color", "saturation")),
                   "brightness": _t(g("color", "brightness")),
                   "palette_samples": [a.get("color", {}).get("palette", []) for a in analyses]},
        "audio":  {"integrated_lufs": _t(g("audio", "integrated_lufs")),
                   "loudness_range":  _t(g("audio", "loudness_range")),
                   "true_peak_db":    _t(g("audio", "true_peak_db")),
                   "speech_ratio":    _t(g("audio", "speech_ratio")),
                   "avg_pause_sec":   _t(g("audio", "avg_pause_sec"))},
        "structure": {"duration_sec": _t(g("container", "duration_sec")),
                      "fps":          _t(g("container", "fps"))},
    }


def build_judged(analyses: list[dict], vision_fn: Optional[Callable] = None) -> dict:
    """The judged STYLE-PROFILE layer over the saved frames (see module docstring).

    vision_fn(frames) -> a style-profile assessment dict. It is the injected seam: the
    objective half never needs it, and a failure degrades to status 'draft' + an
    error note, never a crash.
    """
    judged = {"status": "pending" if vision_fn is None else "draft",
              "needs": list(JUDGED_NEEDS),
              "frames": sorted({f for a in analyses for f in a.get("frames", [])})}
    if vision_fn is not None:
        try:
            judged["assessment"] = vision_fn(judged["frames"])
            judged["status"] = "scored"
        except Exception as e:
            judged["error"] = str(e)
    return judged


# --------------------- N videos -> the rubric -------------------------------
def build_rubric(video_paths, vision_fn: Optional[Callable] = None,
                 ceo_prefs: Optional[dict] = None, work_dir: Optional[str] = None) -> dict:
    work_dir = work_dir or tempfile.mkdtemp(prefix="refrubric_")
    frames_dir = os.path.join(work_dir, "frames")
    analyses = [analyze_video(p, frames_dir) for p in video_paths]

    targets = build_targets(analyses)
    judged = build_judged(analyses, vision_fn)

    return {"schema_version": RUBRIC_VERSION,
            "source_videos": [a["video"] for a in analyses],
            "targets": targets,
            "judged": judged,
            "open_questions": _open_questions(targets),
            "ceo_prefs": ceo_prefs or {},
            "raw": analyses}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Analyze reference video(s) -> quality rubric")
    ap.add_argument("videos", nargs="+", help="one or more reference video files")
    ap.add_argument("--out", default="rubric.json")
    ap.add_argument("--work", default=None)
    a = ap.parse_args()
    rub = build_rubric(a.videos, work_dir=a.work)
    with open(a.out, "w") as fh:
        json.dump(rub, fh, indent=2)
    print(json.dumps({k: v for k, v in rub.items() if k != "raw"}, indent=2))
    print(f"\n[rubric -> {a.out}]  [frames -> {len(rub['judged']['frames'])} saved]")
