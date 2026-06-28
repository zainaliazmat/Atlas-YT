"""studio.compose.archetypes.big_number — the 'big-number' archetype builder.

Produces bespoke, deterministic HTML + GSAP beats for scenes whose centrepiece
is a single dominant statistic (e.g. "5.66B USERS", "96× a day", "141 minutes").
The number ticks up from 0 with integer/decimal snap and a suffix, inside a
stat-card that punches in via `makeBigNumber` from the motion library.

The archetype is registered at import time so compose dispatches to it
automatically whenever Iris (or the heuristic classify()) tags a scene as
'big-number'.

Determinism guarantee: no Math.random / Date.now / new Date / fetch /
XMLHttpRequest in any authored string.  The beats_js uses `tl` (the master
re-timer proxy passed by the Composer, bound to `window.__timelines`).
"""
from __future__ import annotations

import html
import re

from studio.compose import archetypes

# ---------------------------------------------------------------------------
# Number parsing helper (mirrors studio.compose.__init__._num but also
# handles × as a tight suffix, per the big-number archetype spec)
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([A-Za-z%×]+)?")
_TIGHT_SUFFIXES = {"%", "x", "×"}


def _num(text: str):
    """Find the first number + optional unit in *text*.

    Returns (target: float, dec: int, suffix: str) or None.
    Mirrors compose.__init__._num but also treats '×' as a tight suffix.
    """
    m = _NUM_RE.search(text or "")
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    unit = m.group(2) or ""
    dec = len(raw.split(".")[1]) if "." in raw else 0
    if unit in _TIGHT_SUFFIXES:
        suffix = unit
    else:
        suffix = f" {unit}" if unit else ""
    try:
        return float(raw), dec, suffix
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

_LABEL_MAX = 24


def _build_html(scene: dict, target: float, dec: int, suffix: str) -> str:
    """Render the stat-card block for this scene.

    Produces a `<div class="big-number stat-card anim">` containing:
      - `<span class="count-host" data-count="…" data-dec="…" data-suffix="…">`
      - `<small class="stat-label mono">LABEL</small>`

    LABEL comes from scene['point'] (preferred) or scene['on_screen_text'],
    upper-cased and capped at 24 characters.  Falls back to "FIGURE".
    """
    # derive the label
    label_src = scene.get("point") or scene.get("on_screen_text") or ""
    label = html.escape(label_src.upper()[:_LABEL_MAX]) or "FIGURE"

    count_str = str(target)  # e.g. "5.66", "96.0" → we want clean repr
    # Format target cleanly: drop trailing .0 for integers, keep actual decimal
    if dec == 0:
        count_str = str(int(target)) if target == int(target) else str(target)
    else:
        # Use the same precision as dec so data-count matches the parsed value
        count_str = f"{target:.{dec}f}".rstrip("0").rstrip(".")
        # Re-apply the exact decimal count the parser found
        count_str = f"{target:.{dec}f}"

    escaped_suffix = html.escape(suffix)

    return (
        f'          <div class="big-number stat-card anim">\n'
        f'            <span class="count-host" data-count="{count_str}"'
        f' data-dec="{dec}" data-suffix="{escaped_suffix}"></span>\n'
        f'            <small class="stat-label mono">{label}</small>\n'
        f'          </div>'
    )


# ---------------------------------------------------------------------------
# GSAP beats builder
# ---------------------------------------------------------------------------

def _build_beats_js(scene: dict, sid: str, color: str,
                    target: float, dec: int, suffix: str,
                    at_base: float = 0.6) -> str:
    """Return inline GSAP choreography JS for the big-number scene.

    Selects `#{sid} .count-host` and calls `makeBigNumber(...)` anchored at
    `at_base` (the scene's authored start time from ctx['at']).

    Deterministic: constants only, no RNG/Date/fetch.
    """
    escaped_suffix = suffix.replace("\\", "\\\\").replace('"', '\\"')
    lines = [
        f"// big-number archetype — scene #{scene.get('scene_no', '?')} sid={sid}",
        f"(function() {{",
        f"  var host = document.querySelector('#{sid} .count-host');",
        f"  makeBigNumber({{",
        f"    tl: tl,",
        f"    mount: host,",
        f"    at: {at_base},",
        f"    color: SPRAY,",
        f"    target: {target},",
        f"    dec: {dec},",
        f'    suffix: "{escaped_suffix}",',
        f"    dur: 1.5",
        f"  }});",
        f"}})();",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def build(scene: dict, ctx: dict) -> dict:
    """Build the bespoke big-number beat for *scene*.

    Args:
        scene: the script scene dict (has ``on_screen_text``, ``point``, etc.)
        ctx:   composer context — at minimum ``{"sid": "sN"}``; optional keys:
               ``spray`` (highlight colour), ``ink`` (text colour),
               ``at`` (scene authored base time, default 0.6).

    Returns:
        {"html": str, "beats_js": str, "token": str}
    """
    sid     = ctx.get("sid", "s0")
    color   = ctx.get("spray", "var(--spray,#2e5e1f)")
    at_base = ctx.get("at", 0.6)

    # parse the dominant number from on_screen_text, fall back to narration
    text = scene.get("on_screen_text") or scene.get("narration") or ""
    parsed = _num(text)
    if parsed is None:
        target, dec, suffix = 0.0, 0, ""
    else:
        target, dec, suffix = parsed

    html_block = _build_html(scene, target, dec, suffix)
    beats_js   = _build_beats_js(scene, sid, color, target, dec, suffix, at_base)

    return {
        "html":     html_block,
        "beats_js": beats_js,
        "token":    "count-up",
    }


# ---------------------------------------------------------------------------
# Registration (side-effect at import time — the parity invariant)
# ---------------------------------------------------------------------------

archetypes.register("big-number", build, "count-up")
