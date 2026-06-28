"""studio.review.state — the project's ``state.json`` audit trail.

This is where the old eval/coach idea finds its REAL home (project memory: "coaching THIS
video from frames, then later coaching the PACK"). Every review run appends an entry to
the project's ``state.json`` recording WHAT the critics found, WHAT was auto-applied, and
the before/after — so the history of how a video reached the bar is inspectable, and so a
later pass can mine recurring fixes to coach the Design Pack itself.

``state.json`` lives at ``studio/projects/<slug>/state.json`` and is intentionally simple:

  {
    "slug": "...",
    "reviews": [
      {"ts": <unix>, "video": "...", "counts": {...}, "polish_rate": <float|null>,
       "fixes": [ranked fix list], "conflicts": [...],
       "applied": [...], "escalated": [...], "before_after": {...}, "mode": "auto|stop"}
    ]
  }

Pure-ish: only touches the one JSON file, never raises on a corrupt/missing file (starts
fresh), and is fully round-trippable in tests. Timestamps are injected by the caller
(``ts=...``) so the writer stays deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import config

STATE_FILENAME = "state.json"


def state_path(slug: str) -> Path:
    return config.PROJECTS_DIR / slug / STATE_FILENAME


def load_state(slug: str) -> dict:
    """Read the project's state.json, returning a fresh skeleton on missing/corrupt."""
    path = state_path(slug)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("slug", slug)
                data.setdefault("reviews", [])
                return data
        except Exception:
            pass
    return {"slug": slug, "reviews": []}


def save_state(slug: str, state: dict) -> Path:
    """Atomically write the project's state.json (write-temp-then-rename)."""
    path = state_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def record_review(slug: str, *, ts: float, evidence: dict, synthesis: dict,
                  apply_result: dict | None, mode: str) -> dict:
    """Append one review entry to state.json and persist it. Returns the entry written.

    Stores the full ranked fix list + conflicts (the critique) and the auto-apply
    outcome (applied / escalated / before_after) — the audit trail. ``ts`` is supplied by
    the caller so this stays deterministic/testable."""
    apply_result = apply_result or {}
    polish = (evidence.get("polish_vs_reference") or {}).get("rate")
    entry = {
        "ts": ts,
        "video": evidence.get("video"),
        "reference": evidence.get("reference"),
        "render_duration_sec": evidence.get("render_duration_sec"),
        "polish_rate": polish,
        "loudness": evidence.get("loudness"),
        "counts": synthesis.get("counts", {}),
        "fixes": synthesis.get("fixes", []),
        "conflicts": synthesis.get("conflicts", []),
        "mode": mode,
        "applied": apply_result.get("applied", []),
        "escalated": apply_result.get("escalated", []),
        "before_after": apply_result.get("before_after", {}),
        "rerendered": apply_result.get("rerendered"),
    }
    state = load_state(slug)
    state["reviews"].append(entry)
    save_state(slug, state)
    return entry
