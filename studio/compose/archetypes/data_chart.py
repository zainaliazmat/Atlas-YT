"""studio.compose.archetypes.data_chart — the 'data-chart' archetype builder.

Produces bespoke, deterministic HTML + GSAP beats for scenes visualizing accumulated
time or quantity. Lifted from reference S3's calendar-crumble: a grid of cells fills
cell-by-cell (green), then crumbles — each cell scatters by index-derived offsets,
rotates, falls and fades ("disintegrates into grain").

The archetype is registered at import time so compose dispatches to it automatically
whenever Iris (or the heuristic classify()) tags a scene as 'data-chart'.

Determinism guarantee: no Math.random / Date.now / new Date / fetch /
XMLHttpRequest in any authored string. The beats_js uses `tl` (the master
re-timer proxy passed by the Composer, bound to `window.__timelines`).
Scatter is derived from element-index arithmetic only (no clock or RNG).
"""
from __future__ import annotations

import html

from studio.compose import archetypes

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LABEL_MAX = 28
_DEFAULT_LABEL = "DAYS LOST"


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _build_html(scene: dict) -> str:
    """Render the data-chart block for this scene.

    Produces:
      <div class="data-chart anim">
        <div class="calendar-mount"></div>
        <small class="chart-label mono">LABEL</small>
      </div>

    LABEL comes from scene['point'] (preferred) or scene['on_screen_text'],
    upper-cased and capped at _LABEL_MAX characters. Falls back to "DAYS LOST".
    The .calendar-mount is the mount point for the makeCalendarCrumble grid
    (which creates the .calendar-grid element at runtime).
    """
    label_src = scene.get("point") or scene.get("on_screen_text") or ""
    label = html.escape(label_src.upper()[:_LABEL_MAX]) or _DEFAULT_LABEL

    return (
        f'          <div class="data-chart anim">\n'
        f'            <div class="calendar-mount"></div>\n'
        f'            <small class="chart-label mono">{label}</small>\n'
        f'          </div>'
    )


# ---------------------------------------------------------------------------
# GSAP beats builder
# ---------------------------------------------------------------------------

def _build_beats_js(scene: dict, sid: str, at_base: float = 0.6) -> str:
    """Return inline GSAP choreography JS for the data-chart scene.

    Selects `#{sid} .calendar-mount` and calls `makeCalendarCrumble(...)` anchored
    at `at_base` (the scene's authored start time from ctx['at']).

    Deterministic: constants only, no RNG/Date/fetch.
    """
    lines = [
        f"// data-chart archetype — scene #{scene.get('scene_no', '?')} sid={sid}",
        f"(function() {{",
        f"  var mount = document.querySelector('#{sid} .calendar-mount');",
        f"  makeCalendarCrumble({{",
        f"    tl: tl,",
        f"    mount: mount,",
        f"    at: {at_base},",
        f"    color: SPRAY,",
        f"    cells: 36,",
        f"    cols: 6",
        f"  }});",
        f"}})();",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def build(scene: dict, ctx: dict) -> dict:
    """Build the bespoke data-chart beat for *scene*.

    Args:
        scene: the script scene dict (has ``on_screen_text``, ``point``, etc.)
        ctx:   composer context — at minimum ``{"sid": "sN"}``; optional keys:
               ``spray`` (highlight colour), ``ink`` (text colour),
               ``at`` (scene authored base time, default 0.6).

    Returns:
        {"html": str, "beats_js": str, "token": str}
    """
    sid     = ctx.get("sid", "s0")
    at_base = ctx.get("at", 0.6)

    html_block = _build_html(scene)
    beats_js   = _build_beats_js(scene, sid, at_base)

    return {
        "html":     html_block,
        "beats_js": beats_js,
        "token":    "calendar-crumble",
    }


# ---------------------------------------------------------------------------
# Registration (side-effect at import time — the parity invariant)
# ---------------------------------------------------------------------------

archetypes.register("data-chart", build, "calendar-crumble")
