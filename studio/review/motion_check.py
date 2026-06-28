"""studio.review.motion_check — THE NO-DEAD-AIR GATE.

GOLDEN_REFERENCE.md's #1 rule ("looks alive on every frame / no dead air"), automated.
This replaces "trust the effect-enum" with "MEASURE the motion": it frame-diffs a DRAFT
render and, per scene, flags the three ways a composition goes dead —

  (a) a trailing STATIC hold  — motion energy ~0 for > ``static_min_hold`` s before a cut
                                (a frozen frame sitting on the seam);
  (b) a silent GAP            — a scene's cut falls before the next scene begins;
  (c) still ANIMATING at cut  — motion is still hot at the cut (a move chopped mid-flight).

It REUSES ``atlas/eval/analyzers/video.py`` (REUSE_MAP.md §5): the same delta-luma
frame-diff technique + sampling constants, and its ``_measure_motion_energy`` /
``_measure_cut_rhythm`` for the whole-render numbers — but run IN-LOOP on the draft and
turned into per-scene PASS/FLAG gates instead of a single post-hoc score. The pipeline
must FLAG (not silently pass) any dead air.

cv2/numpy + the atlas analyzer are imported LAZILY so ``import studio`` stays cheap and
the gate logic stays unit-testable with an injected synthetic motion series.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import config

# Gate thresholds (heuristic; tunable). delta-luma is mean |Δ| per pixel on a 0–255
# scale at a 320px downscale, so "alive" textures (grain drift) keep it comfortably > 0.
STATIC_EPS = 0.6          # diff at/below this is "no motion"
STATIC_MIN_HOLD = 0.5     # a static tail longer than this before a cut is dead air
CUT_MOTION_EPS = 8.0      # diff above this at the cut = still mid-animation
GAP_EPS = 0.05            # tolerance before a scene-to-scene gap counts as silence

# Fallbacks mirroring video.py, used only if the atlas analyzer can't be imported.
_FALLBACK_SAMPLE_FPS = 4.0
_FALLBACK_DOWNSCALE = 320
_FALLBACK_MAX_FRAMES = 1200


# ======================================================================
# scene windows from the VO grid
# ======================================================================
def scene_windows(vo_grid: dict) -> list[dict]:
    """``[{scene_no, start, cut}]`` — each scene spans ``[NS[i], NS[i+1])``; the last
    scene's cut is the composition total (the end of the render)."""
    grid = vo_grid.get("grid") or {}
    NS = [round(float(x), 3) for x in grid.get("NS", [])]
    total = round(float(grid.get("total", NS[-1] if NS else 0.0)), 3)
    scenes = vo_grid.get("scenes") or []
    out = []
    for i in range(len(NS)):
        sc = scenes[i] if i < len(scenes) else {}
        cut = NS[i + 1] if i + 1 < len(NS) else total
        out.append({"scene_no": sc.get("scene_no", i + 1),
                    "start": NS[i], "cut": round(float(cut), 3)})
    return out


# ======================================================================
# the gate — pure, testable with an injected motion series
# ======================================================================
def evaluate_scene_motion(windows: list[dict], series: list[dict], *,
                          static_eps: float = STATIC_EPS,
                          static_min_hold: float = STATIC_MIN_HOLD,
                          cut_motion_eps: float = CUT_MOTION_EPS,
                          gap_eps: float = GAP_EPS) -> dict:
    """Score each scene window against the per-frame motion ``series`` ([{t, diff}]).
    Returns ``{"scenes": [...], "any_flag": bool}`` where each scene carries
    motion_energy, trailing_static_sec, animating_at_cut, gap_after, flags, status."""
    series = sorted(({"t": float(s["t"]), "diff": float(s["diff"])} for s in series),
                    key=lambda s: s["t"])
    rows = []
    for idx, w in enumerate(windows):
        start, cut = float(w["start"]), float(w["cut"])
        inside = [s for s in series if start <= s["t"] < cut]

        motion_energy = (sum(s["diff"] for s in inside) / len(inside)) if inside else 0.0

        # trailing static hold: the contiguous run of ~0-motion samples ending at the cut
        trailing = 0.0
        run_start = None
        for s in reversed(inside):
            if s["diff"] <= static_eps:
                run_start = s["t"]
            else:
                break
        if run_start is not None:
            trailing = round(cut - run_start, 3)

        # still animating at the cut: the last sample before the cut is hot
        animating = bool(inside) and inside[-1]["diff"] > cut_motion_eps

        # silent gap AFTER this scene (next scene starts later than this cut)
        gap_after = 0.0
        if idx + 1 < len(windows):
            gap_after = round(float(windows[idx + 1]["start"]) - cut, 3)

        flags = []
        if trailing > static_min_hold:
            flags.append("trailing_static")
        if animating:
            flags.append("animating_at_cut")
        if gap_after > gap_eps:
            flags.append("silent_gap")
        if not inside:
            flags.append("no_frames")

        rows.append({
            "scene_no": w.get("scene_no", idx + 1),
            "start": round(start, 3), "cut": round(cut, 3),
            "motion_energy": round(motion_energy, 3),
            "trailing_static_sec": trailing,
            "animating_at_cut": animating,
            "gap_after": gap_after,
            "flags": flags,
            "status": "FLAG" if flags else "PASS",
        })

    return {"scenes": rows, "any_flag": any(r["flags"] for r in rows)}


