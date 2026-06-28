"""studio.compose.archetypes.title_card — the 'title-card' archetype builder.

Produces bespoke, deterministic HTML + GSAP beats for the opening title scene:
a silhouette self-draws (strokeDashoffset 1→0 via makeOutlineDraw) while a cluster
of geometric icons orbits and settles around it (makeOrbitCluster).

Lifted from reference S2 (GOLDEN_REFERENCE.md §6/§7; reference index.html
~lines 2018–2023 + the halftone portrait): the opening identity reveal.

The archetype is registered at import time so compose dispatches to it automatically
whenever Iris (or the heuristic classify()) tags a scene as 'title-card'.

Determinism guarantee: no Math.random / Date.now / new Date / fetch /
XMLHttpRequest in any authored string. The beats_js uses `tl` (the master
re-timer proxy passed by the Composer, bound to `window.__timelines`) and `SPRAY`
(the highlight colour injected at render time). All timing is constant arithmetic
derived from ctx['at'] only (no clock or RNG). The ITEMS glyph strings are a
deterministic module-level constant list.
"""
from __future__ import annotations

from studio.compose import archetypes


# ---------------------------------------------------------------------------
# Deterministic glyph items for makeOrbitCluster
# ---------------------------------------------------------------------------

# Five simple geometric inline-SVG glyphs — fully deterministic, no RNG.
_ITEMS: list[str] = [
    '<svg viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8"/></svg>',
    '<svg viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16"/></svg>',
    '<svg viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12,3 21,21 3,21"/></svg>',
    '<svg viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="2" x2="12" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/></svg>',
    '<svg viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4,12 9,17 20,7"/></svg>',
]


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _build_html() -> str:
    """Render the title-card block.

    Produces:
      <div class="title-card orbit-fx anim">
        <div class="portrait-mount" style="..."></div>
        <div class="orbit-mount" style="..."></div>
      </div>

    The 'orbit-fx' class carries the literal 'orbit' fragment so that
    gate.parse.scene_signature can match it via static HTML scan.
    The 'orbit-mount' class also carries 'orbit' for belt-and-suspenders matching.
    The .portrait-mount is the mount point for makeOutlineDraw (silhouette self-draw).
    The .orbit-mount is the mount point for makeOrbitCluster (orbiting icon settle).
    """
    return (
        '          <div class="title-card orbit-fx anim">\n'
        '            <div class="portrait-mount" style="position:relative;width:200px;height:220px;margin:0 auto"></div>\n'
        '            <div class="orbit-mount" style="position:relative;width:320px;height:320px;margin:0 auto"></div>\n'
        '          </div>'
    )


# ---------------------------------------------------------------------------
# GSAP beats builder
# ---------------------------------------------------------------------------

def _build_beats_js(sid: str, at_base: float = 0.6, spray: str = "currentColor") -> str:
    """Return inline GSAP choreography JS for the title-card scene.

    Selects `#{sid} .portrait-mount` and calls makeOutlineDraw(...) anchored at
    `at_base`, then selects `#{sid} .orbit-mount` and calls makeOrbitCluster(...)
    anchored at `at_base + 0.3`.

    Deterministic: constants only, no RNG/Date/fetch.
    `tl`, `SPRAY`, and both factories are in scope at render time.
    """
    items_js = (
        "[" +
        ", ".join(
            "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
            for s in _ITEMS
        ) +
        "]"
    )
    orbit_at = round(at_base + 0.3, 10)
    lines = [
        f"// title-card archetype — sid={sid}",
        f"(function() {{",
        f"  var pm = document.querySelector('#{sid} .portrait-mount');",
        f"  makeOutlineDraw({{",
        f"    tl: tl,",
        f"    mount: pm,",
        f"    at: {at_base},",
        f"    color: SPRAY,",
        f"    d: \"M40 200 C 40 90, 160 90, 160 200\",",
        f"    viewBox: \"0 0 200 220\",",
        f"    width: 200,",
        f"    height: 220,",
        f"    strokeWidth: 5,",
        f"    dur: 1.4",
        f"  }});",
        f"  var om = document.querySelector('#{sid} .orbit-mount');",
        f"  makeOrbitCluster({{",
        f"    tl: tl,",
        f"    mount: om,",
        f"    at: {orbit_at},",
        f"    color: SPRAY,",
        f"    radius: 120,",
        f"    node: 54,",
        f"    items: {items_js}",
        f"  }});",
        f"}})();",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def build(scene: dict, ctx: dict) -> dict:
    """Build the bespoke title-card beat for *scene*.

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
        "token":    "orbit",
    }


# ---------------------------------------------------------------------------
# Registration (side-effect at import time — the parity invariant)
# ---------------------------------------------------------------------------

archetypes.register("title-card", build, "orbit")
