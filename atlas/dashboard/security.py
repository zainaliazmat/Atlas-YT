"""Guards for the read-mostly dashboard: path-traversal containment + leak redaction.

Two jobs, both defensive:

1. resolve_project_dir / resolve_within — validate every slug/file param against the
   projects dir so a request can NEVER escape it (`../`, absolute paths, symlinks out).
2. redact — strip secrets and absolute/home paths out of anything before it leaves the
   process, so no .env value, API key, token, or filesystem layout can reach a response
   or the DOM. Applied as a final pass over every JSON payload.

Nothing here mutates state.
"""
from __future__ import annotations

import os
import pathlib
import re
from typing import Any

# A project slug is what pipeline._slug produces plus the "-YYYYMMDD-HHMMSS-xxxx"
# suffix: lowercase alnum + single hyphens. We also tolerate the few legacy dirs,
# so the rule is "safe filename, no separators, no traversal" rather than a strict
# slug regex — the real guarantee is the resolve() containment check below.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")

# Artifact files the dashboard is allowed to serve/parse, mapped to their contract
# name (or None for "valid JSON, no contract"). Anything not listed is refused — no
# arbitrary file read, even inside the project dir.
ARTIFACT_FILES: dict[str, str | None] = {
    "project.json": "project",
    "research_brief.json": "research_brief",
    "script.json": "script",
    "factcheck_report.json": "factcheck_report",
    "style_guide.json": "style_guide",
    "storyboard.json": "storyboard",
    "asset_manifest.json": "asset_manifest",
    "composition_manifest.json": "composition_manifest",
    "audio/audio_manifest.json": "audio_manifest",
    "audio/narration.transcript.json": "narration_transcript",
    "eval_scorecard.json": None,
}


class UnsafePathError(ValueError):
    """A slug or file param failed containment — treat as 404/400, never serve it."""


def safe_segment(name: str) -> bool:
    """True if `name` is a single safe path segment (no separators, no traversal)."""
    return bool(name) and name not in (".", "..") and bool(_SAFE_SEGMENT.match(name))


def resolve_project_dir(projects_dir: pathlib.Path, slug: str) -> pathlib.Path:
    """Resolve `slug` to a real, existing project directory STRICTLY inside
    projects_dir. Raises UnsafePathError on any traversal / escape / miss."""
    if not safe_segment(slug):
        raise UnsafePathError(f"unsafe slug: {slug!r}")
    root = projects_dir.resolve()
    target = (root / slug).resolve()
    # Containment: target must be root itself's child (parent == root) and exist.
    if target.parent != root or not target.is_dir():
        raise UnsafePathError(f"slug does not resolve inside projects dir: {slug!r}")
    return target


def resolve_within(base: pathlib.Path, rel: str) -> pathlib.Path:
    """Resolve a relative path under an already-validated base dir, refusing escape.

    `rel` may contain a single forward slash (e.g. "audio/audio_manifest.json"); each
    segment is checked, and the resolved path must stay under base.
    """
    rel = (rel or "").replace("\\", "/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(not safe_segment(p) for p in parts):
        raise UnsafePathError(f"unsafe path: {rel!r}")
    base_r = base.resolve()
    target = base_r.joinpath(*parts).resolve()
    if base_r != target and base_r not in target.parents:
        raise UnsafePathError(f"path escapes project dir: {rel!r}")
    return target


# ----------------------------------------------------------------------
# Leak redaction — never let a secret or an absolute/home path leave the process.
# ----------------------------------------------------------------------
_SECRET_KEY_HINT = re.compile(r"(api[_-]?key|secret|token|password|passwd|bearer|"
                              r"authorization|_llm$|credential)", re.IGNORECASE)
_HOME = str(pathlib.Path.home())
# Any absolute path under a sensitive root — collapsed so the DOM never reveals the
# filesystem layout (home, tmp, var, opt, etc., not just $HOME).
_ABS_PATH = re.compile(r"/(?:home|Users|root|tmp|var|opt|private|mnt|srv|etc)/"
                       r"[^\s\"'<>]+")
# Value-level secret token shapes — scrubbed even when they sit in a free-text field
# (a `note`, `claim_text`, an exception message) under a non-secret-looking key.
_SECRET_VALUE = re.compile(
    r"(sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9]{16,}|AIza[A-Za-z0-9_-]{20,}|"
    r"ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"AKIA[A-Z0-9]{12,}|ey[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})")


def _redact_str(s: str) -> str:
    # 1) collapse any absolute home/projects path to a project-relative / ~ tail so
    #    the DOM never reveals the filesystem layout.
    if "projects/" in s:
        s = re.sub(r"(?:~|/[^\s\"'<>]*?)?(projects/)", r"\1", s)
    if _HOME in s:
        s = s.replace(_HOME, "~")
    s = _ABS_PATH.sub("~", s)
    # 2) scrub any secret-shaped token value, wherever it appears.
    s = _SECRET_VALUE.sub("***", s)
    return s


def redact(obj: Any) -> Any:
    """Deep-copy `obj` with secrets dropped and absolute paths collapsed.

    - dict keys hinting at a secret are replaced with "***" (we never echo a value
      that might be a key/token, even if the source artifact carried one).
    - string values get absolute/home paths collapsed to project-relative / "~".
    - applied as the final pass over every endpoint payload.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SECRET_KEY_HINT.search(k):
                out[k] = "***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return _redact_str(obj)
    return obj


def env_provider(var: str, default: str = "claude") -> str:
    """Resolve a sibling agent's LLM switch from the environment (read-only).

    Mirrors each sibling llm.py: `os.environ.get("<NAME>_LLM", "claude")`. We read
    the SAME env var the engine would, so the surfaced provider is the real one —
    without importing (and thus booting) the heavy engine.
    """
    return (os.environ.get(var, default) or default).strip().lower()
