"""studio.compose.archetypes.comparison_2up — the 'comparison-2up' archetype builder.

Produces bespoke, deterministic HTML + GSAP beats for scenes doing a two-up
"your worst VS their highlight" comparison. Lifted from reference S6's shatter +
RGB-split glitch: a focus bar breaks into drifting shards (index-derived offsets)
and a stepped red/cyan RGB-split glitch flickers (tl.set steps — seek-safe).

The archetype is registered at import time so compose dispatches to it
automatically whenever Iris (or the heuristic classify()) tags a scene as
'comparison-2up'.

Token: the EXISTING 'shatter' token (gate/parse._BEAT_TOKENS already recognises it
via the `shatter|crumble` pattern — no new token needed).  The static html uses
the class `cmp-shatter-mount` so scene_signature() matches the `shatter` regex
from the block_html even without a scoped beats_js line.

Determinism guarantee: no Math.random / Date.now / new Date / fetch /
XMLHttpRequest in any authored string.  The beats_js uses `tl` (the master
re-timer proxy passed by the Composer, bound to `window.__timelines`).
Shard scatter is derived from element-index arithmetic only (no clock or RNG).
"""
from __future__ import annotations

import html
import re

from studio.compose import archetypes

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TAG_MAX = 22
_DEFAULT_LEFT = "YOUR REALITY"
_DEFAULT_RIGHT = "THEIR HIGHLIGHT"

_VS_RE = re.compile(r"\s+vs\s+", re.IGNORECASE)


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _parse_sides(scene: dict) -> tuple[str, str]:
    """Split on_screen_text on ' VS ' (case-insensitive) to derive LEFT and RIGHT tags.

    Returns (left_tag, right_tag) upper-cased and capped at _TAG_MAX characters.
    Falls back to defaults if no delimiter is found.
    """
    text = (scene.get("on_screen_text") or "").strip()
    parts = _VS_RE.split(text, maxsplit=1)
    if len(parts) == 2:
        left = html.escape(parts[0].strip().upper()[:_TAG_MAX]) or _DEFAULT_LEFT
        right = html.escape(parts[1].strip().upper()[:_TAG_MAX]) or _DEFAULT_RIGHT
    else:
        left = _DEFAULT_LEFT
        right = _DEFAULT_RIGHT
    return left, right


def _build_html(scene: dict) -> str:
    """Render the comparison-2up block for this scene.

    Produces:
      <div class="comparison-2up anim">
        <div class="cmp-panel cmp-left"><span class="cmp-tag mono">{LEFT}</span></div>
        <div class="cmp-shatter-mount"></div>
        <div class="cmp-panel cmp-right"><span class="cmp-tag mono">{RIGHT}</span></div>
      </div>

    The `cmp-shatter-mount` class contains the literal word 'shatter' so
    scene_signature() matches via block_html without needing a scoped beats_js line.
    """
    left, right = _parse_sides(scene)
    return (
        f'          <div class="comparison-2up anim">\n'
        f'            <div class="cmp-panel cmp-left"><span class="cmp-tag mono">{left}</span></div>\n'
        f'            <div class="cmp-shatter-mount"></div>\n'
        f'            <div class="cmp-panel cmp-right"><span class="cmp-tag mono">{right}</span></div>\n'
        f'          </div>'
    )


# ---------------------------------------------------------------------------
# GSAP beats builder
# ---------------------------------------------------------------------------

def _build_beats_js(scene: dict, sid: str, at_base: float = 0.6) -> str:
    """Return inline GSAP choreography JS for the comparison-2up scene.

    Selects `#{sid} .cmp-shatter-mount` and calls `makeShatterGlitch(...)` anchored
    at `at_base` (the scene's authored start time from ctx['at']).

    Deterministic: constants only, no RNG/Date/fetch.
    """
    lines = [
        f"// comparison-2up archetype — scene #{scene.get('scene_no', '?')} sid={sid}",
        f"(function() {{",
        f"  var mount = document.querySelector('#{sid} .cmp-shatter-mount');",
        f"  makeShatterGlitch({{",
        f"    tl: tl,",
        f"    mount: mount,",
        f"    at: {at_base},",
        f"    color: SPRAY,",
        f"    shards: 8",
        f"  }});",
        f"}})();",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def build(scene: dict, ctx: dict) -> dict:
    """Build the bespoke comparison-2up beat for *scene*.

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
        "token":    "shatter",
    }


# ---------------------------------------------------------------------------
# Registration (side-effect at import time — the parity invariant)
# ---------------------------------------------------------------------------

archetypes.register("comparison-2up", build, "shatter")
