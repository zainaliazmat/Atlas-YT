"""Durable, provider-agnostic state for Atlas — the source of truth on disk.

Same contract as the sibling agents (intentionally): atomic writes (temp file +
os.replace) so an interrupted save can't corrupt a file, and tolerant loads (a
corrupt file is backed up, not fatal). Atlas's meeting-room memory is a single
distilled SUMMARY across sessions — the raw transcript lives only in RAM and is
distilled on session boundaries (Phase 2). Shape on disk (chat_state.json):

    {
        "summary": "<distilled durable context about the CEO + the fleet>",
        "updated": <unix timestamp>,
        "pending": [ {"role": ..., "content": ...}, ... ]   # OPTIONAL recovery
    }

This file is OUR record — never a Claude session id — so the brain can be swapped
to Gemini/DeepSeek and the same saved state still works.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
import time
from typing import Any


def new_state() -> dict[str, Any]:
    """A fresh, empty in-RAM chat session state (current session only)."""
    return {"transcript": [], "summary": "", "pending": None}


def atomic_write_json(path: str | pathlib.Path, obj: Any) -> None:
    """Write JSON to `path` atomically: dump to a UNIQUE temp file, then os.replace.

    The temp file is minted with `tempfile.mkstemp` (unique per call, even across threads of
    the same process) so two threads writing the SAME target — e.g. the supervisor logging a
    decision from the worker thread while an operator action rewrites the same project.json —
    cannot collide on the temp path. `os.replace` is atomic, so the only observable effect of
    a concurrent write is last-writer-wins on the target (never a crash or a corrupt file)."""
    path = pathlib.Path(path)
    data = json.dumps(obj, indent=2, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".tmp.")
    tmp = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.replace(tmp, path)  # atomic on POSIX/Windows for same-dir paths
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def load_json(path: str | pathlib.Path, default: Any) -> Any:
    """Load JSON, tolerating absence and corruption.

    Missing file -> `default`. Corrupt file -> moved aside to `<name>.corrupt-<ts>`
    (nothing silently lost) and `default` returned, rather than crashing.
    """
    path = pathlib.Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError, OSError):
        try:
            backup = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
            path.rename(backup)
            print(f"⚠️  {path.name} was unreadable; backed it up to {backup.name} "
                  "and started fresh.")
        except OSError:
            pass
        return default


def load_state(path: str | pathlib.Path) -> dict[str, Any]:
    """Load chat memory for a NEW session: saved summary + a FRESH empty transcript.

    The raw transcript is intentionally NOT loaded — long-term memory is the
    distilled summary only. A `pending` list (failed-distill recovery) is surfaced
    for the launch to fold in; everything else falls back to safe defaults.
    """
    data = load_json(path, {})
    if not isinstance(data, dict):
        data = {}
    summary = data.get("summary")
    if not isinstance(summary, str):
        summary = ""
    pending = data.get("pending")
    if not isinstance(pending, list):
        pending = None
    return {"transcript": [], "summary": summary, "pending": pending}


def save_summary(path: str | pathlib.Path, summary: str,
                 pending: list[dict[str, str]] | None = None) -> None:
    """Persist ONLY the durable summary (atomically), stamping the update time.

    `pending` is written only when given — it parks a raw transcript a failed/timed-
    out distill couldn't fold in. On a clean save we omit the key so a stale
    `pending` can't linger.
    """
    obj: dict[str, Any] = {"summary": summary or "", "updated": time.time()}
    if pending:
        obj["pending"] = pending
    atomic_write_json(path, obj)


def append_turn(state: dict[str, Any], role: str, content: str) -> None:
    """Append one turn. role is 'user' or 'atlas' (kept provider-neutral)."""
    state["transcript"].append({"role": role, "content": content})
