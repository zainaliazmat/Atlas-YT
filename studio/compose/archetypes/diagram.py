"""studio.compose.archetypes.diagram — the 'diagram' archetype builder.

Produces bespoke, deterministic HTML + GSAP beats for scenes presenting a
flat in-HTML/SVG diagram whose nodes pop in and edges self-draw (the
SVG-experiments direction). Nodes are derived from the scene's
bullets/list_items, on_screen_text (split on '/' and newlines), or a point
fallback, capped at 4; default ["INPUT", "MODEL", "OUTPUT"] if none.

The archetype is registered at import time so compose dispatches to it
automatically whenever Iris (or the heuristic classify()) tags a scene as
'diagram'.

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

_MAX_ITEMS = 4
_DEFAULT_LABELS = ["INPUT", "MODEL", "OUTPUT"]

# SVG geometry constants
_VIEWBOX_W = 800
_VIEWBOX_H = 240
_NODE_W = 150
_NODE_H = 70
_NODE_Y = 85  # top edge of node rect (vertically centered: 85 + 70/2 = 120 = VIEWBOX_H/2)
_NODE_TEXT_Y = 124  # baseline inside node (85 + 70/2 + ~4px cap-height offset)
_EDGE_Y = 120  # midpoint of node height (85 + 70/2)


# ---------------------------------------------------------------------------
# Item extraction helper
# ---------------------------------------------------------------------------

def _extract_items(scene: dict) -> list[str]:
    """Derive diagram node labels from the scene dict.

    Priority:
      1. scene['bullets']        — explicit list of strings
      2. scene['list_items']     — alternate key for the same
      3. scene['on_screen_text'] split on '/' and newlines
      4. scene['point']          — single-item fallback
      5. default ["INPUT", "MODEL", "OUTPUT"] — absolute fallback

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

    # 4. absolute default
    return list(_DEFAULT_LABELS)


# ---------------------------------------------------------------------------
# SVG geometry helper
# ---------------------------------------------------------------------------

def _node_x(index: int, n: int) -> int:
    """Compute x-coordinate (left edge) of node at given index for n total nodes.

    gap = (VIEWBOX_W - NODE_W * n) / (n + 1)
    x_i = gap + i * (NODE_W + gap)
    """
    gap = (_VIEWBOX_W - _NODE_W * n) / (n + 1)
    return round(gap + index * (_NODE_W + gap))


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _build_html(scene: dict, spray: str, ink: str) -> str:
    """Render the diagram SVG block for this scene.

    Produces:
      <div class="diagram diagram-draw-fx anim">
        <svg class="diagram-svg" viewBox="0 0 800 240" width="100%" height="240"
             preserveAspectRatio="xMidYMid meet">
          <!-- edges first (painted under nodes) -->
          <path class="diagram-edge" d="M{x+150} 120 L{x_next} 120" fill="none"
                stroke=SPRAY stroke-width="2" pathLength="1"/>
          ...
          <!-- nodes -->
          <g class="diagram-node">
            <rect x=.. y=85 width=150 height=70 rx=8 fill="none" stroke=SPRAY
                  stroke-width="2"/>
            <text x=center y=124 text-anchor="middle" font-family="monospace"
                  font-size="18" fill=INK>{label}</text>
          </g>
          ...
        </svg>
      </div>

    The 'diagram-draw-fx' class carries the literal 'diagram-draw' for
    static signature matching.
    """
    items = _extract_items(scene)
    n = len(items)

    # Build edges (between consecutive nodes)
    edges_parts: list[str] = []
    for i in range(n - 1):
        x_i = _node_x(i, n)
        x_next = _node_x(i + 1, n)
        edge_x_start = x_i + _NODE_W
        edge = (
            f'<path class="diagram-edge"'
            f' d="M{edge_x_start} {_EDGE_Y} L{x_next} {_EDGE_Y}"'
            f' fill="none" stroke="{html.escape(spray)}" stroke-width="2"'
            f' pathLength="1"/>'
        )
        edges_parts.append(edge)

    # Build nodes
    nodes_parts: list[str] = []
    for i, item in enumerate(items):
        x_i = _node_x(i, n)
        cx = x_i + _NODE_W // 2  # horizontal center of node
        label = html.escape(item)
        node = (
            f'<g class="diagram-node">'
            f'<rect x="{x_i}" y="{_NODE_Y}" width="{_NODE_W}" height="{_NODE_H}" rx="8"'
            f' fill="none" stroke="{html.escape(spray)}" stroke-width="2"/>'
            f'<text x="{cx}" y="{_NODE_TEXT_Y}" text-anchor="middle"'
            f' font-family="monospace" font-size="18" fill="{html.escape(ink)}">'
            f'{label}'
            f'</text>'
            f'</g>'
        )
        nodes_parts.append(node)

    edges_svg = "".join(edges_parts)
    nodes_svg = "".join(nodes_parts)

    return (
        f'          <div class="diagram diagram-draw-fx anim">\n'
        f'            <svg class="diagram-svg" viewBox="0 0 {_VIEWBOX_W} {_VIEWBOX_H}"'
        f' width="100%" height="{_VIEWBOX_H}" preserveAspectRatio="xMidYMid meet">'
        f'{edges_svg}{nodes_svg}'
        f'</svg>\n'
        f'          </div>'
    )


# ---------------------------------------------------------------------------
# GSAP beats builder
# ---------------------------------------------------------------------------

def _build_beats_js(scene: dict, sid: str, spray: str, at_base: float = 0.6) -> str:
    """Return inline GSAP choreography JS for the diagram scene.

    Selects `#{sid} .diagram-svg` and calls `makeDiagramDraw(...)` anchored
    at `at_base` (the scene's authored start time from ctx['at']).

    Deterministic: constants only, no RNG/Date/fetch.
    """
    lines = [
        f"// diagram archetype — scene #{scene.get('scene_no', '?')} sid={sid}",
        f"(function() {{",
        f"  var el = document.querySelector('#{sid} .diagram-svg');",
        f"  makeDiagramDraw({{",
        f"    tl: tl,",
        f"    mount: el,",
        f"    at: {at_base},",
        f"    color: SPRAY,",
        f"    stagger: 0.5",
        f"  }});",
        f"}})();",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def build(scene: dict, ctx: dict) -> dict:
    """Build the bespoke diagram beat for *scene*.

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
    ink     = ctx.get("ink", "currentColor")
    at_base = ctx.get("at", 0.6)

    html_block = _build_html(scene, spray, ink)
    beats_js   = _build_beats_js(scene, sid, spray, at_base)

    return {
        "html":     html_block,
        "beats_js": beats_js,
        "token":    "diagram-draw",
    }


# ---------------------------------------------------------------------------
# Registration (side-effect at import time — the parity invariant)
# ---------------------------------------------------------------------------

archetypes.register("diagram", build, "diagram-draw")
