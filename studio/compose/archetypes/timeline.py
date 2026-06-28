"""studio.compose.archetypes.timeline — the 'timeline' archetype builder.

Produces bespoke, deterministic HTML + GSAP beats for scenes presenting a
sequential timeline (a rail with nodes that reveal one by one as the rail
draws across). Nodes are derived from the scene's bullets/list_items,
on_screen_text (split on '/' and newlines), or a point/dash fallback.

The archetype is registered at import time so compose dispatches to it
automatically whenever Iris (or the heuristic classify()) tags a scene as
'timeline'.

Determinism guarantee: no Math.random / Date.now / new Date / fetch /
XMLHttpRequest in any authored string.  The beats_js uses `tl` (the master
re-timer proxy passed by the Composer, bound to `window.__timelines`).
"""
from __future__ import annotations

import html

from studio.compose import archetypes

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_ITEMS = 5


# ---------------------------------------------------------------------------
# Item extraction helper
# ---------------------------------------------------------------------------

def _extract_items(scene: dict) -> list[str]:
    """Derive timeline node labels from the scene dict.

    Priority:
      1. scene['bullets']      — explicit list of strings
      2. scene['list_items']   — alternate key for the same
      3. scene['on_screen_text'] split on '/' and newlines
      4. scene['point']        — single-item fallback
      5. "—"                   — absolute fallback

    Items are stripped, empty strings discarded, capped at _MAX_ITEMS.
    """
    # 1. explicit bullets list
    bullets = scene.get("bullets") or scene.get("list_items")
    if bullets and isinstance(bullets, list):
        items = [str(b).strip() for b in bullets if str(b).strip()]
        return items[:_MAX_ITEMS]

    # 2. split on_screen_text on '/' and newlines
    ost = scene.get("on_screen_text") or ""
    if ost:
        parts: list[str] = []
        for chunk in ost.split("/"):
            for line in chunk.split("\n"):
                stripped = line.strip()
                if stripped:
                    parts.append(stripped)
        if parts:
            return parts[:_MAX_ITEMS]

    # 3. point fallback
    point = scene.get("point") or ""
    if point.strip():
        return [point.strip()]

    return ["—"]


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _build_html(scene: dict) -> str:
    """Render the timeline rail block for this scene.

    Produces:
      <div class="timeline timeline-rail-fx anim">
        <div class="rail-line"></div>
        <div class="rail-nodes">
          <div class="rail-node"><span class="node-dot"></span><span class="node-label mono">{item}</span></div>
          ... one per item ...
        </div>
      </div>

    The 'timeline-rail-fx' class carries the literal 'timeline-rail'
    for static signature matching.
    """
    items = _extract_items(scene)
    nodes: list[str] = []
    for item in items:
        label = html.escape(item)
        node = (
            f'          <div class="rail-node">'
            f'<span class="node-dot"></span>'
            f'<span class="node-label mono">{label}</span>'
            f'</div>'
        )
        nodes.append(node)

    inner = "\n".join(nodes)
    return (
        f'          <div class="timeline timeline-rail-fx anim">\n'
        f'            <div class="rail-line"></div>\n'
        f'            <div class="rail-nodes">\n'
        f'{inner}\n'
        f'            </div>\n'
        f'          </div>'
    )


# ---------------------------------------------------------------------------
# GSAP beats builder
# ---------------------------------------------------------------------------

def _build_beats_js(scene: dict, sid: str, spray: str, at_base: float = 0.6) -> str:
    """Return inline GSAP choreography JS for the timeline scene.

    Selects `#{sid} .timeline` and calls `makeTimelineRail(...)` anchored
    at `at_base` (the scene's authored start time from ctx['at']).

    Deterministic: constants only, no RNG/Date/fetch.
    """
    lines = [
        f"// timeline archetype — scene #{scene.get('scene_no', '?')} sid={sid}",
        f"(function() {{",
        f"  var el = document.querySelector('#{sid} .timeline');",
        f"  makeTimelineRail({{",
        f"    tl: tl,",
        f"    mount: el,",
        f"    at: {at_base},",
        f"    color: SPRAY,",
        f"    stagger: 0.4",
        f"  }});",
        f"}})();",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def build(scene: dict, ctx: dict) -> dict:
    """Build the bespoke timeline beat for *scene*.

    Args:
        scene: the script scene dict (has ``on_screen_text``, ``bullets``,
               ``list_items``, ``point``, etc.)
        ctx:   composer context — at minimum ``{"sid": "sN"}``; optional keys:
               ``spray`` (highlight colour), ``ink`` (text colour),
               ``at`` (scene authored base time, default 0.6).

    Returns:
        {"html": str, "beats_js": str, "token": str}
    """
    sid     = ctx.get("sid", "s0")
    spray   = ctx.get("spray", "currentColor")
    at_base = ctx.get("at", 0.6)

    html_block = _build_html(scene)
    beats_js   = _build_beats_js(scene, sid, spray, at_base)

    return {
        "html":     html_block,
        "beats_js": beats_js,
        "token":    "timeline-rail",
    }


# ---------------------------------------------------------------------------
# Registration (side-effect at import time — the parity invariant)
# ---------------------------------------------------------------------------

archetypes.register("timeline", build, "timeline-rail")
