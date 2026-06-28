"""studio.gate.types — shared result shapes + threshold loading for the gate."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_THRESHOLDS_PATH = Path(__file__).with_name("thresholds.json")


@dataclass
class DimResult:
    name: str
    score: float | None          # 0-5, or None when unmeasurable (non-blocking)
    floor: float
    passed: bool | None          # score >= floor; None when unmeasurable
    diagnostics: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


@dataclass
class ComplianceResult:
    name: str
    passed: bool | None          # None = could not run (toolchain gap)
    reason: str = ""


def load_thresholds() -> dict:
    return json.loads(_THRESHOLDS_PATH.read_text(encoding="utf-8"))


def band_score(value: float, low: float, high: float) -> float:
    """Map ``value`` in [low,high] linearly to [0,5], clamped to the ends."""
    if high == low:
        return 5.0 if value >= high else 0.0
    frac = (float(value) - low) / (high - low)
    frac = max(0.0, min(1.0, frac))
    return round(frac * 5.0, 3)
