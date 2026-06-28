"""studio.compose.archetypes.map_focus — the 'map-focus' archetype builder.

Produces bespoke, deterministic HTML + GSAP beats for scenes that self-draw a
route on a map and pop a destination pin. The route is an SVG path drawn via
strokeDashoffset 1→0, then a pin circle scales in with a back.out ease.

The archetype is registered at import time so compose dispatches to it
automatically whenever Iris (or the heuristic classify()) tags a scene as
'map-focus'.

Determinism guarantee: no Math.random / Date.now / new Date / fetch /
XMLHttpRequest in any authored string. The beats_js uses `tl` (the master
re-timer proxy passed by the Composer, bound to `window.__timelines`).
"""
from __future__ import annotations

import html

from studio.compose import archetypes

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LABEL_MAX = 24
_DEFAULT_LABEL = "FROM HERE"


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _build_html(scene: dict) -> str:
    """Render the map-focus block for this scene.

    Produces:
      <div class="map-focus map-draw-fx anim">
        <div class="map-mount" style="position:relative;width:400px;height:300px;margin:0 auto"></div>
        <div class="map-label mono">{LABEL}</div>
      </div>

    LABEL comes from scene['point'] (preferred) or scene['on_screen_text'],
    upper-cased and capped at _LABEL_MAX characters. Falls back to "FROM HERE".
    The .map-mount is the mount point for makeMapDraw (which creates the inline
    SVG at runtime). The 'map-draw-fx' class carries the literal 'map-draw'
    for static signature matching.
    """
    label_src = scene.get("point") or scene.get("on_screen_text") or ""
    label = html.escape(label_src.upper()[:_LABEL_MAX]) or _DEFAULT_LABEL

    return (
        f'          <div class="map-focus map-draw-fx anim">\n'
        f'            <div class="map-mount" style="position:relative;width:400px;height:300px;margin:0 auto"></div>\n'
        f'            <div class="map-label mono">{label}</div>\n'
        f'          </div>'
    )


# ---------------------------------------------------------------------------
# GSAP beats builder
# ---------------------------------------------------------------------------

def _build_beats_js(scene: dict, sid: str, spray: str, at_base: float = 0.6) -> str:
    """Return inline GSAP choreography JS for the map-focus scene.

    Selects `#{sid} .map-mount` and calls `makeMapDraw(...)` anchored
    at `at_base` (the scene's authored start time from ctx['at']).

    Deterministic: constants only, no RNG/Date/fetch.
    """
    lines = [
        f"// map-focus archetype — scene #{scene.get('scene_no', '?')} sid={sid}",
        f"(function() {{",
        f"  var mount = document.querySelector('#{sid} .map-mount');",
        f"  makeMapDraw({{",
        f"    tl: tl,",
        f"    mount: mount,",
        f"    at: {at_base},",
        f"    color: SPRAY",
        f"  }});",
        f"}})();",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def build(scene: dict, ctx: dict) -> dict:
    """Build the bespoke map-focus beat for *scene*.

    Args:
        scene: the script scene dict (has ``on_screen_text``, ``point``, etc.)
        ctx:   composer context — at minimum ``{"sid": "sN"}``; optional keys:
               ``spray`` (highlight colour), ``ink`` (text colour),
               ``at`` (scene authored base time, default 0.6).

    Returns:
        {"html": str, "beats_js": str, "token": str}
    """
    sid    = ctx.get("sid", "s0")
    spray  = ctx.get("spray", "currentColor")
    at_base = ctx.get("at", 0.6)

    html_block = _build_html(scene)
    beats_js   = _build_beats_js(scene, sid, spray, at_base)

    return {
        "html":     html_block,
        "beats_js": beats_js,
        "token":    "map-draw",
    }


# ---------------------------------------------------------------------------
# Registration (side-effect at import time — the parity invariant)
# ---------------------------------------------------------------------------

archetypes.register("map-focus", build, "map-draw")
