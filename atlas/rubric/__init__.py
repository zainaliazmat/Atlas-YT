"""atlas.rubric — the FROZEN, CEO-owned quality standard.

This package is the success criterion of the whole self-improvement system, and
it is deliberately the *most privileged* object in it: the improvement loop may
read it but can NEVER write it. That asymmetry is enforced here STRUCTURALLY,
not by convention:

  * The only public entry point, ``load_rubric()``, returns a DEEPLY IMMUTABLE
    view (nested ``MappingProxyType`` + tuples). Any attempt to mutate it raises
    ``TypeError``.
  * There is NO save / write / dump function in this module. The improver
    imports ``load_rubric`` and the band helpers; there is simply no code path
    that writes ``rubric.json``.

Mirrors the ``atlas/contracts/`` pattern (frozen JSON + lru_cache loader), but
where contracts pin artifact *shapes*, the rubric pins quality *targets*
(weights, bands, judged-reference pool). Bands flagged ``placeholder: true`` are
to be replaced by reference-derived values in step 1 of the path; the methods,
ownership, comparators and roll-up structure are the stable part.
"""
from __future__ import annotations

import json
import pathlib
from functools import lru_cache
from types import MappingProxyType
from typing import Any

_DIR = pathlib.Path(__file__).parent
_RUBRIC_FILE = _DIR / "rubric.json"


def _deep_freeze(obj: Any) -> Any:
    """Recursively convert dicts->MappingProxyType and lists->tuple so the
    returned rubric cannot be mutated by any caller (the improver included)."""
    if isinstance(obj, dict):
        return MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, (list, tuple)):
        return tuple(_deep_freeze(v) for v in obj)
    return obj


@lru_cache(maxsize=1)
def load_rubric() -> MappingProxyType:
    """Load the frozen rubric as a deeply-immutable mapping.

    Cached: every caller shares the one frozen instance. Mutating it raises
    ``TypeError`` — this is the read-only guarantee the improver depends on.
    """
    raw = json.loads(_RUBRIC_FILE.read_text())
    return _deep_freeze(raw)


# --- read-only accessors ---------------------------------------------------

def rubric_version() -> str:
    return load_rubric()["rubric_version"]


def global_weights() -> MappingProxyType:
    return load_rubric()["global_weights"]


def global_dimensions() -> MappingProxyType:
    return load_rubric()["global_dimensions"]


def bands() -> MappingProxyType:
    """All bands, keyed by ``"<stage>:<prop>"``."""
    return load_rubric()["bands"]


def band(stage: str, prop: str) -> MappingProxyType | None:
    """The band for one property, or None if the rubric does not score it."""
    return load_rubric()["bands"].get(f"{stage}:{prop}")


def band_by_id(band_id: str) -> MappingProxyType | None:
    return load_rubric()["bands"].get(band_id)


def floor_properties() -> tuple[str, ...]:
    """The hard pass/fail floor property ids (the 'F' dimension)."""
    return tuple(load_rubric()["floor"]["properties"])


def judged_pool(prop: str) -> MappingProxyType | None:
    return load_rubric()["judged_pool"].get(prop)


def ceo_anchor_path() -> pathlib.Path:
    """Absolute path to the (optional, stubbed) CEO-anchor labels file."""
    return _DIR / load_rubric()["ceo_anchor"]["path"]


# Intentionally NO save()/write()/dump(): the rubric has no write path. This is
# the privilege asymmetry made structural. Do not add one — a CEO-owned change
# to the standard is a human edit to rubric.json, never an improver action.

__all__ = [
    "load_rubric", "rubric_version", "global_weights", "global_dimensions",
    "bands", "band", "band_by_id", "floor_properties", "judged_pool",
    "ceo_anchor_path",
]
