"""The lightweight per-project manifest — Atlas's checklist, NOT a state machine.

This replaces the deleted pipeline's stage tracking. There is no fixed stage order, no
gate state, no `blocked_at_*`: just one folder per video under `projects/<slug>/` holding
the produced artifacts, plus a `project.json` that records the brief and a flat checklist
of which artifacts exist (done/pending + their path). Atlas reads the checklist to know
where a video is and to resume without re-doing or skipping a step; it writes to it as each
delegated job produces an artifact.

The agent job tools all operate on an explicit `slug`: each job reads its upstream
artifact(s) from `projects/<slug>/` and writes its output there, so a sequence of
delegations accumulates ONE video.

Determinism for the RENDERED output still lives in the specialist engines — this module
only tracks what's been produced.
"""
from __future__ import annotations

import pathlib
import time
import uuid

import chat_state

HERE = pathlib.Path(__file__).parent
PROJECTS_DIR = HERE / "projects"

# The canonical artifact checklist for a full video, in playbook order. A job may record
# extra artifacts (e.g. the optional creative-architecture passes) — the checklist is
# additive, so unknown names are tolerated.
ARTIFACTS = [
    "research_brief",
    "script",
    "factcheck_report",
    "style_guide",
    "storyboard",
    "asset_manifest",
    "narration",
    "composition",
    "render",
]


def slugify(text: str) -> str:
    """A filesystem-safe slug stem from free text (mirrors the old pipeline._slug)."""
    keep = [c.lower() if c.isalnum() else "-" for c in (text or "video").strip()]
    s = "".join(keep).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    return (s or "video")[:50]


def project_dir(slug: str) -> pathlib.Path | None:
    """The directory for `slug`, or None if it doesn't exist / slug is empty."""
    slug = (slug or "").strip()
    if not slug:
        return None
    pdir = PROJECTS_DIR / slug
    return pdir if pdir.exists() else None


def manifest_path(slug: str) -> pathlib.Path:
    return PROJECTS_DIR / slug / "project.json"


def load_manifest(slug: str) -> dict | None:
    """The project.json manifest for `slug`, or None if it isn't a real project."""
    proj = chat_state.load_json(manifest_path(slug), None)
    return proj if isinstance(proj, dict) else None


def _new_manifest(brief: str, topic: str, slug: str) -> dict:
    now = time.time()
    return {
        "schema_version": "1.0",
        "project_id": uuid.uuid4().hex[:12],
        "slug": slug,
        "created": now,
        "updated": now,
        "brief": brief,
        "topic": topic,
        "artifacts": {name: {"status": "pending", "path": None} for name in ARTIFACTS},
    }


def _save(proj: dict, slug: str) -> None:
    proj["updated"] = time.time()
    chat_state.atomic_write_json(manifest_path(slug), proj)


def start_project(brief: str, *, slug: str | None = None,
                  topic: str | None = None) -> dict:
    """Mint a new project workspace and write its checklist manifest. Returns
    {slug, project_dir}. `slug` is optional — by default one is generated from the brief
    with a timestamp + uuid suffix so two starts in the same second never collide."""
    b = (brief or topic or "").strip()
    the_topic = (topic or b).strip()
    if slug:
        slug = slugify(slug)
    else:
        slug = f"{slugify(the_topic)}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    pdir = PROJECTS_DIR / slug
    pdir.mkdir(parents=True, exist_ok=True)
    proj = _new_manifest(b, the_topic, slug)
    _save(proj, slug)
    return {"slug": slug, "project_dir": str(pdir)}


def mark_artifact(slug: str, name: str, path: str | pathlib.Path | None = None,
                  **extra) -> None:
    """Flip one checklist entry to done (tolerant of unknown names — additive). Extra
    keyword fields (e.g. a factcheck `verdict`) are merged into the entry. No-op if the
    project doesn't exist."""
    proj = load_manifest(slug)
    if proj is None:
        return
    arts = proj.setdefault("artifacts", {})
    entry = {"status": "done", "path": str(path) if path is not None else None}
    entry.update(extra)
    arts[name] = entry
    _save(proj, slug)


def status_text(slug: str) -> str:
    """A human/LLM-readable checklist of what's produced for `slug` (resumability)."""
    proj = load_manifest(slug)
    if proj is None:
        return f"No project named {slug!r}. Start one with start_project."
    arts = proj.get("artifacts", {})
    lines = [f"Project '{slug}' — {proj.get('topic') or proj.get('brief') or ''}".rstrip(),
             "Checklist:"]
    for name in ARTIFACTS:
        e = arts.get(name, {})
        mark = "✓" if e.get("status") == "done" else "·"
        extra = ""
        if name == "factcheck_report" and e.get("verdict"):
            extra = f" (verdict: {e['verdict']})"
        lines.append(f"  {mark} {name}{extra}")
    # surface any extra (non-canonical) artifacts produced, e.g. creative passes
    for name, e in arts.items():
        if name not in ARTIFACTS and e.get("status") == "done":
            lines.append(f"  ✓ {name}")
    return "\n".join(lines)


def list_projects() -> list[dict]:
    """All projects (newest first) as {slug, topic, updated} — for resume discovery."""
    if not PROJECTS_DIR.exists():
        return []
    out = []
    for d in PROJECTS_DIR.iterdir():
        proj = chat_state.load_json(d / "project.json", None)
        if isinstance(proj, dict):
            out.append({"slug": proj.get("slug") or d.name,
                        "topic": proj.get("topic") or proj.get("brief") or "",
                        "updated": proj.get("updated", 0) or 0})
    out.sort(key=lambda p: p["updated"], reverse=True)
    return out
