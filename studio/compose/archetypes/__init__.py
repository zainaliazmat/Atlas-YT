"""studio.compose.archetypes — the bespoke per-scene archetype registry.

ARCHETYPES is the CLOSED vocab (== Iris art_engine LAYOUTS). REGISTRY maps an archetype to
its builder(scene, ctx) -> {"html": str, "beats_js": str, "token": str}. token_for() names
the motion_variety beat-token an archetype emits — the gate must recognize it (parity test).
classify() is the heuristic fallback when a scene has no Iris tag."""
from __future__ import annotations

ARCHETYPES = (
    "centered-statement", "split-screen", "full-bleed-image", "lower-third",
    "data-chart", "quote-card", "map-focus", "list-stack", "comparison-2up",
    "title-card", "big-number", "timeline", "diagram",
)

# archetype -> the motion_variety beat-token it emits. Grown as builders land (Phase C).
# Until a builder exists, an archetype maps to an already-known token so parity holds.
_TOKEN: dict[str, str] = {
    "big-number": "count-up",
    "quote-card": "quote-cards",
    "list-stack": "checklist",
    "centered-statement": "underline",
}

REGISTRY: dict = {}   # filled by Phase C builder tasks via register()


def token_for(archetype: str) -> str:
    return _TOKEN.get(archetype, "underline")


def register(archetype: str, builder, token: str) -> None:
    """Register an archetype builder AND its motion_variety token together (the invariant).

    A new archetype MUST call this in the same commit that adds its beat-token to
    gate/parse._BEAT_TOKENS — this is the CEO-mandated parity invariant."""
    REGISTRY[archetype] = builder
    _TOKEN[archetype] = token


def classify(scene: dict) -> str:
    """Heuristic fallback tag when a scene carries no Iris archetype. Mirrors the old
    keyword logic but returns a vocab archetype."""
    from studio.gate.parse import is_attributed_quote
    ost = (scene.get("on_screen_text") or "") + " " + (scene.get("narration") or "")
    claims = scene.get("claims") or []
    if any(is_attributed_quote((c.get("text") if isinstance(c, dict) else c) or "")
           for c in claims):
        return "quote-card"
    import re
    if re.search(r"\d", scene.get("on_screen_text") or ""):
        return "big-number"
    if any(w in ost.lower() for w in ("checklist", "steps", "off", "on ")):
        return "list-stack"
    return "centered-statement"


def _load_builders() -> None:
    """Import every builder submodule so its register() side-effect populates REGISTRY
    when this package is imported (compose imports `from . import archetypes`)."""
    import importlib
    import pkgutil
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_"):
            continue
        importlib.import_module(f"{__name__}.{mod.name}")


_load_builders()
