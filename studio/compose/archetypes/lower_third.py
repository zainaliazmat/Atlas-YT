"""studio.compose.archetypes.lower_third — the 'lower-third' archetype builder.

Produces bespoke, deterministic HTML + GSAP beats for a lower-third name/handle
bar with a self-writing signature flourish.

Lifted from reference S9 (GOLDEN_REFERENCE.md §7 — `.accent-draw` paths with
`pathLength="1"` self-drawn via `strokeDashoffset 1→0`): the identity reveal
lower-third with a cursive signature flourish path.

The archetype is registered at import time so compose dispatches to it automatically
whenever Iris (or the heuristic classify()) tags a scene as 'lower-third'.

Determinism guarantee: no Math.random / Date.now / new Date / fetch /
XMLHttpRequest in any authored string. The beats_js uses `tl` (the master
re-timer proxy passed by the Composer, bound to `window.__timelines`) and `SPRAY`
(the highlight colour injected at render time). All timing is constant arithmetic
derived from ctx['at'] only (no clock or RNG).

Token is the EXISTING 'signature' token (gate/parse._BEAT_TOKENS has
("signature", r'signature|writeOn')). The static html carries the literal
'signature' via class 'signature-fx' / '.signature', which matches the
'signature' pattern BEFORE the later 'underline' pattern ever matches
'makeOutlineDraw'. No new token is introduced.
"""
from __future__ import annotations

import html as _html_mod

from studio.compose import archetypes


# ---------------------------------------------------------------------------
# Name + handle derivation
# ---------------------------------------------------------------------------

_MAX_NAME_LEN: int = 24
_DEFAULT_NAME: str = "FIELD REPORT"
_DEFAULT_HANDLE: str = "@FIELDREPORT"


def _derive_name(scene: dict) -> str:
    """Extract and normalise the NAME from the scene.

    Priority: scene['point'] > scene['on_screen_text'] > 'FIELD REPORT'.
    Upper-cased, truncated to 24 chars, HTML-escaped.
    """
    raw = scene.get("point") or scene.get("on_screen_text") or ""
    name = raw.strip().upper()[:_MAX_NAME_LEN] or _DEFAULT_NAME
    return _html_mod.escape(name)


def _derive_handle(scene: dict, name: str) -> str:
    """Derive a mono handle from the working title or fall back to default.

    If the scene carries a 'working_title' key we derive a slug from it;
    otherwise we fall back to '@FIELDREPORT'.
    HTML-escaped.
    """
    title = scene.get("working_title") or ""
    if title:
        slug = "".join(c.upper() for c in title if c.isalpha() or c.isdigit())[:12]
        handle = "@" + (slug or "FIELDREPORT")
    else:
        handle = _DEFAULT_HANDLE
    return _html_mod.escape(handle)


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _build_html(name: str, handle: str) -> str:
    """Render the lower-third block.

    Produces:
      <div class="lower-third signature-fx anim">
        <div class="signature" style="position:relative;width:360px;height:90px"></div>
        <div class="handle-row mono"><span class="lt-name">{NAME}</span> <span class="lt-handle">{HANDLE}</span></div>
      </div>

    The 'signature-fx' class carries the literal 'signature' so that
    gate.parse.scene_signature can match it via the static HTML scan using the
    'signature' token pattern (r'signature|writeOn').  The '.signature' div also
    carries the literal for belt-and-suspenders matching.

    Because _BEAT_TOKENS orders 'signature' (index 10) BEFORE 'underline'
    (index 11, pattern r'makeOutlineDraw|underline'), the 'signature' token wins
    even though beats_js calls makeOutlineDraw.  This ordering must be preserved
    in gate/parse.py — a parity test locks it.
    """
    return (
        '          <div class="lower-third signature-fx anim">\n'
        '            <div class="signature" style="position:relative;width:360px;height:90px"></div>\n'
        f'            <div class="handle-row mono"><span class="lt-name">{name}</span>'
        f' <span class="lt-handle">{handle}</span></div>\n'
        '          </div>'
    )


# ---------------------------------------------------------------------------
# GSAP beats builder
# ---------------------------------------------------------------------------

_FLOURISH_D: str = "M20 60 C 60 20, 120 90, 170 50 S 280 20, 340 55"


def _build_beats_js(sid: str, at_base: float = 0.6, spray: str = "currentColor") -> str:
    """Return inline GSAP choreography JS for the lower-third scene.

    Selects `#{sid} .signature` and calls makeOutlineDraw(...) anchored at
    `at_base` (the cursive signature flourish).
    Then selects `#{sid} .handle-row` and calls tl.from(...) anchored at
    `at_base + 0.8` (the name/handle fade-in).

    Deterministic: constants only, no RNG/Date/fetch.
    `tl`, `SPRAY`, and makeOutlineDraw are in scope at render time.
    """
    handle_at = round(at_base + 0.8, 10)
    lines = [
        f"// lower-third archetype — sid={sid}",
        f"(function() {{",
        f"  var sig = document.querySelector('#{sid} .signature');",
        f"  makeOutlineDraw({{",
        f"    tl: tl,",
        f"    mount: sig,",
        f"    at: {at_base},",
        f"    color: SPRAY,",
        f"    d: \"{_FLOURISH_D}\",",
        f"    viewBox: \"0 0 360 90\",",
        f"    width: 360,",
        f"    height: 90,",
        f"    strokeWidth: 4,",
        f"    dur: 1.6",
        f"  }});",
        f"  var hr = document.querySelector('#{sid} .handle-row');",
        f"  tl.from(hr, {{ y: 16, opacity: 0, duration: 0.5, ease: \"power3.out\" }}, {handle_at});",
        f"}})();",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def build(scene: dict, ctx: dict) -> dict:
    """Build the bespoke lower-third beat for *scene*.

    Args:
        scene: the script scene dict (has ``on_screen_text``, ``point``, etc.)
        ctx:   composer context — at minimum ``{"sid": "sN"}``; optional keys:
               ``spray`` (highlight colour), ``ink`` (text colour),
               ``at`` (scene authored base time, default 0.6).

    Returns:
        {"html": str, "beats_js": str, "token": str}

    The returned token is always "signature" — matching the EXISTING entry in
    gate/parse._BEAT_TOKENS.  No new token is created.
    """
    sid     = ctx.get("sid", "s0")
    at_base = ctx.get("at", 0.6)
    spray   = ctx.get("spray", "currentColor")

    name   = _derive_name(scene)
    handle = _derive_handle(scene, name)

    html_block = _build_html(name, handle)
    beats_js   = _build_beats_js(sid, at_base, spray)

    return {
        "html":     html_block,
        "beats_js": beats_js,
        "token":    "signature",
    }


# ---------------------------------------------------------------------------
# Registration (side-effect at import time — the parity invariant)
# ---------------------------------------------------------------------------

archetypes.register("lower-third", build, "signature")
