"""studio.compose.archetypes.quote_cards — the 'quote-card' archetype builder.

Produces bespoke, deterministic HTML + GSAP beats for scenes whose centrepiece
is one or more attributed quotes (the Raskin/Brichter pattern from the S5
reference).  Each card gets:
  - a parallax entry tween (y + opacity)
  - a highlighter-swipe beat (`makeHighlighterSwipe`) under the `.quote-body`

The archetype is registered at import time so compose dispatches to it
automatically whenever Iris (or the heuristic classify()) tags a scene as
'quote-card'.

Determinism guarantee: no Math.random / Date.now / new Date / fetch /
XMLHttpRequest in any authored string.  The beats_js uses `tl` (the master
timeline passed by the Composer, bound to `window.__timelines`).
"""
from __future__ import annotations

from studio.compose import _content
from studio.compose import archetypes

# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _build_html(scene: dict) -> str:
    """Render the quote-card block for this scene using the shared content builder.

    Returns the `.claims` HTML produced by `_content.render_claims`, which already
    emits `.quote-card / .quote-body / .byline` markup for attributed quotes.
    Falls back to an empty claims wrapper so the DOM slot always exists.
    """
    claims_html = _content.render_claims(scene)
    if not claims_html:
        # No attributed claims; emit an empty placeholder so beats still have a mount
        claims_html = '<div class="claims"></div>'
    return claims_html


# ---------------------------------------------------------------------------
# GSAP beats builder
# ---------------------------------------------------------------------------

_STAGGER_ENTRY = 0.15   # seconds between card entries
_ENTRY_DUR     = 0.55   # parallax y+opacity fade-in per card
_SWEEP_DUR     = 0.45   # highlighter-swipe duration per card
_ENTRY_Y       = 28     # pixels each card rises from on entry


def _build_beats_js(scene: dict, sid: str, color: str, at_base: float = 0.6) -> str:
    """Return inline GSAP choreography JS for the quote-card scene.

    For each `.quote-card` inside `#<sid>`:
      1. parallax entry: `tl.from(card, {y, opacity, duration, ease})`, staggered
      2. highlighter swipe: `makeHighlighterSwipe({ tl, mount: bodyEl, ... })`

    Uses the `tl` variable (master timeline) injected by the Composer.
    Deterministic: constants only, no RNG/Date/fetch.

    ``at_base`` is the scene's authored start time (``ctx["at"]``); tweens are
    anchored relative to it so they land in the correct scene window, not at t=0.
    """
    lines = [
        f"// quote-card archetype — scene #{scene.get('scene_no', '?')} sid={sid}",
        f"(function() {{",
        f"  var _sid = {repr(sid)};",
        f"  var _color = {repr(color)};",
        f"  var _base = {at_base};",
        f"  var _cards = document.querySelectorAll('#' + _sid + ' .quote-card');",
        f"  for (var _i = 0; _i < _cards.length; _i++) {{",
        f"    var _card = _cards[_i];",
        f"    var _at = _base + _i * {_STAGGER_ENTRY};",
        # parallax entry: card rises into view
        f"    tl.from(_card, {{ y: {_ENTRY_Y}, opacity: 0, duration: {_ENTRY_DUR},"
        f" ease: 'power2.out' }}, _at);",
        # highlighter swipe under the quote body
        f"    var _body = _card.querySelector('.quote-body');",
        f"    if (_body) {{",
        f"      makeHighlighterSwipe({{",
        f"        tl: tl,",
        f"        mount: _body,",
        f"        at: _at + {_ENTRY_DUR * 0.6:.2f},",
        f"        color: _color,",
        f"        dur: {_SWEEP_DUR},",
        f"        width: 320,",
        f"        height: 20,",
        f"        top: '55%'",
        f"      }});",
        f"    }}",
        f"  }}",
        f"}})();",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def build(scene: dict, ctx: dict) -> dict:
    """Build the bespoke quote-card beat for *scene*.

    Args:
        scene: the script scene dict (has ``claims``, ``on_screen_text``, etc.)
        ctx:   composer context — at minimum ``{"sid": "sN"}``; optional keys:
               ``spray`` (highlight colour), ``ink`` (text colour).

    Returns:
        {"html": str, "beats_js": str, "token": str}
    """
    sid     = ctx.get("sid", "s0")
    color   = ctx.get("spray", "var(--spray,#2e5e1f)")
    at_base = ctx.get("at", 0.6)

    html_block = _build_html(scene)
    beats_js   = _build_beats_js(scene, sid, color, at_base)

    return {
        "html":     html_block,
        "beats_js": beats_js,
        "token":    "quote-cards",
    }


# ---------------------------------------------------------------------------
# Registration (side-effect at import time — the parity invariant)
# ---------------------------------------------------------------------------

archetypes.register("quote-card", build, "quote-cards")
