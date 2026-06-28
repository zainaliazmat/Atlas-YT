"""studio.compose.archetypes.list_stack — the 'list-stack' archetype builder.

Produces bespoke, deterministic HTML + GSAP beats for scenes presenting a short
checklist or sequential step list (do this, not that pattern). Lifted from
reference S8's grayscale-drain checklist: each row's checkmark self-draws in
sequence (strokeDashoffset 1→0 per circle+path, staggered), the row sliding in.

The archetype is registered at import time so compose dispatches to it
automatically whenever Iris (or the heuristic classify()) tags a scene as
'list-stack'.

Determinism guarantee: no Math.random / Date.now / new Date / fetch /
XMLHttpRequest in any authored string.  The beats_js uses `tl` (the master
re-timer proxy passed by the Composer, bound to `window.__timelines`).
"""
from __future__ import annotations

import html

from studio.compose import archetypes

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_ITEMS = 6


# ---------------------------------------------------------------------------
# Item extraction helper
# ---------------------------------------------------------------------------

def _extract_items(scene: dict) -> list[str]:
    """Derive list items from the scene dict.

    Priority:
      1. scene['bullets']      — explicit list of strings
      2. scene['list_items']   — alternate key for the same
      3. scene['on_screen_text'] split on '/' and newlines
      4. scene['point']        — single-item fallback
      5. "—"                   — absolute fallback

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
        # split on slash first, then newlines within each chunk
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

    return ["—"]


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def _build_html(scene: dict, color: str) -> str:
    """Render the checklist block for this scene.

    Produces a `<div class="checklist">` with one `<div class="check-row anim">`
    per item. Each row contains an inline `.check-mark` SVG (circle + checkmark
    path, both fill="none" stroke the spray colour, pathLength="1") and a
    `<span class="check-label">{item}</span>`.
    """
    items = _extract_items(scene)
    rows: list[str] = []
    for item in items:
        label = html.escape(item)
        svg = (
            f'<svg class="check-mark" viewBox="0 0 24 24" width="28" height="28"'
            f' xmlns="http://www.w3.org/2000/svg">'
            f'<circle cx="12" cy="12" r="11"'
            f' fill="none" stroke="{html.escape(color)}"'
            f' stroke-width="1.5" stroke-linecap="round" pathLength="1"></circle>'
            f'<path d="M5 13 l4 4 l10 -10"'
            f' fill="none" stroke="{html.escape(color)}"'
            f' stroke-width="1.8" stroke-linecap="round" pathLength="1"></path>'
            f'</svg>'
        )
        row = (
            f'          <div class="check-row anim">\n'
            f'            {svg}\n'
            f'            <span class="check-label">{label}</span>\n'
            f'          </div>'
        )
        rows.append(row)

    inner = "\n".join(rows)
    return f'          <div class="checklist">\n{inner}\n          </div>'


# ---------------------------------------------------------------------------
# GSAP beats builder
# ---------------------------------------------------------------------------

def _build_beats_js(scene: dict, sid: str, color: str, at_base: float = 0.6) -> str:
    """Return inline GSAP choreography JS for the list-stack scene.

    Selects `#{sid} .checklist` and calls `makeChecklistDraw(...)` anchored at
    `at_base` (the scene's authored start time from ctx['at']).

    Deterministic: constants only, no RNG/Date/fetch.
    """
    lines = [
        f"// list-stack archetype — scene #{scene.get('scene_no', '?')} sid={sid}",
        f"(function() {{",
        f"  var el = document.querySelector('#{sid} .checklist');",
        f"  makeChecklistDraw({{",
        f"    tl: tl,",
        f"    mount: el,",
        f"    at: {at_base},",
        f"    color: SPRAY,",
        f"    stagger: 0.35,",
        f"    dur: 0.5",
        f"  }});",
        f"}})();",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def build(scene: dict, ctx: dict) -> dict:
    """Build the bespoke list-stack beat for *scene*.

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
    color   = ctx.get("spray", "var(--spray,#2e5e1f)")
    at_base = ctx.get("at", 0.6)

    html_block = _build_html(scene, color)
    beats_js   = _build_beats_js(scene, sid, color, at_base)

    return {
        "html":     html_block,
        "beats_js": beats_js,
        "token":    "checklist",
    }


# ---------------------------------------------------------------------------
# Registration (side-effect at import time — the parity invariant)
# ---------------------------------------------------------------------------

archetypes.register("list-stack", build, "checklist")
