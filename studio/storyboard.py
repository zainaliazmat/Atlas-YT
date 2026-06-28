"""studio.storyboard — the archetype-tagging stage.

Iris (art_engine.build_storyboard) plans each scene's layout from her closed LAYOUTS vocab;
we adopt that layout as the scene's archetype tag (compose reads it). Heuristic classify()
is the fallback when Iris is unavailable or emits a layout outside the vocab.

Never raises — a tagging gap degrades to classify, never blocks the pipeline.
"""
from __future__ import annotations

from .compose import archetypes as A


def tag_archetypes(script: dict, pdir, *, iris_fn=None) -> dict:
    """Tag each scene in script with an archetype from the closed ARCHETYPES vocab.

    Calls Iris via iris_fn (default: engines.storyboard); maps the returned scene layout
    to the archetype if it is in ARCHETYPES, else falls back to archetypes.classify(scene).
    On any Iris failure, all scenes fall back to classify(). Never raises.

    Returns: {"scenes": [{"scene_no": int, "archetype": str}, ...]}
    """
    from . import engines
    iris_fn = iris_fn or (lambda s, p: engines.storyboard(s, p))
    scenes = script.get("scenes") or []
    try:
        board = iris_fn(script, pdir) or {}
        by_no = {s.get("scene_no"): s for s in (board.get("scenes") or [])}
    except Exception:  # noqa: BLE001
        by_no = {}
    out = []
    for sc in scenes:
        no = sc.get("scene_no")
        layout = (by_no.get(no) or {}).get("layout")
        archetype = layout if layout in A.ARCHETYPES else A.classify(sc)
        out.append({"scene_no": no, "archetype": archetype})
    return {"scenes": out}
