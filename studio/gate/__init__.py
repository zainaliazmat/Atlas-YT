"""studio.gate — the quality scorecard + publish blocker (see
docs/superpowers/specs/2026-06-28-reference-quality-compose-and-quality-gate-design.md).
Public seam (gate.score) is filled in Task 8."""
from __future__ import annotations
from .scorecard import score, build_scorecard   # noqa: E402,F401
