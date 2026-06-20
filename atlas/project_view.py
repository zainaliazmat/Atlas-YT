"""Read-only views over a project directory — the data the gate UI renders inline.

Pure, tolerant, side-effect-free. The web UI uses these to (a) detect which project
is paused at a human gate and (b) build the inline preview the operator approves
against: the fact-check report + script at Gate 1, the render plan + playable draft
renders (+ palette) at Gate 2.

This module READS artifacts the pipeline already wrote. It does not run, advance, or
mutate anything — the pipeline and its gate logic are untouched.
"""
from __future__ import annotations

import pathlib

import chat_state  # tolerant load_json (missing/corrupt -> default, never raises)

_BLOCKED = "blocked_at_"


def find_latest_blocked(projects_dir) -> dict | None:
    """The most-recently-updated project whose status is `blocked_at_<gate>`, or None.

    Returns {slug, gate, status, details, project_dir, label}. Scans tolerantly: a
    missing dir or an unreadable project.json is skipped, never fatal.
    """
    root = pathlib.Path(projects_dir)
    if not root.exists():
        return None
    best = None
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        proj = chat_state.load_json(d / "project.json", None)
        if not isinstance(proj, dict):
            continue
        status = proj.get("status") or ""
        if not status.startswith(_BLOCKED):
            continue
        updated = proj.get("updated", 0) or 0
        if best is None or updated > best[0]:
            best = (updated, d, proj, status)
    if best is None:
        return None
    _, d, proj, status = best
    gate = status[len(_BLOCKED):]
    details = (proj.get("gates", {}).get(gate, {}) or {}).get("details")
    label = proj.get("title") or proj.get("topic") or proj.get("slug") or d.name
    return {"slug": proj.get("slug") or d.name, "gate": gate, "status": status,
            "details": details, "project_dir": str(d), "label": label,
            "updated": best[0]}


def _flagged_claims(report) -> list[dict]:
    claims = (report or {}).get("claims", [])
    return [c for c in claims if c.get("status") in ("flagged", "unverifiable")]


def gate1_preview(project_dir) -> dict:
    """Fact-check gate: verdict + summary + the flagged/unverifiable claims, plus the
    script being judged. Reads factcheck_report.json + script.json directly."""
    pdir = pathlib.Path(project_dir)
    report = chat_state.load_json(pdir / "factcheck_report.json", {})
    script = chat_state.load_json(pdir / "script.json", {})
    return {
        "gate": "factcheck",
        "verdict": report.get("verdict"),
        "summary": report.get("summary", {}),
        "flagged": _flagged_claims(report),
        "script": {
            "working_title": script.get("working_title", ""),
            "total_scenes": script.get("total_scenes", 0),
            "est_runtime_sec": script.get("est_runtime_sec", 0),
            "scenes": script.get("scenes", []),
        },
    }


def draft_renders(project_dir) -> list[pathlib.Path]:
    """Every existing per-scene draft render, sorted by scene (scene-01, scene-02, …)."""
    pdir = pathlib.Path(project_dir)
    scenes = pdir / "scenes"
    if not scenes.exists():
        return []
    return sorted(scenes.glob("scene-*/renders/draft.mp4"), key=lambda p: str(p))


def load_palette(project_dir) -> dict:
    """The style guide's palette (incl. the signature #FFD000), or {} if absent."""
    pdir = pathlib.Path(project_dir)
    style = chat_state.load_json(pdir / "style_guide.json", {})
    return style.get("palette", {}) if isinstance(style, dict) else {}


def gate2_preview(project_dir) -> dict:
    """Final-render gate: the render plan (mirrors pipeline._render_plan, rebuilt from
    disk) + the playable draft renders + the palette. Reads script.json,
    audio/audio_manifest.json, style_guide.json — never advances anything."""
    pdir = pathlib.Path(project_dir)
    script = chat_state.load_json(pdir / "script.json", {})
    mix = chat_state.load_json(pdir / "audio" / "audio_manifest.json", {})
    plan = {
        "working_title": script.get("working_title", ""),
        "scenes": script.get("total_scenes", 0),
        "est_runtime_sec": script.get("est_runtime_sec", 0),
        "audio_duration_sec": mix.get("total_duration_sec", 0),
        "plan": "Render each scene HTML, concat with FFmpeg, mux narration + bed.",
    }
    return {
        "gate": "final_render",
        "plan": plan,
        "draft_renders": draft_renders(pdir),
        "palette": load_palette(pdir),
    }
