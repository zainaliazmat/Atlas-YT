"""studio.review.evidence — the EVIDENCE pass of the in-loop multi-critic review.

The review's #1 discipline (GOLDEN_REFERENCE.md anti-pattern #3: *never looking at
frames produces flat output*) is that every critique is GROUNDED in measured evidence,
not vibes. This module gathers that evidence ONCE per draft so the seven critics
(``studio.review.critics``) all argue from the same shared, measured pack:

  - per-scene SAMPLED FRAMES — one at each scene's midpoint AND one straddling each
    transition (the two frames most likely to expose a frozen hold, a collision, or a
    text card that flips before it can be read);
  - per-scene DURATIONS (ffprobe via the render + the VO grid);
  - LOUDNESS + CLIPPING — integrated LUFS + true-peak dBTP (reuses the eval audio
    analyzer's ``_ebur128``); clipping is true-peak at/above the −1 dBTP ceiling;
  - the per-frame MOTION SERIES + whole-render motion/cut measures (REUSE
    ``studio.review.motion_check`` → which itself reuses ``eval/analyzers/video.py``);
  - the composition source (``index.html``) so the technical-determinism critic can grep
    it and the auto-apply editor can locate scene blocks;
  - a POLISH-VS-REFERENCE anchor — a pairwise forced-choice ("is our draft more polished
    than the bar?") modelled on ``eval/judged.py``'s discipline (pairwise, order-
    randomised, ensembled) but run vision-native against the reference frames, so the
    holistic "are we at the bar?" question has a number, not an opinion.

Everything degrades gracefully: a missing render, absent cv2/ffmpeg, or an unreadable
artifact yields an evidence pack with empty/None fields and an ``errors`` list — it NEVER
raises, so a flaky toolchain flags rather than crashes the review. Heavy deps (cv2, the
eval analyzers, the LLM) are imported lazily so ``import studio`` stays cheap.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .. import config
from . import motion_check as mc

# True-peak at/above this dBTP ceiling counts as clipping risk (EBU R128 / −1 dBTP).
CLIP_DBTP = -1.0
# Where the per-draft review frames are written (separate from the pipeline snapshots).
REVIEW_FRAMES_SUBDIR = "snapshots/review"
# Default reference pack the polish anchor compares against.
DEFAULT_REFERENCE = "dark-truth-social"


# ======================================================================
# ffmpeg / ffprobe helpers (located lazily; absence degrades, never raises)
# ======================================================================
def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _ffprobe() -> str | None:
    return shutil.which("ffprobe")


def probe_duration(video_path) -> float | None:
    """Total render duration in seconds via ffprobe (None if unavailable)."""
    ffprobe = _ffprobe()
    path = Path(video_path) if video_path else None
    if not ffprobe or not path or not path.is_file():
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return round(float(out.stdout.strip()), 3)
    except Exception:
        return None


def sample_timestamps(windows: list[dict]) -> list[dict]:
    """The frame-grab plan: a midpoint frame per scene + a transition frame straddling
    each cut. Returns ``[{kind, scene_no, t, label}]`` sorted by ``t``. Pure (no I/O),
    so the sampling policy is unit-testable without a render."""
    plan: list[dict] = []
    for w in windows:
        start, cut = float(w["start"]), float(w["cut"])
        sc = w.get("scene_no")
        mid = round((start + cut) / 2.0, 3)
        plan.append({"kind": "mid", "scene_no": sc, "t": mid,
                     "label": f"s{sc:02d}-mid-{mid:.1f}s"})
    # transition frames: just AFTER each cut except the final one (the seam itself)
    for i in range(len(windows) - 1):
        cut = float(windows[i]["cut"])
        a, b = windows[i].get("scene_no"), windows[i + 1].get("scene_no")
        t = round(cut + 0.05, 3)
        plan.append({"kind": "transition", "scene_no": a, "to_scene": b, "t": t,
                     "label": f"t{a:02d}-{b:02d}-{t:.1f}s"})
    return sorted(plan, key=lambda p: p["t"])


def extract_frames(video_path, plan: list[dict], out_dir: Path) -> list[dict]:
    """Grab one PNG per planned timestamp via ffmpeg ``-ss``. Returns the plan rows with
    a ``path`` added (or ``path=None`` + ``error`` when a grab fails). Best-effort: a
    missing ffmpeg/render yields every row path=None rather than raising."""
    ffmpeg = _ffmpeg()
    path = Path(video_path) if video_path else None
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for p in plan:
        row = dict(p)
        dst = out_dir / f"{p['label']}.png"
        if ffmpeg and path and path.is_file():
            try:
                rc = subprocess.run(
                    [ffmpeg, "-y", "-ss", f"{float(p['t']):.3f}", "-i", str(path),
                     "-frames:v", "1", "-q:v", "2", str(dst)],
                    capture_output=True, text=True, timeout=60,
                )
                row["path"] = str(dst) if (rc.returncode == 0 and dst.is_file()) else None
                if row["path"] is None:
                    row["error"] = (rc.stderr or "ffmpeg produced no frame").strip()[-200:]
            except Exception as exc:  # noqa: BLE001
                row["path"] = None
                row["error"] = str(exc)
        else:
            row["path"] = None
            row["error"] = "ffmpeg or render unavailable"
        rows.append(row)
    return rows


# ======================================================================
# loudness + clipping (REUSE eval/analyzers/audio.py _ebur128)
# ======================================================================
def _audio_analyzer():
    """Load ``atlas/eval/analyzers/audio.py`` with atlas/ on the path (mirrors
    motion_check._video_analyzer). Returns the module or None."""
    import importlib.util
    import sys
    atlas = str((config.REPO_ROOT / "atlas").resolve())
    if atlas not in sys.path:
        sys.path.insert(0, atlas)
    path = config.REPO_ROOT / "atlas" / "eval" / "analyzers" / "audio.py"
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("studio_eval_audio", str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def measure_loudness(video_path) -> dict:
    """Integrated loudness (LUFS) + true-peak (dBTP) + a clipping flag for the render's
    audio, reusing the eval audio analyzer's ``_ebur128``. Returns
    ``{integrated_lufs, true_peak_dbtp, clipping, error}``; all-None + error if the
    analyzer/ffmpeg/audio are unavailable."""
    path = Path(video_path) if video_path else None
    if not path or not path.is_file():
        return {"integrated_lufs": None, "true_peak_dbtp": None,
                "clipping": None, "error": "no render"}
    aud = _audio_analyzer()
    if aud is None or not hasattr(aud, "_ebur128"):
        return {"integrated_lufs": None, "true_peak_dbtp": None,
                "clipping": None, "error": "audio analyzer unavailable"}
    try:
        integrated, true_peak, err = aud._ebur128(path)
    except Exception as exc:  # noqa: BLE001
        return {"integrated_lufs": None, "true_peak_dbtp": None,
                "clipping": None, "error": str(exc)}
    clipping = (true_peak is not None) and (true_peak >= CLIP_DBTP)
    return {"integrated_lufs": integrated, "true_peak_dbtp": true_peak,
            "clipping": clipping, "error": err or None}


# ======================================================================
# polish-vs-reference anchor (discipline from eval/judged.py, vision-native)
# ======================================================================
def _reference_frames(reference: str, limit: int = 4) -> list[str]:
    """Reference still frames to anchor polish against — the pack's own snapshots /
    thumbnails / render stills. Best-effort; empty if the pack has none."""
    base = config.REPO_ROOT / "reference" / reference
    found: list[Path] = []
    for sub in ("snapshots", ".thumbnails", "renders"):
        d = base / sub
        if d.is_dir():
            found += sorted(d.glob("*.png")) + sorted(d.glob("*.jpg"))
    return [str(p) for p in found[:limit]]


def polish_vs_reference(our_frames: list[str], reference: str = DEFAULT_REFERENCE, *,
                        vision_fn=None, n: int = 3, seed: int = 7) -> dict:
    """Pairwise forced-choice "is our draft as polished as the bar?", modelled on
    ``eval/judged.py`` (pairwise-vs-reference, order-randomised, ensembled) but run
    vision-native: each vote shows the judge OUR frames vs the REFERENCE frames and asks
    which set looks more like a finished, premium motion-graphics video. Returns
    ``{rate, votes, n, reference, error}`` where ``rate`` is the fraction of votes our
    draft won (1.0 = consistently judged at/above the bar). Best-effort → rate=None."""
    import random as _random
    ref_frames = _reference_frames(reference)
    our_frames = [f for f in our_frames if f]
    if not our_frames or not ref_frames:
        return {"rate": None, "votes": [], "n": 0, "reference": reference,
                "error": "missing our/reference frames"}
    if vision_fn is None:
        from .vision import vision_chat as vision_fn  # lazy default seam

    # reuse judged.py's winner parser so A/B handling is identical
    try:
        import sys
        atlas = str((config.REPO_ROOT / "atlas").resolve())
        if atlas not in sys.path:
            sys.path.insert(0, atlas)
        from eval.judged import _parse_winner  # type: ignore
    except Exception:
        def _parse_winner(reply: str):  # noqa: ANN001
            up = (reply or "").upper()
            if "WINNER: A" in up:
                return "A"
            if "WINNER: B" in up:
                return "B"
            return None

    system = (
        "You are a rigorous, decisive video-quality judge for a premium YouTube studio. "
        "You will see two sets of still frames, A and B, from two motion-graphics videos. "
        "Decide which set looks more like a FINISHED, premium, on-brand video — "
        "composition, type, contrast, texture, polish. You MUST pick a winner; no ties. "
        "Order is randomised and meaningless. "
        "Reply with EXACTLY one line: 'WINNER: A' or 'WINNER: B'.")
    rng = _random.Random(seed)
    votes: list[dict] = []
    wins = 0
    for i in range(n):
        ours_is_a = rng.random() < 0.5
        imgs = (our_frames + ref_frames) if ours_is_a else (ref_frames + our_frames)
        na = len(our_frames) if ours_is_a else len(ref_frames)
        user = (f"Set A = the first {na} frames. Set B = the remaining frames.\n"
                "Which set looks more like a finished, premium, on-brand video? "
                "Reply EXACTLY 'WINNER: A' or 'WINNER: B'.")
        try:
            reply = vision_fn(system, user, imgs)
            winner = _parse_winner(reply)
            ours_won = (winner == "A") == ours_is_a if winner else None
        except Exception as exc:  # noqa: BLE001
            winner, ours_won, reply = None, None, f"error: {exc}"
        if ours_won:
            wins += 1
        votes.append({"ours_is_a": ours_is_a, "winner": winner, "ours_won": ours_won})
    counted = [v for v in votes if v["ours_won"] is not None]
    rate = round(wins / len(counted), 3) if counted else None
    return {"rate": rate, "votes": votes, "n": len(counted),
            "reference": reference, "error": None if counted else "no countable votes"}


# ======================================================================
# the evidence pack
# ======================================================================
def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def collect_evidence(slug: str, *, video=None, series_fn=None, reference: str = DEFAULT_REFERENCE,
                     vision_fn=None, polish: bool = True) -> dict:
    """Gather the full evidence pack for a draft. Loads the project's vo.grid.json (scene
    windows + per-scene text), finds the newest draft render, samples frames, measures
    durations/loudness/motion, loads index.html, and (optionally) computes the polish
    anchor. Returns the pack dict; never raises — failures land in ``pack['errors']``."""
    pdir = config.PROJECTS_DIR / slug
    errors: list[str] = []

    grid_path = pdir / "vo.grid.json"
    if not grid_path.is_file():
        raise FileNotFoundError(
            f"no vo.grid.json for '{slug}' — run the VO stage first (studio.vo.produce_vo)")
    vo_grid = json.loads(grid_path.read_text(encoding="utf-8"))
    windows = mc.scene_windows(vo_grid)
    script = _load_json(pdir / "script.json") or {}

    if video is None:
        video = mc._find_draft_render(slug, pdir)
    if not video:
        errors.append("no draft render found")

    # frames
    plan = sample_timestamps(windows)
    frames = extract_frames(video, plan, pdir / REVIEW_FRAMES_SUBDIR)
    mid_frames = [f["path"] for f in frames if f["kind"] == "mid" and f["path"]]

    # per-frame motion series + whole-render measures (reused gate machinery)
    series = series_fn(video) if series_fn else mc.frame_diff_series(video)
    motion = mc.evaluate_scene_motion(windows, series)
    global_measures = mc.global_measures(video, vo_grid) if video else {}

    # durations: render total + per-scene windows
    render_dur = probe_duration(video)
    scene_durations = [{"scene_no": w.get("scene_no"),
                        "start": w["start"], "cut": w["cut"],
                        "duration_sec": round(w["cut"] - w["start"], 3)} for w in windows]

    loudness = measure_loudness(video)

    index_html = pdir / "index.html"
    html_text = index_html.read_text(encoding="utf-8") if index_html.is_file() else ""
    if not html_text:
        errors.append("no index.html")

    polish_anchor = {}
    if polish:
        try:
            polish_anchor = polish_vs_reference(mid_frames, reference, vision_fn=vision_fn)
        except Exception as exc:  # noqa: BLE001
            polish_anchor = {"rate": None, "error": str(exc)}

    # per-scene rollup the critics consume directly: text + measures + its frames
    scenes = []
    motion_by_no = {r["scene_no"]: r for r in motion["scenes"]}
    grid_scenes = {s.get("scene_no", i + 1): s
                   for i, s in enumerate(vo_grid.get("scenes") or [])}
    frames_by_scene: dict = {}
    for f in frames:
        frames_by_scene.setdefault(f["scene_no"], []).append(
            {"kind": f["kind"], "t": f["t"], "path": f.get("path")})
    for w in windows:
        no = w.get("scene_no")
        gs = grid_scenes.get(no, {})
        scenes.append({
            "scene_no": no,
            "start": w["start"], "cut": w["cut"],
            "duration_sec": round(w["cut"] - w["start"], 3),
            "narration": gs.get("narration", ""),
            "on_screen_text": gs.get("on_screen_text", ""),
            "motion": motion_by_no.get(no, {}),
            "frames": frames_by_scene.get(no, []),
        })

    return {
        "slug": slug,
        "video": str(video) if video else None,
        "reference": reference,
        "render_duration_sec": render_dur,
        "frames": frames,
        "scenes": scenes,
        "scene_durations": scene_durations,
        "motion": motion,
        "global": global_measures,
        "loudness": loudness,
        "polish_vs_reference": polish_anchor,
        "script": {"hook": script.get("hook"), "cta": script.get("cta"),
                   "working_title": script.get("working_title"),
                   "scenes": script.get("scenes", [])},
        "index_html": html_text,
        "index_html_path": str(index_html) if index_html.is_file() else None,
        "errors": errors,
    }
