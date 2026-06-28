"""The seam between Atlas (the chat showrunner) and the studio v2 production spine.

This is the ONE place Atlas crosses into ``studio/``. Atlas no longer hand-orchestrates
the sibling agents to assemble a video; it drives studio's single resumable ``produce``
state machine — research → script → factcheck★ → vo → compose → draft → review →
final★ → video.mp4 — and surfaces its two gates through the CEO.

``studio/`` is a sibling package under the repo root, so we put the repo root on
sys.path (once, here) the same way tools.py bootstraps ``atlas/`` — ``import studio``
then resolves no matter how the process was launched, and a call-time import can never
fail with "No module named 'studio'" after a sys.path mutation (the lesson from the
project_status bug: resolve the module once, at import, and cache it).
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time
import uuid

_REPO_ROOT = str(pathlib.Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from studio import config as _sconfig      # noqa: E402 — needs the sys.path bootstrap
from studio import pipeline as _spipeline  # noqa: E402

GATE_FACTCHECK = "factcheck"
GATE_FINAL = "final"
GATES = (GATE_FACTCHECK, GATE_FINAL)

# Studio's resumable stage order (mirrors studio.pipeline.STAGES) — used to render a
# compact checklist. Read from the live module so it never drifts.
STAGES = tuple(getattr(_spipeline, "STAGES",
                       ("research", "script", "factcheck", "storyboard", "vo",
                        "compose", "draft", "review", "final")))

_MARKS = {"done": "✓", "pending": "·", "awaiting_approval": "⏸",
          "blocked": "✗", "error": "!", "rejected": "✗", "passed": "✓"}


# ----------------------------------------------------------------------
# Naming + config
# ----------------------------------------------------------------------
def slugify(text: str) -> str:
    """A unique, filesystem-safe slug for a NEW production: studio's stem shape plus a
    timestamp + uuid suffix so two starts in the same second never collide."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    stem = (s[:48].rstrip("-")) or "untitled"
    return f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


def run_config(channel: str | None = None, pack: str | None = None,
               voice: str | None = None) -> dict:
    return _sconfig.resolve_run_config(channel=channel, pack=pack, voice=voice)


# ----------------------------------------------------------------------
# Reading studio projects (Atlas's project list/status now read THIS — 2A)
# ----------------------------------------------------------------------
def project_dir(slug: str) -> pathlib.Path | None:
    """studio/projects/<slug> if it exists, else None."""
    slug = (slug or "").strip()
    if not slug:
        return None
    pdir = _sconfig.project_dir(slug)
    return pdir if pdir.exists() else None


def read_state(slug: str) -> dict | None:
    """The studio state.json for `slug`, or None if it isn't a real production."""
    pdir = _sconfig.project_dir((slug or "").strip())
    sp = pdir / "state.json"
    if not sp.exists():
        return None
    try:
        with sp.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def list_projects() -> list[dict]:
    """Every studio production (newest first) as {slug, topic, status, updated} — the
    unified project list Atlas surfaces."""
    pdir = _sconfig.PROJECTS_DIR
    if not pdir.exists():
        return []
    out = []
    for d in pdir.iterdir():
        if not d.is_dir():
            continue
        st = read_state(d.name)
        if not st:
            continue
        out.append({"slug": st.get("slug") or d.name,
                    "topic": (st.get("brief") or {}).get("topic", ""),
                    "status": st.get("status", "?"),
                    "updated": st.get("updated_at") or ""})
    out.sort(key=lambda p: p["updated"], reverse=True)
    return out


def status_digest(state: dict) -> str:
    """A human/LLM-readable status for one production: the stage checklist, gate state,
    and the final video path when ready."""
    slug = state.get("slug", "?")
    topic = (state.get("brief") or {}).get("topic", "")
    head = f"Production '{slug}' — {topic}".rstrip()
    lines = [head, f"Status: {state.get('status', '?')}"]
    stages = state.get("stages", {})
    marks = []
    for st in STAGES:
        s = stages.get(st, {}).get("status", "pending")
        marks.append(f"{_MARKS.get(s, '·')} {st}")
    lines.append("  " + "  ".join(marks))
    gates = state.get("gates", {})
    for g in GATES:
        gs = gates.get(g)
        if gs:
            blocked = gs.get("status") in ("rejected", "blocked")
            lines.append(f"gate[{g}]: {gs.get('status', '?')}"
                         + (" (un-approvable — must re-earn a pass)"
                            if blocked and gs.get("approvable") is False else ""))
    video = (state.get("artifacts") or {}).get("video")
    if video:
        lines.append(f"video: {video}")
    return "\n".join(lines)


def delete(slug: str) -> dict:
    """Permanently delete a studio production workspace, through the structural delete
    boundary (PROJECT tier — studio/projects/<slug> qualifies). Returns
    {slug, deleted, path}."""
    import boundary
    s = (slug or "").strip()
    if not s:
        raise ValueError("a slug is required to delete a production")
    pdir = project_dir(s)
    if pdir is None:
        return {"slug": s, "deleted": False, "path": None}
    removed = boundary.guarded_delete(pdir)
    return {"slug": s, "deleted": True, "path": str(removed)}


# ----------------------------------------------------------------------
# Driving the produce state machine
# ----------------------------------------------------------------------
def start(topic: str, *, angle: str | None = None, channel: str | None = None,
          pack: str | None = None, voice: str | None = None,
          unattended: bool = False, slug: str | None = None) -> tuple[str, dict]:
    """Start a NEW produce run. Runs until it pauses at a gate, blocks, or completes.
    Returns (slug, state). Gates are ENFORCED (gates=True); `unattended` only ever
    auto-approves the FINAL gate, and only when motion+vision pass under budget."""
    s = slug or slugify(topic)
    brief = {"topic": (topic or "").strip()}
    if angle and angle.strip():
        brief["angle"] = angle.strip()
    cfg = run_config(channel, pack, voice)
    state = _spipeline.produce(brief, s, gates=True, unattended=bool(unattended),
                               run_config=cfg)
    return s, state


def resume(slug: str, *, approve) -> dict:
    """Resume a paused run, approving the gate ids in `approve` (e.g. {'final'} or
    {'factcheck'}). Reads the stored brief + run_config from state.json (produce needs a
    topic-bearing brief even on resume). Raises ValueError if `slug` isn't a real run."""
    state = read_state(slug)
    if state is None:
        raise ValueError(f"no studio production {slug!r}")
    brief = state.get("brief") or {}
    if not brief.get("topic"):
        raise ValueError(f"studio production {slug!r} has no brief topic to resume")
    cfg = state.get("run_config") or run_config()
    return _spipeline.produce(brief, slug, approve=set(approve),
                              gates=bool(state.get("gates_enabled", True)),
                              unattended=bool(state.get("unattended", False)),
                              run_config=cfg)
