"""Deterministic progress lines — the reliable, NON-LLM half of transparency.

Status updates ("🔎 Scout is scanning…", "✅ Scout returned 8 topics") are emitted
from INSIDE the tools/adapters as work actually happens, so they're true regardless
of what the orchestrator LLM decides to say. They are intentionally separate from
Atlas's own streamed reasoning (the "🧠 I'm going with X because…" decisions), which
is the LLM's text. Together: status = deterministic from tools; decisions =
Atlas's words.

A `Progress` is just a sink for status strings. The default prints to stdout (with a
blank-safe newline). Tests inject a list-backed sink to assert lines were emitted in
order, with no terminal and no LLM.
"""
from __future__ import annotations

from typing import Callable


class Progress:
    """A status-line sink. `emit(msg)` is called from inside adapters as they run."""

    def __init__(self, sink: Callable[[str], None] | None = None):
        # Default sink writes to the terminal on its own line (a leading newline
        # keeps a status line from colliding with Atlas's mid-stream text).
        # Injectable for tests (the test sink captures the raw msg, no newline).
        self._sink = sink if sink is not None else (lambda m: print("\n" + m, flush=True))

    def emit(self, msg: str) -> None:
        self._sink(msg)

    # Convenience helpers so adapters don't hand-format the common shapes.
    def start(self, emoji: str, who: str, doing: str, subject: str) -> None:
        self.emit(f"{emoji} {who} is {doing} '{subject}'…")

    def done(self, who: str, result: str) -> None:
        self.emit(f"✅ {who} {result}")

    def fail(self, who: str, why: str) -> None:
        self.emit(f"⚠️  {who} hit a problem: {why}")


def list_progress() -> tuple[Progress, list[str]]:
    """A Progress backed by a list — for tests. Returns (progress, captured_lines)."""
    lines: list[str] = []
    return Progress(sink=lines.append), lines
