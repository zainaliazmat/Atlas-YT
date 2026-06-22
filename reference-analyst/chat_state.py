"""Durable, provider-agnostic state for Vera — the source of truth on disk.

Two things live through this module:

1. Vera's chat memory ("Talk to Vera"). Across sessions, Vera's ONLY long-term
   memory is a single distilled SUMMARY. The raw transcript is NOT persisted
   between sessions — it lives only in RAM for the duration of a session and is
   distilled into the summary on session end (see chat.distill_and_save). Shape on
   disk (chat_state.json):

       {
           "summary":  "<distilled durable context about the user>",
           "updated":  <unix timestamp>,
           "pending":  [ {"role": ..., "content": ...}, ... ]   # OPTIONAL
       }

   `pending` only appears for crash/failure recovery: if distillation fails or
   times out on exit, the raw transcript is parked here so nothing is lost, and the
   next launch folds it into the summary and clears it.

2. The durable, MERGING rubric memory (rubric_store.py) — an evolving rubric per
   named "standard" — through the same atomic_write_json / load_json helpers.

This file is OUR record — NEVER a Claude session id — so the brain can later be
swapped to Gemini/DeepSeek and the same saved state still works.

Writes are atomic (temp file + os.replace) so an interrupted write can't corrupt a
file. Loads are tolerant: a corrupt file is backed up and we start clean instead of
crashing.
"""
from __future__ import annotations

import json
import os
import pathlib
import time
from typing import Any


def new_state() -> dict[str, Any]:
    """A fresh, empty in-RAM chat session state.

    `transcript` is the in-memory working memory for the CURRENT session only; it
    is never persisted directly. `pending` is normally None and only set when a
    prior session's distill failed and left raw turns to recover.
    """
    return {"transcript": [], "summary": "", "pending": None}


def atomic_write_json(path: str | pathlib.Path, obj: Any) -> None:
    """Write JSON to `path` atomically: dump to a temp file, then os.replace.

    os.replace is atomic on the same filesystem, so readers see either the old file
    or the fully-written new one — never a half-written mix.
    """
    path = pathlib.Path(path)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False))
    os.replace(tmp, path)  # atomic on POSIX/Windows for same-dir paths


def load_json(path: str | pathlib.Path, default: Any) -> Any:
    """Load JSON, tolerating absence and corruption.

    Missing file -> return `default`. Corrupt file -> move it aside to
    `<name>.corrupt-<ts>` (so nothing is silently lost) and return `default`,
    rather than raising and killing the program.
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
    distilled summary only. Any `transcript` key in an older on-disk file is
    ignored, so upgrading never replays stale raw turns. A `pending` list
    (failed-distill recovery) is surfaced for the launch to fold in; everything
    else falls back to safe defaults.
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

    `pending` is written only when given — it parks a raw transcript that a
    failed/timed-out distill couldn't fold in, so the next launch can recover it.
    On a clean save we omit the key entirely so a stale `pending` can't linger.
    """
    obj: dict[str, Any] = {"summary": summary or "", "updated": time.time()}
    if pending:
        obj["pending"] = pending
    atomic_write_json(path, obj)


def append_turn(state: dict[str, Any], role: str, content: str) -> None:
    """Append one turn. role is 'user' or 'vera' (kept provider-neutral)."""
    state["transcript"].append({"role": role, "content": content})
