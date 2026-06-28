"""studio.compose.archetypes.full_bleed_image — the 'full-bleed-image' archetype builder.

Produces bespoke, deterministic HTML + GSAP beats for scenes that put the product
front and centre: a simulated phone frame containing an infinite-scrolling feed, a
fake cursor doing periodic flick gestures, and a slot-reel that rolls.

Lifted from reference S4 (GOLDEN_REFERENCE.md §6; reference index.html lines
2110–2180): "the product is happening to you" scene.

The archetype is registered at import time so compose dispatches to it automatically
whenever Iris (or the heuristic classify()) tags a scene as 'full-bleed-image'.

Determinism guarantee: no Math.random / Date.now / new Date / fetch /
XMLHttpRequest in any authored string. The beats_js uses `tl` (the master
re-timer proxy passed by the Composer, bound to `window.__timelines`).
All timing is derived from element-index arithmetic only (no clock or RNG).
"""
from __future__ import annotations

from studio.compose import archetypes


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _build_html() -> str:
    """Render the full-bleed-image block.

    Produces:
      <div class="full-bleed-image device-loop-fx anim">
        <div class="device-mount"></div>
      </div>

    The 'device-loop-fx' class carries the literal 'device-loop' fragment so that
    gate.parse.scene_signature can match it via static HTML scan.
    The .device-mount is the mount point for the makeDeviceLoop factory (which
    creates the phone frame, feed, cursor and slot-reel at runtime).
    """
    return (
        '          <div class="full-bleed-image device-loop-fx anim">\n'
        '            <div class="device-mount"></div>\n'
        '          </div>'
    )


# ---------------------------------------------------------------------------
# GSAP beats builder
# ---------------------------------------------------------------------------

def _build_beats_js(sid: str, at_base: float = 0.6, spray: str = "currentColor") -> str:
    """Return inline GSAP choreography JS for the full-bleed-image scene.

    Selects `#{sid} .device-mount` and calls `makeDeviceLoop(...)` anchored at
    `at_base` (the scene's authored start time from ctx['at']).

    Deterministic: constants only, no RNG/Date/fetch.
    """
    lines = [
        f"// full-bleed-image archetype — sid={sid}",
        f"(function() {{",
        f"  var mount = document.querySelector('#{sid} .device-mount');",
        f"  makeDeviceLoop({{",
        f"    tl: tl,",
        f"    mount: mount,",
        f"    at: {at_base},",
        f"    color: SPRAY,",
        f"    rows: 12,",
        f"    scrollDur: 8,",
        f"    flicks: 5",
        f"  }});",
        f"}})();",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def build(scene: dict, ctx: dict) -> dict:
    """Build the bespoke full-bleed-image beat for *scene*.

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
    spray   = ctx.get("spray", "currentColor")

    html_block = _build_html()
    beats_js   = _build_beats_js(sid, at_base, spray)

    return {
        "html":     html_block,
        "beats_js": beats_js,
        "token":    "device-loop",
    }


# ---------------------------------------------------------------------------
# Registration (side-effect at import time — the parity invariant)
# ---------------------------------------------------------------------------

archetypes.register("full-bleed-image", build, "device-loop")
