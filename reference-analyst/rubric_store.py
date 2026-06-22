"""Durable, MERGING rubric memory — one evolving rubric per named "standard".

The whole point of feeding Vera more than one reference is that the targets become
the videos' shared DNA, not one clip's quirks. This module makes that durable across
separate invocations: it keeps the per-video `raw` analyses for a named standard and,
when new videos arrive, rebuilds the banded targets over the UNION of old + new
analyses — reusing the engine's own `build_targets`/`_band` logic, so more references
genuinely TIGHTEN the bands.

It also persists the CEO's `ceo_prefs` answers (new over old) so future videos need
less asking.

Storage: one JSON file per standard slug under `standards/`, written atomically via
chat_state (temp file + os.replace). No atlas import — this stays a pure sibling
module the adapter can drive in-process.
"""
from __future__ import annotations

import pathlib
import re

import chat_state
import reference_engine as engine

HERE = pathlib.Path(__file__).resolve().parent
STANDARDS_DIR = HERE / "standards"


def _slug(name: str) -> str:
    """A filesystem-safe slug for a standard's name ('My House Style' -> 'my-house-style')."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "default"


def rubric_path(standard: str, root: pathlib.Path | str | None = None) -> pathlib.Path:
    root = pathlib.Path(root) if root else STANDARDS_DIR
    return root / f"{_slug(standard)}.json"


def load_rubric(standard: str, root: pathlib.Path | str | None = None) -> dict | None:
    """The saved rubric for `standard`, or None if this is the first reference."""
    path = rubric_path(standard, root)
    data = chat_state.load_json(path, None)
    return data if isinstance(data, dict) else None


def save_rubric(standard: str, rubric: dict,
                root: pathlib.Path | str | None = None) -> pathlib.Path:
    """Persist `rubric` for `standard` atomically; returns the path written."""
    path = rubric_path(standard, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    chat_state.atomic_write_json(path, rubric)
    return path


def validate_videos(video_paths) -> tuple[list[str], list[str]]:
    """Split `video_paths` into (existing, missing) LOCAL files.

    URIs are always local in this repo — no remote fetches. A missing file is NOT a
    crash: the caller degrades with a clear note and works the rest.
    """
    if isinstance(video_paths, (str, pathlib.Path)):
        video_paths = [video_paths]
    existing, missing = [], []
    for p in video_paths:
        (existing if pathlib.Path(p).expanduser().is_file() else missing).append(str(p))
    return existing, missing


def merge_rubric(existing: dict | None, new_analyses: list[dict], *,
                 ceo_prefs: dict | None = None,
                 vision_fn=None) -> dict:
    """Fold `new_analyses` into `existing` and rebuild the banded targets.

    Pure (no I/O): tests drive it with stubbed analyses, no ffmpeg/cv2. Bands are
    recomputed over the UNION of prior + new per-video analyses, so the more
    references accumulate, the tighter and more representative the bands get.
    """
    old_analyses = list((existing or {}).get("raw") or [])
    combined = old_analyses + list(new_analyses or [])

    targets = engine.build_targets(combined)
    judged = engine.build_judged(combined, vision_fn)

    prefs = dict((existing or {}).get("ceo_prefs") or {})
    if ceo_prefs:
        prefs.update({k: v for k, v in ceo_prefs.items() if v not in (None, "")})

    return {
        "schema_version": engine.RUBRIC_VERSION,
        "source_videos": [a.get("video") for a in combined],
        "targets": targets,
        "judged": judged,
        "open_questions": engine._open_questions(targets),
        "ceo_prefs": prefs,
        "raw": combined,
    }


def build_standard(standard: str, video_paths, *, vision_fn=None,
                   ceo_prefs: dict | None = None,
                   work_dir: pathlib.Path | str | None = None,
                   root: pathlib.Path | str | None = None) -> dict:
    """Analyze `video_paths`, MERGE them into `standard`'s saved rubric, persist, return.

    The merge is the durable band-tightening: existing per-video analyses + the new
    ones are rolled back up into shared bands. Frames are saved under the standard's
    folder so the judged seam has something to read.
    """
    existing, missing = validate_videos(video_paths)
    root = pathlib.Path(root) if root else STANDARDS_DIR
    frames_dir = str(pathlib.Path(work_dir) if work_dir
                     else root / _slug(standard) / "frames")

    new_analyses = [engine.analyze_video(p, frames_dir) for p in existing]
    merged = merge_rubric(load_rubric(standard, root), new_analyses,
                          ceo_prefs=ceo_prefs, vision_fn=vision_fn)
    if missing:
        merged["notes"] = (f"{len(missing)} reference file(s) not found and skipped: "
                           + ", ".join(missing))
    save_rubric(standard, merged, root)
    return merged
