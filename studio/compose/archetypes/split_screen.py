"""studio.compose.archetypes.split_screen — the 'split-screen' archetype builder.

Produces bespoke, deterministic HTML + GSAP beats for scenes doing a two-panel
tile comparison with internal parallax. Two panels side-by-side each tile in
(yPercent 12->0, staggered) and their inner layers drift with a gentle yoyo
parallax (opposite directions per panel index — the T-TILE verb).

The archetype is registered at import time so compose dispatches to it
automatically whenever Iris (or the heuristic classify()) tags a scene as
'split-screen'.

Token: NEW 'tile-parallax' token (gate/parse._BEAT_TOKENS recognises it via
the `tile-parallax|makeTileParallax|tile-panel` pattern). The static html uses
the class `tile-parallax-fx` so scene_signature() matches via block_html.

Determinism guarantee: no Math.random / Date.now / new Date / fetch /
XMLHttpRequest in any authored string. The beats_js uses `tl` (the master
re-timer proxy passed by the Composer, bound to `window.__timelines`).
Parallax direction is derived from element-index arithmetic only (no clock or RNG).
"""
from __future__ import annotations

import html
import re

from studio.compose import archetypes

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TAG_MAX = 20
_DEFAULT_LEFT = "BEFORE"
_DEFAULT_RIGHT = "AFTER"

_VS_RE = re.compile(r"\s+vs\s+", re.IGNORECASE)


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _parse_sides(scene: dict) -> tuple[str, str]:
    """Split on_screen_text on ' VS ' (case-insensitive) or '/' to derive LEFT and RIGHT tags.

    Returns (left_tag, right_tag) upper-cased and capped at _TAG_MAX characters.
    Falls back to defaults if no delimiter is found.
    """
    text = (scene.get("on_screen_text") or "").strip()

    # Try ' VS ' / ' vs ' first
    parts = _VS_RE.split(text, maxsplit=1)
    if len(parts) == 2:
        left = html.escape(parts[0].strip().upper()[:_TAG_MAX]) or _DEFAULT_LEFT
        right = html.escape(parts[1].strip().upper()[:_TAG_MAX]) or _DEFAULT_RIGHT
        return left, right

    # Try '/' delimiter
    slash_parts = text.split("/", 1)
    if len(slash_parts) == 2:
        left = html.escape(slash_parts[0].strip().upper()[:_TAG_MAX]) or _DEFAULT_LEFT
        right = html.escape(slash_parts[1].strip().upper()[:_TAG_MAX]) or _DEFAULT_RIGHT
        return left, right

    return _DEFAULT_LEFT, _DEFAULT_RIGHT


def _build_html(scene: dict) -> str:
    """Render the split-screen block for this scene.

    Produces:
      <div class="split-screen tile-parallax-fx anim">
        <div class="tile-panel tile-left"><div class="tile-inner"><span class="tile-tag mono">{LEFT}</span></div></div>
        <div class="tile-panel tile-right"><div class="tile-inner"><span class="tile-tag mono">{RIGHT}</span></div></div>
      </div>

    The `tile-parallax-fx` class carries the literal `tile-parallax` string fragment
    so scene_signature() matches via block_html even without a scoped beats_js line.
    """
    left, right = _parse_sides(scene)
    return (
        f'          <div class="split-screen tile-parallax-fx anim">\n'
        f'            <div class="tile-panel tile-left"><div class="tile-inner"><span class="tile-tag mono">{left}</span></div></div>\n'
        f'            <div class="tile-panel tile-right"><div class="tile-inner"><span class="tile-tag mono">{right}</span></div></div>\n'
        f'          </div>'
    )


# ---------------------------------------------------------------------------
# GSAP beats builder
# ---------------------------------------------------------------------------

def _build_beats_js(scene: dict, sid: str, at_base: float = 0.6) -> str:
    """Return inline GSAP choreography JS for the split-screen scene.

    Selects `#{sid} .split-screen` and calls `makeTileParallax(...)` anchored
    at `at_base` (the scene's authored start time from ctx['at']).

    Deterministic: constants only, no RNG/Date/fetch.
    """
    lines = [
        f"// split-screen archetype — scene #{scene.get('scene_no', '?')} sid={sid}",
        f"(function() {{",
        f"  var mount = document.querySelector('#{sid} .split-screen');",
        f"  makeTileParallax({{",
        f"    tl: tl,",
        f"    mount: mount,",
        f"    at: {at_base},",
        f"    dur: 6",
        f"  }});",
        f"}})();",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def build(scene: dict, ctx: dict) -> dict:
    """Build the bespoke split-screen beat for *scene*.

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
        "token":    "tile-parallax",
    }


# ---------------------------------------------------------------------------
# Registration (side-effect at import time — the parity invariant)
# ---------------------------------------------------------------------------

archetypes.register("split-screen", build, "tile-parallax")
