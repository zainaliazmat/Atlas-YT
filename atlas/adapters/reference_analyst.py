"""Adapter for Vera (reference-analyst) — the Reference Analyst & standards-keeper.

ONE real job, and it does NOT read a pipeline project — it reads reference VIDEO files
directly (paths the CEO hands her):
- build_rubric(videos, ceo_prefs) -> reference_rubric.json (banded quality targets +
  a judged style profile, merged into a durable named "standard")

Vera is NOT a pipeline stage. She DEFINES the standard later stages can be tuned toward;
she never generates or improves a video. So there is no project-dir resolution here (no
_resolve_project_dir like the producers) — she consumes local video paths and writes her
own durable, MERGING rubric under the sibling project's standards/.

DECOUPLING: Vera's engine emits the rubric as a plain dict in the frozen shape and NEVER
imports atlas. ATLAS owns the contract — it stamps `schema_version` (from the engine's
own RUBRIC_VERSION) and validates against `reference_rubric.schema.json` HERE, at the
boundary, before returning the digest.

INPUT HANDLING: the SDK tool layer coerces every param to a string, so `videos` arrives
as a string — we accept a JSON array, a comma/space-separated list, or a single path —
and `ceo_prefs` as a JSON object string. URIs are always local (repo convention): we
validate existence and degrade gracefully with a clear note if a file is missing, never
a remote fetch.

VISION: the judged style-profile seam is bound to Vera's own `llm` (best-effort); any
failure degrades the judged layer to a note — it never blocks the objective rubric.

PERSONA `ask` is inherited from base.
"""
from __future__ import annotations

import json
import logging
import os

from adapters.base import Adapter
from adapters.loader import load_engine

log = logging.getLogger(__name__)

# Default named standard a conversational build merges into (the job params are
# {videos, ceo_prefs} — no `standard` param — so repeated calls accrete one standard).
DEFAULT_STANDARD = "default"

# Opt OUT of the LLM/vision style-profile pass at the adapter seam (objective-only).
# The pass is best-effort and the engine contains any failure, but a meeting may want
# the fast offline path. Default: vision ON.
VISION_OFF_ENV = "VERA_NO_VISION"


def _truthy(value: str) -> bool:
    return (value or "").strip().lower() in ("1", "yes", "true", "on")


# ----------------------------------------------------------------------
# The seams (one place each; tests monkeypatch these)
# ----------------------------------------------------------------------
def _reference_store():
    """Load Vera's `rubric_store` module (isolated, cached by the loader).

    The store wraps the pure engine + the durable, merging persistence, so the adapter
    gets band-tightening merge and atomic writes for free.
    """
    import registry  # lazy: registry imports this module, so avoid a top-level cycle
    ra_dir = registry.get_entry("reference_analyst").project_dir
    return load_engine(ra_dir, "rubric_store")


def _reference_engine():
    """Load Vera's `reference_engine` module (for RUBRIC_VERSION at the boundary)."""
    import registry
    ra_dir = registry.get_entry("reference_analyst").project_dir
    return load_engine(ra_dir, "reference_engine")


def _vision_fn():
    """Best-effort style-profiler from Vera's own llm seam; None if unavailable/off."""
    if _truthy(os.environ.get(VISION_OFF_ENV, "")):
        return None
    try:
        import registry
        ra_dir = registry.get_entry("reference_analyst").project_dir
        vera_llm = load_engine(ra_dir, "llm")
        return vera_llm.make_style_profiler()
    except Exception as exc:  # never let a missing brain block the objective rubric
        log.warning("Vera's vision seam unavailable (%s) — running objective-only.", exc)
        return None


# ----------------------------------------------------------------------
# Lenient input parsing (the SDK coerces every tool param to a string)
# ----------------------------------------------------------------------
def _parse_videos(raw) -> list[str]:
    """Accept a list, a JSON array string, or a comma/space-separated string of paths."""
    if isinstance(raw, (list, tuple)):
        return [str(p).strip() for p in raw if str(p).strip()]
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            arr = json.loads(raw)
            if isinstance(arr, list):
                return [str(p).strip() for p in arr if str(p).strip()]
        except (ValueError, TypeError):
            pass
    return [p.strip() for p in raw.replace(",", " ").split() if p.strip()]


def _parse_prefs(raw) -> dict:
    """Accept a dict or a JSON object string; anything else -> empty (no prefs)."""
    if isinstance(raw, dict):
        return raw
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except (ValueError, TypeError):
        return {}