# ======================================================================
# the cv2 frame-diff pass — REUSES video.py's technique + constants
# ======================================================================
def _video_analyzer():
    """Load atlas/eval/analyzers/video.py with atlas/ on the path so its own
    ``import rubric`` / ``from eval.types import …`` resolve. Returns the module or None."""
    import importlib.util
    import sys
    atlas = str((config.REPO_ROOT / "atlas").resolve())
    if atlas not in sys.path:
        sys.path.insert(0, atlas)
    path = config.REPO_ROOT / "atlas" / "eval" / "analyzers" / "video.py"
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("studio_eval_video", str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def frame_diff_series(video_path) -> list[dict]:
    """Per-frame motion series ``[{t, diff}]`` over a render, using the SAME delta-luma
    frame-diff + sampling constants as ``atlas/eval/analyzers/video.py`` (reused when the
    module loads). ``t`` is the wall-clock timestamp of each sampled frame; ``diff`` is
    the mean |Δ-luma| since the previous sampled frame. Returns [] if cv2/the video is
    unavailable (the gate then reports no_frames rather than crashing)."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return []
    vid = _video_analyzer()
    sample_fps = getattr(vid, "_SAMPLE_FPS", _FALLBACK_SAMPLE_FPS)
    downscale = getattr(vid, "_DOWNSCALE_WIDTH", _FALLBACK_DOWNSCALE)
    max_frames = getattr(vid, "_MAX_SAMPLED_FRAMES", _FALLBACK_MAX_FRAMES)

    path = Path(video_path)
    if not path.is_file():
        return []
    cap = None
    try:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            return []
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        step = max(1, round(fps / sample_fps)) if fps > 0 else 1
        out: list[dict] = []
        prev = None
        pos = sampled = 0
        fails = 0
        while sampled < max_frames:
            if not cap.grab():
                fails += 1
                if fails > 5:
                    break
                continue
            fails = 0
            if pos % step != 0:
                pos += 1
                continue
            pos += 1
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                continue
            sampled += 1
            t = (cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0) / 1000.0
            h, w = frame.shape[:2]
            if w > downscale and w > 0:
                nh = max(1, int(round(h * downscale / w)))
                frame = cv2.resize(frame, (downscale, nh), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
            if prev is not None:
                out.append({"t": round(t, 4),
                            "diff": float(np.mean(np.abs(gray - prev)))})
            prev = gray
        return out
    except Exception:
        return []
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def global_measures(video_path, vo_grid: dict) -> dict:
    """REUSE video.py's whole-render measurers (motion_energy + cut_rhythm) by feeding
    them a duck-typed EvalContext built from the draft + the VO grid. Best-effort:
    returns {} if the analyzer/cv2 are unavailable."""
    vid = _video_analyzer()
    if vid is None:
        return {}
    scenes = vo_grid.get("scenes") or []
    NS = (vo_grid.get("grid") or {}).get("NS") or []
    durs = []
    for i in range(len(NS)):
        cut = NS[i + 1] if i + 1 < len(NS) else (vo_grid.get("grid") or {}).get("total", NS[i])
        durs.append({"scene_no": scenes[i].get("scene_no", i + 1) if i < len(scenes) else i + 1,
                     "duration_sec": round(float(cut) - float(NS[i]), 3)})
    manifest = {"scenes": durs}

    class _Ctx:
        def __init__(self, v, m):
            self.video = Path(v) if v else None
            self.composition_manifest = m
            self.transcript = None

        def has_media(self, _kind):
            return self.video is not None and self.video.exists()

    ctx = _Ctx(video_path, manifest)
    out = {}
    try:
        out["motion_energy"] = vid._measure_motion_energy(ctx).value
    except Exception:
        out["motion_energy"] = None
    try:
        out["cut_rhythm"] = vid._measure_cut_rhythm(ctx).value
    except Exception:
        out["cut_rhythm"] = None
    return out


# ======================================================================
# orchestrator + table
# ======================================================================
def _find_draft_render(slug: str, pdir: Path):
    """Newest plausible draft render for a slug: project dir, then repo renders/."""
    candidates: list[Path] = []
    for d in (pdir / "renders", pdir, config.REPO_ROOT / "renders"):
        if d.is_dir():
            candidates += [p for p in d.glob("*.mp4")]
            candidates += [p for p in d.glob(f"{slug}*.mp4")]
    candidates = [p for p in candidates if p.is_file()]
    if not candidates:
        return None
    # newest by mtime
    return max(candidates, key=lambda p: p.stat().st_mtime)


def motion_check(slug: str, *, video=None, series_fn=None, reuse_global: bool = True,
                 **gate_kwargs) -> dict:
    """Run the no-dead-air gate on a draft render. Loads the project's vo.grid.json for
    the scene windows, frame-diffs the render (or an injected ``series_fn``), evaluates
    the per-scene flags, and (best-effort) attaches the reused whole-render measures.
    Returns the report dict; ``report["any_flag"]`` is the pipeline's FLAG signal."""
    pdir = config.PROJECTS_DIR / slug
    grid_path = pdir / "vo.grid.json"
    if not grid_path.is_file():
        raise FileNotFoundError(
            f"no vo.grid.json for '{slug}' — run the VO stage first (studio.vo.produce_vo)")
    vo_grid = json.loads(grid_path.read_text(encoding="utf-8"))
    windows = scene_windows(vo_grid)

    if video is None:
        video = _find_draft_render(slug, pdir)
    series = series_fn(video) if series_fn else frame_diff_series(video)

    report = evaluate_scene_motion(windows, series, **gate_kwargs)
    report["slug"] = slug
    report["video"] = str(video) if video else None
    report["frames_sampled"] = len(series)
    report["global"] = global_measures(video, vo_grid) if (reuse_global and video) else {}
    return report


def format_table(report: dict) -> str:
    """A per-scene PASS/FLAG table (the gate's human-readable verdict)."""
    lines = []
    g = report.get("global") or {}
    head = f"NO-DEAD-AIR GATE — {report.get('slug', '?')}"
    if report.get("video"):
        head += f"  ({Path(report['video']).name})"
    lines.append(head)
    if g:
        me = g.get("motion_energy")
        cr = g.get("cut_rhythm")
        lines.append(f"  whole-render (via eval/analyzers/video.py): "
                     f"motion_energy={me if me is None else round(me, 3)}  "
                     f"cut_rhythm={cr if cr is None else round(cr, 3)}s")
    lines.append("")
    lines.append(f"  {'scene':>5} {'window':>14} {'motion':>7} {'tail_static':>11} "
                 f"{'@cut':>5} {'gap':>5}  {'verdict':<6} flags")
    for s in report.get("scenes", []):
        win = f"{s.get('start', 0.0):.2f}-{s.get('cut', 0.0):.2f}"
        mark = "✓ PASS" if s["status"] == "PASS" else "✗ FLAG"
        anim = "hot" if s["animating_at_cut"] else "-"
        lines.append(
            f"  {s['scene_no']:>5} {win:>14} {s['motion_energy']:>7.2f} "
            f"{s['trailing_static_sec']:>11.2f} {anim:>5} {s['gap_after']:>5.2f}  "
            f"{mark:<6} {','.join(s['flags']) or '-'}")
    flagged = [s["scene_no"] for s in report.get("scenes", []) if s["flags"]]
    lines.append("")
    if report.get("any_flag"):
        lines.append(f"  RESULT: ✗ DEAD AIR FLAGGED on scene(s) {flagged} — re-author before render.")
    else:
        lines.append("  RESULT: ✓ no dead air — every scene stays alive through its cut.")
    return "\n".join(lines)
