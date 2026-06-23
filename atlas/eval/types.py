"""Shared types + helpers for the evaluation foundation (Phase 1).

This is the seam every analyzer codes to. An *analyzer* is a pure function

    analyze(ctx: EvalContext) -> list[Measurement]

that reads the on-disk artifacts of ONE completed project and returns raw
measurements. Analyzers MEASURE; they do not decide pass/fail — the CEO-owned
rubric (atlas/rubric/) decides, in the roll-up. This keeps the privilege
asymmetry intact: an analyzer never imports a band, so it can never quietly
move one.

Two hard rules (from the design docs):
  * Objective measurement is deterministic Python + ffmpeg/ffprobe — NO LLM.
    Only explicitly judged properties touch the LLM, and those live in
    atlas/eval/judged.py (ensembled, never single-shot).
  * Graceful degradation. A missing artifact or a broken ffprobe call yields a
    Measurement with `value=None` and an `error` string — it never raises. The
    Inspector must be able to score a half-finished project without crashing.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field, asdict
from functools import cached_property
from pathlib import Path
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Measurement: the one currency every analyzer returns.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Measurement:
    """One measured property of one artifact.

    `value` is the raw measured number/bool (None when not measurable). The
    rubric roll-up compares it to a band to derive pass/fail — that is NOT
    decided here. `stage` + `prop` form the key the rubric is looked up by
    (band id = f"{stage}:{prop}"), so analyzer authors must use the exact stage
    + property keys listed in atlas/rubric/rubric.json.
    """

    artifact: str                 # e.g. "script.json", "audio/master.wav"
    stage: str                    # owning pipeline stage key, e.g. "narration"
    owner: str                    # display owner, e.g. "Cadence"
    prop: str                     # property key, e.g. "speech_cadence"
    value: Optional[float]        # raw measured value (bool/int are floats here); None if unmeasurable
    kind: str                     # "objective" | "judged"
    rolls_up_to: tuple[str, ...]  # global dims, e.g. ("G1",) or ("F",) for the floor
    unit: str = ""                # "wpm", "LUFS", "sec", "ratio", "bool", ...
    detail: dict = field(default_factory=dict)   # variance, raw subvalues, flags
    error: Optional[str] = None   # set (with value=None) when measurement degraded

    def to_row(self) -> dict:
        d = asdict(self)
        d["rolls_up_to"] = list(self.rolls_up_to)
        return d


# An analyzer is any callable taking the context and returning measurements.
Analyzer = Callable[["EvalContext"], list[Measurement]]


# ---------------------------------------------------------------------------
# EvalContext: lazy, graceful access to one project's artifacts + media.
# ---------------------------------------------------------------------------

# artifact-name -> on-disk relative path within a project dir.
ARTIFACT_PATHS: dict[str, str] = {
    "project": "project.json",
    "research_brief": "research_brief.json",
    "script": "script.json",
    "factcheck_report": "factcheck_report.json",
    "style_guide": "style_guide.json",
    "storyboard": "storyboard.json",
    "asset_manifest": "asset_manifest.json",
    "composition_manifest": "composition_manifest.json",
    # audio artifacts live under audio/
    "narration_transcript": "audio/narration.transcript.json",
    "audio_manifest": "audio/audio_manifest.json",
}

MEDIA_PATHS: dict[str, str] = {
    "video": "video.mp4",
    "master": "audio/master.wav",
    "narration": "audio/narration.wav",
}


def make_measurement_error(artifact: str, stage: str, owner: str, prop: str,
                           kind: str, rolls_up_to: tuple[str, ...], err: str,
                           unit: str = "") -> Measurement:
    """Convenience for the common 'could not measure' Measurement."""
    return Measurement(artifact=artifact, stage=stage, owner=owner, prop=prop,
                       value=None, kind=kind, rolls_up_to=rolls_up_to, unit=unit,
                       error=err)


class EvalContext:
    """Read-only access to a single completed project's artifacts and media.

    Everything is lazy and tolerant: a missing JSON returns None, a missing
    media file returns a path that simply doesn't exist. Nothing here mutates
    the project. Analyzers receive ONE of these.
    """

    def __init__(self, project_dir: str | Path, run_id: str | None = None):
        self.dir = Path(project_dir)
        # run_id identifies this evaluation pass in the tracking store; default
        # to the project slug (the dir name) so a re-eval of the same project is
        # traceable.
        self.run_id = run_id or self.dir.name
        self._json_cache: dict[str, Any] = {}

    # -- JSON artifacts -----------------------------------------------------
    def load(self, name: str) -> Optional[dict]:
        """Load artifact `name` (see ARTIFACT_PATHS). Returns None if absent or
        unparseable — never raises."""
        if name in self._json_cache:
            return self._json_cache[name]
        rel = ARTIFACT_PATHS.get(name)
        if rel is None:
            self._json_cache[name] = None
            return None
        p = self.dir / rel
        data: Any = None
        try:
            if p.is_file():
                data = json.loads(p.read_text())
        except Exception:
            data = None
        self._json_cache[name] = data
        return data

    @cached_property
    def project(self) -> Optional[dict]: return self.load("project")
    @cached_property
    def research_brief(self) -> Optional[dict]: return self.load("research_brief")
    @cached_property
    def script(self) -> Optional[dict]: return self.load("script")
    @cached_property
    def factcheck(self) -> Optional[dict]: return self.load("factcheck_report")
    @cached_property
    def style_guide(self) -> Optional[dict]: return self.load("style_guide")
    @cached_property
    def storyboard(self) -> Optional[dict]: return self.load("storyboard")
    @cached_property
    def asset_manifest(self) -> Optional[dict]: return self.load("asset_manifest")
    @cached_property
    def transcript(self) -> Optional[dict]: return self.load("narration_transcript")
    @cached_property
    def audio_manifest(self) -> Optional[dict]: return self.load("audio_manifest")
    @cached_property
    def composition_manifest(self) -> Optional[dict]: return self.load("composition_manifest")

    # -- media paths --------------------------------------------------------
    def media(self, name: str) -> Path:
        return self.dir / MEDIA_PATHS.get(name, name)

    @property
    def video(self) -> Path: return self.media("video")
    @property
    def master(self) -> Path: return self.media("master")
    @property
    def narration(self) -> Path: return self.media("narration")

    def has_media(self, name: str) -> bool:
        return self.media(name).is_file()


# ---------------------------------------------------------------------------
# Deterministic media probing helpers (ffprobe / ffmpeg). All degrade to {}.
# ---------------------------------------------------------------------------

def ffprobe_json(path: str | Path, *, streams: bool = True,
                 fmt: bool = True, timeout: int = 60) -> dict:
    """Return ffprobe -show_format/-show_streams JSON for `path`, or {} on any
    failure (missing file, missing ffprobe, bad media). Never raises."""
    path = Path(path)
    if not path.is_file():
        return {}
    args = ["ffprobe", "-v", "quiet", "-print_format", "json"]
    if streams:
        args.append("-show_streams")
    if fmt:
        args.append("-show_format")
    args.append(str(path))
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0 or not out.stdout.strip():
            return {}
        return json.loads(out.stdout)
    except Exception:
        return {}


def media_duration_sec(path: str | Path) -> Optional[float]:
    """Container duration in seconds, or None."""
    info = ffprobe_json(path)
    try:
        return float(info.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        # fall back to a stream duration
        for s in info.get("streams", []):
            try:
                return float(s["duration"])
            except (KeyError, TypeError, ValueError):
                continue
    return None


def run_ffmpeg(args: list[str], *, timeout: int = 300) -> tuple[int, str, str]:
    """Run `ffmpeg <args>` capturing stderr (where ffmpeg writes filter reports
    such as ebur128/volumedetect). Returns (rc, stdout, stderr); (-1,"",msg) on
    failure. Never raises."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", *args],
                             capture_output=True, text=True, timeout=timeout)
        return out.returncode, out.stdout, out.stderr
    except Exception as e:  # pragma: no cover - environment dependent
        return -1, "", f"{type(e).__name__}: {e}"