# ----------------------------------------------------------------------
# Build + stamp + persist (the real work; tests monkeypatch _reference_store)
# ----------------------------------------------------------------------
def run_build_rubric(videos: list[str], ceo_prefs: dict | None = None,
                     standard: str = DEFAULT_STANDARD) -> dict:
    """Drive Vera's merging store, stamp `schema_version` at the boundary, return rubric.

    The caller validates it against the frozen contract. The store persists the merged
    rubric under the sibling project's standards/ atomically.
    """
    rubric = _reference_store().build_standard(
        standard, videos, vision_fn=_vision_fn(), ceo_prefs=ceo_prefs or None)
    # ATLAS owns the envelope — stamp schema_version from the engine's declared version.
    rubric = {**rubric, "schema_version": _reference_engine().RUBRIC_VERSION}
    return rubric


# ----------------------------------------------------------------------
# Digest (compact: a targets summary + the open_questions)
# ----------------------------------------------------------------------
def _band_str(node: dict) -> str | None:
    node = node or {}
    v, b = node.get("value"), node.get("band")
    if v is None:
        return None
    return f"{v} (band {b})" if b else f"{v}"


def _rubric_digest(rubric: dict, standard: str) -> str:
    t = rubric.get("targets", {})
    g = lambda grp, key: _band_str((t.get(grp, {}) or {}).get(key, {}))
    srcs = rubric.get("source_videos", []) or []
    lines = [f"Rubric for standard {standard!r} — built from {len(srcs)} reference(s): "
             f"{', '.join(srcs) or '—'}."]

    rows = [("avg shot (s)", g("pacing", "avg_shot_sec")),
            ("cuts/min", g("pacing", "cuts_per_min")),
            ("kinetic", g("motion", "kinetic_score")),
            ("saturation", g("color", "saturation")),
            ("brightness", g("color", "brightness")),
            ("integrated LUFS", g("audio", "integrated_lufs")),
            ("speech ratio", g("audio", "speech_ratio")),
            ("duration (s)", g("structure", "duration_sec"))]
    for label, val in rows:
        if val is not None:
            lines.append(f"  {label}: {val}")

    judged = rubric.get("judged", {})
    jline = f"  judged: {judged.get('status', '?')} ({len(judged.get('frames', []))} frames)"
    if judged.get("error"):
        jline += f" — style profile degraded: {judged['error']}"
    lines.append(jline)
    if rubric.get("notes"):
        lines.append(f"  note: {rubric['notes']}")

    oq = rubric.get("open_questions", []) or []
    if oq:
        lines.append("\nQuestions only the CEO's taste can answer:")
        for q in oq:
            lines.append(f"  - [{q.get('id')}] {q.get('plain')}")
    lines.append("\n(Feeding more references tightens the bands toward their shared DNA.)")
    return "\n".join(lines)


class ReferenceAnalystAdapter(Adapter):
    module_name = "reference_engine"   # reference-analyst/reference_engine.py

    def run_job(self, job_name: str, progress, **params) -> dict:
        if job_name != "build_rubric":
            return {"ok": False, "text": f"Vera has no job named {job_name!r}."}

        from contracts import validate
        who = self.entry.display

        videos = _parse_videos(params.get("videos"))
        ceo_prefs = _parse_prefs(params.get("ceo_prefs"))
        if not videos:
            msg = ("No reference video paths were given. Pass 'videos' as a path or a "
                   "list of local paths.")
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg}

        # URIs are always local — validate existence and degrade with a clear note.
        existing, missing = self._reference_store().validate_videos(videos)
        if not existing:
            msg = ("None of those reference files exist locally: "
                   + ", ".join(videos) + ". (URIs are always local — no remote fetch.)")
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg}

        if progress is not None:
            progress.start(self.entry.emoji, who, "measuring the reference(s)",
                           ", ".join(os.path.basename(p) for p in existing))
        try:
            rubric = run_build_rubric(existing, ceo_prefs)
        except Exception as exc:  # an unusable/unsourceable reference, said plainly
            if progress is not None:
                progress.fail(who, str(exc))
            return {"ok": False, "text": str(exc)}

        ok, errors = validate("reference_rubric", rubric)
        if not ok:
            msg = f"reference_rubric failed contract validation: {'; '.join(errors)}"
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg}

        if progress is not None:
            progress.done(who, "finished the rubric")
        return {"ok": True, "text": _rubric_digest(rubric, DEFAULT_STANDARD),
                "standard": DEFAULT_STANDARD, "missing": missing}

    # small indirection so the run_job body can be tested with the store monkeypatched
    @staticmethod
    def _reference_store():
        return _reference_store()
