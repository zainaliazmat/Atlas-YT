"""studio.compose.archetypes.centered_statement — the 'centered-statement' archetype builder.

Produces bespoke, deterministic HTML + GSAP beats for scenes with a single hero
statement that gets struck through and re-asserted (reference S7 "DESIGNED" strike
re-pulse). The beat sequence:
  1. A strike line draws across (scaleX 0→1).
  2. A spray-over restatement rises in.
  3. A stamp punches in (back.out).

Token: the EXISTING 'strike' token (gate/parse._BEAT_TOKENS already recognises it
via the `strike|strikethrough` pattern — no new token needed). The static html uses
the classes `strike-fx` and `strike-line` so scene_signature() matches the `strike`
regex without needing a scoped beats_js line.

Determinism guarantee: no Math.random / Date.now / new Date / fetch /
XMLHttpRequest in any authored string. All timing is constants-only.
"""
from __future__ import annotations

import html

from studio.compose import archetypes

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OVER_MAX = 28
_STAMP_MAX = 14
_DEFAULT_OVER = "BY DESIGN"
_DEFAULT_STAMP = "FACT"


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _derive_texts(scene: dict) -> tuple[str, str]:
    """Derive OVER and STAMP from scene['point'].

    OVER  = upper-cased scene['point'] (≤28 chars), default 'BY DESIGN'.
    STAMP = upper-cased first word of scene['point'] (≤14 chars), default 'FACT'.
    Both are HTML-escaped.
    """
    point = (scene.get("point") or "").strip()
    if point:
        over_raw = point.upper()[:_OVER_MAX]
        stamp_raw = point.split()[0].upper()[:_STAMP_MAX]
    else:
        over_raw = _DEFAULT_OVER
        stamp_raw = _DEFAULT_STAMP
    return html.escape(over_raw), html.escape(stamp_raw)


def _build_html(scene: dict) -> str:
    """Render the centered-statement block for this scene.

    Produces:
      <div class="centered-statement strike-fx anim">
        <div class="strike-line"></div>
        <div class="spray-over mono">{OVER}</div>
        <div class="stamp mono">{STAMP}</div>
      </div>

    The `strike-fx` and `strike-line` classes contain the literal word 'strike' so
    scene_signature() matches via block_html even without a scoped beats_js line.
    """
    over, stamp = _derive_texts(scene)
    return (
        f'          <div class="centered-statement strike-fx anim">\n'
        f'            <div class="strike-line"></div>\n'
        f'            <div class="spray-over mono">{over}</div>\n'
        f'            <div class="stamp mono">{stamp}</div>\n'
        f'          </div>'
    )


# ---------------------------------------------------------------------------
# GSAP beats builder
# ---------------------------------------------------------------------------

def _build_beats_js(scene: dict, sid: str, at_base: float = 0.6, spray: str = "SPRAY") -> str:
    """Return inline GSAP choreography JS for the centered-statement scene.

    Selects `#{sid} .centered-statement` and calls `makeStrikeStamp(...)` anchored
    at `at_base` (the scene's authored start time from ctx['at']).

    Deterministic: constants only, no RNG/Date/fetch.
    """
    lines = [
        f"// centered-statement archetype — scene #{scene.get('scene_no', '?')} sid={sid}",
        f"(function() {{",
        f"  var mount = document.querySelector('#{sid} .centered-statement');",
        f"  makeStrikeStamp({{",
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
    """Build the bespoke centered-statement beat for *scene*.

    Args:
        scene: the script scene dict (has ``point``, ``on_screen_text``, etc.)
        ctx:   composer context — at minimum ``{"sid": "sN"}``; optional keys:
               ``spray`` (highlight colour), ``ink`` (text colour),
               ``at`` (scene authored base time, default 0.6).

    Returns:
        {"html": str, "beats_js": str, "token": str}
    """
    sid     = ctx.get("sid", "s0")
    at_base = ctx.get("at", 0.6)
    spray   = ctx.get("spray", "SPRAY")

    html_block = _build_html(scene)
    beats_js   = _build_beats_js(scene, sid, at_base, spray)

    return {
        "html":     html_block,
        "beats_js": beats_js,
        "token":    "strike",
    }


# ---------------------------------------------------------------------------
# Registration (side-effect at import time — the parity invariant)
# ---------------------------------------------------------------------------

archetypes.register("centered-statement", build, "strike")
