"""Mason's conceptual-diagram renderer — a DiagramPlan -> animated flat SVG.

Pure, deterministic, byte-stable. The LLM (in Magpie, off the render path) emits a tiny
closed-vocab `DiagramPlan`; THIS module (in Mason, at compose time) turns that plan into
flat inline SVG + GSAP timeline tweens on the scene's paused master timeline — exactly the
way `render_bar_chart` emits inline SVG and effect builders emit `tl.from/.to` lines. No
RoughJS, no Node, no static file, no render-time `Math.random`/`Date.now`/SMIL: any
"random" value is a seeded constant baked at compose from `seed=hash(shot_id)`.

Locked architecture: docs/superpowers/specs/2026-06-24-diagram-generator.md §3.5 (D16).
Templates own layout (D8): the renderer positions everything into FIXED SLOTS computed
from the component count — the plan never carries coordinates.

The closed vocabularies are the contract with Magpie's planner; an unknown token is a hard
error (mirrors the engine's other closed sets), surfaced to the caller to fall back.
"""
from __future__ import annotations

import html as _html

# ---- closed vocabularies (the DiagramPlan contract) ------------------
DIAGRAM_LAYOUTS = ("left-to-right", "stacked", "grid", "radial", "freeform")
DIAGRAM_COMPONENTS = (
    "node", "labeled-box", "container", "speech-bubble", "thought-bubble",
    "layer-stack", "before-after", "cycle", "grid", "glyph",
)
DIAGRAM_GLYPHS = (
    "person", "gear", "document", "robot-arm", "button", "brain",
    "cloud", "database", "lock",
)
DIAGRAM_EMPHASIS = ("underline", "box", "circle", "highlight", "strike", "cross-off", "bracket")
DIAGRAM_ANIM = ("draw-on", "pop-in", "count-up", "cross-fade")

# canvas the diagram is authored in (16:9; CSS sizes it into the diagram-frame, which the
# layout already zones clear of the caption band — caption clearance pass-by-construction).
_VW, _VH = 1600.0, 900.0


def _esc(s) -> str:
    return _html.escape(str(s), quote=True)


def _luma(hex_color: str) -> float:
    """Relative luminance (0..1) of a #rgb/#rrggbb color; ~0=black, ~1=white. Used only to
    pick a label color that contrasts a node fill — pure + deterministic."""
    s = (hex_color or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return 0.0
    try:
        r, g, b = (int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return 0.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b  # Rec. 709 luma weights


def _on_fill_ink(fill: str) -> str:
    """A legible text color for a label sitting ON `fill` — light ink on a dark box, dark
    ink on a light box. (A node's label must contrast the BOX, not the page; the page ink
    is wrong when the box is filled with the dark `muted` swatch on a light scene.)"""
    return "#F5F5F5" if _luma(fill) < 0.5 else "#1c1c1c"


def _seeded(seed: int):
    """A tiny deterministic PRNG (mulberry32) -> floats in [0,1). Used ONLY at compose to
    bake constants (e.g. particle offsets); never runs at render time."""
    state = seed & 0xFFFFFFFF

    def nxt() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        t = state
        t = (t ^ (t >> 15)) * (t | 1) & 0xFFFFFFFF
        t ^= (t + ((t ^ (t >> 7)) * (t | 61) & 0xFFFFFFFF)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    return nxt


# ======================================================================
# Glyphs — pre-authored flat line-art in a 0..100 box, recolored by stroke.
# Each returns SVG positioned/scaled at (cx, cy) to `size`. Stroke-only (fill:none) so they
# read as clean editorial icons on the dark stage, accent-tinted on emphasis.
# ======================================================================
def _glyph(name: str, cx: float, cy: float, size: float, stroke: str) -> str:
    s = size / 100.0
    # translate so the 0..100 box is centred on (cx,cy), then scale
    tx, ty = cx - size / 2.0, cy - size / 2.0
    g = (f'<g class="dg-glyph" transform="translate({tx:.1f},{ty:.1f}) scale({s:.4f})" '
         f'fill="none" stroke="{stroke}" stroke-width="6" '
         f'stroke-linecap="round" stroke-linejoin="round">')
    body = _GLYPH_PATHS.get(name, "")
    return g + body + "</g>"


_GLYPH_PATHS = {
    "person": ('<circle cx="50" cy="30" r="18"/>'
               '<path d="M18,86 C18,60 38,52 50,52 C62,52 82,60 82,86"/>'),
    "gear": ('<circle cx="50" cy="50" r="20"/>'
             '<path d="M50,12 L50,24 M50,76 L50,88 M12,50 L24,50 M76,50 L88,50 '
             'M23,23 L32,32 M68,68 L77,77 M77,23 L68,32 M32,68 L23,77"/>'),
    "document": ('<path d="M28,14 L62,14 L78,30 L78,86 L28,86 Z"/>'
                 '<path d="M62,14 L62,30 L78,30"/>'
                 '<path d="M38,46 L68,46 M38,58 L68,58 M38,70 L58,70"/>'),
    "robot-arm": ('<rect x="14" y="70" width="28" height="18" rx="3"/>'
                  '<path d="M28,70 L40,46 L66,38"/>'
                  '<circle cx="28" cy="70" r="6"/><circle cx="40" cy="46" r="6"/>'
                  '<path d="M66,30 L84,30 M66,46 L84,46 M66,38 L80,38"/>'),
    "button": ('<rect x="18" y="36" width="64" height="28" rx="14"/>'
               '<circle cx="40" cy="50" r="4"/>'),
    "brain": ('<path d="M50,20 C32,20 24,34 28,46 C18,52 22,70 36,72 '
              'C38,84 60,84 64,72 C78,70 82,52 72,46 C76,34 68,20 50,20 Z"/>'
              '<path d="M50,22 L50,80"/>'),
    "cloud": ('<path d="M34,68 C20,68 18,50 32,48 C34,32 58,32 60,46 '
              'C74,42 80,62 68,68 Z"/>'),
    "database": ('<ellipse cx="50" cy="26" rx="28" ry="10"/>'
                 '<path d="M22,26 L22,74 C22,80 78,80 78,74 L78,26"/>'
                 '<path d="M22,50 C22,56 78,56 78,50"/>'),
    "lock": ('<rect x="26" y="46" width="48" height="38" rx="6"/>'
             '<path d="M36,46 L36,34 C36,22 64,22 64,34 L64,46"/>'
             '<circle cx="50" cy="62" r="5"/>'),
}


# ======================================================================
# Layout — fixed slots from the component count (D8: templates own layout).
# ======================================================================
def _slots(layout: str, n: int) -> list:
    """Return n (cx, cy) centres for the chosen layout, inside a margin-padded canvas."""
    mx, my = 220.0, 150.0
    x0, x1, y0, y1 = mx, _VW - mx, my, _VH - my
    if n <= 0:
        return []
    if n == 1:
        return [((x0 + x1) / 2.0, (y0 + y1) / 2.0)]
    if layout == "stacked" or layout == "layer-stack":
        cy = lambda i: y0 + (y1 - y0) * (i / (n - 1))
        return [((x0 + x1) / 2.0, cy(i)) for i in range(n)]
    if layout == "grid":
        import math
        cols = max(1, int(math.ceil(math.sqrt(n))))
        rows = max(1, int(math.ceil(n / cols)))
        out = []
        for i in range(n):
            r, c = divmod(i, cols)
            cx = x0 + (x1 - x0) * ((c + 0.5) / cols)
            cy = y0 + (y1 - y0) * ((r + 0.5) / rows)
            out.append((cx, cy))
        return out
    if layout in ("radial", "cycle"):
        import math
        cx0, cy0 = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        r = min(x1 - x0, y1 - y0) / 2.0
        return [(cx0 + r * math.cos(2 * math.pi * i / n - math.pi / 2),
                 cy0 + r * math.sin(2 * math.pi * i / n - math.pi / 2)) for i in range(n)]
    # left-to-right (default) and freeform -> an even horizontal row
    cx = lambda i: x0 + (x1 - x0) * (i / (n - 1))
    return [(cx(i), (y0 + y1) / 2.0) for i in range(n)]


# ======================================================================
# Component shapes — flat, editorial, palette-driven.
# ======================================================================
def _node_box(cx, cy, w, h, label, ink, accent, fill, glyph=None, rounded=18):
    parts = [f'<rect class="dg-node" x="{cx - w / 2:.1f}" y="{cy - h / 2:.1f}" '
             f'width="{w:.1f}" height="{h:.1f}" rx="{rounded}" '
             f'fill="{fill}" stroke="{ink}" stroke-width="4" />']
    ly = cy
    if glyph:
        gy = cy - h * 0.16
        parts.append(_glyph(glyph, cx, gy, min(w, h) * 0.46, accent))
        ly = cy + h * 0.32
    if label:
        parts.append(f'<text class="dg-label" x="{cx:.1f}" y="{ly:.1f}" '
                     f'text-anchor="middle" dominant-baseline="middle" '
                     f'fill="{_on_fill_ink(fill)}">{_esc(label)}</text>')
    return "".join(parts)


def _bubble(cx, cy, w, h, label, ink, accent, fill, thought=False, glyph=None):
    parts = [f'<rect class="dg-node" x="{cx - w / 2:.1f}" y="{cy - h / 2:.1f}" '
             f'width="{w:.1f}" height="{h:.1f}" rx="{h / 2:.1f}" '
             f'fill="{fill}" stroke="{ink}" stroke-width="4" />']
    ty = cy + h / 2.0
    if thought:
        parts.append(f'<circle cx="{cx - w * 0.22:.1f}" cy="{ty + 18:.1f}" r="11" '
                     f'fill="{fill}" stroke="{ink}" stroke-width="4"/>'
                     f'<circle cx="{cx - w * 0.30:.1f}" cy="{ty + 40:.1f}" r="7" '
                     f'fill="{fill}" stroke="{ink}" stroke-width="4"/>')
    else:
        parts.append(f'<path class="dg-node" d="M{cx - 26:.1f},{ty - 4:.1f} '
                     f'L{cx - 6:.1f},{ty + 30:.1f} L{cx + 14:.1f},{ty - 4:.1f} Z" '
                     f'fill="{fill}" stroke="{ink}" stroke-width="4"/>')
    ly = cy
    if glyph:
        parts.append(_glyph(glyph, cx, cy - h * 0.14, min(w, h) * 0.4, accent))
        ly = cy + h * 0.30
    if label:
        parts.append(f'<text class="dg-label" x="{cx:.1f}" y="{ly:.1f}" '
                     f'text-anchor="middle" dominant-baseline="middle" '
                     f'fill="{_on_fill_ink(fill)}">{_esc(label)}</text>')
    return "".join(parts)


def _arrow(x0, y0, x1, y1, stroke, idx, curved=False):
    """A draw-on connector (pathLength=1, dashed) + an arrowhead. Each arrow's path gets a
    stable class `dg-edge-N` so the timeline can draw them on in order."""
    import math
    ang = math.atan2(y1 - y0, x1 - x0)
    # stop short of the target node so the head doesn't bury into it
    gap = 46.0
    ex, ey = x1 - gap * math.cos(ang), y1 - gap * math.sin(ang)
    sx, sy = x0 + gap * math.cos(ang), y0 + gap * math.sin(ang)
    if curved:
        mx, my = (sx + ex) / 2.0, (sy + ey) / 2.0 - 90.0
        d = f"M{sx:.1f},{sy:.1f} Q{mx:.1f},{my:.1f} {ex:.1f},{ey:.1f}"
    else:
        d = f"M{sx:.1f},{sy:.1f} L{ex:.1f},{ey:.1f}"
    hl = 18.0
    h1x, h1y = ex - hl * math.cos(ang - 0.4), ey - hl * math.sin(ang - 0.4)
    h2x, h2y = ex - hl * math.cos(ang + 0.4), ey - hl * math.sin(ang + 0.4)
    return (f'<path class="dg-edge dg-edge-{idx}" pathLength="1" fill="none" '
            f'stroke="{stroke}" stroke-width="5" stroke-linecap="round" d="{d}" />'
            f'<path class="dg-edge dg-edge-{idx}" pathLength="1" fill="none" '
            f'stroke="{stroke}" stroke-width="5" stroke-linecap="round" '
            f'd="M{h1x:.1f},{h1y:.1f} L{ex:.1f},{ey:.1f} L{h2x:.1f},{h2y:.1f}" />')


def _emphasis(kind, cx, cy, w, h, accent):
    """A baked Rough-Notation-style emphasis mark (static), under/over the target."""
    if kind == "circle":
        return (f'<ellipse class="dg-emph" cx="{cx:.1f}" cy="{cy:.1f}" '
                f'rx="{w / 2 + 16:.1f}" ry="{h / 2 + 12:.1f}" fill="none" '
                f'stroke="{accent}" stroke-width="5"/>')
    if kind == "box":
        return (f'<rect class="dg-emph" x="{cx - w / 2 - 12:.1f}" y="{cy - h / 2 - 10:.1f}" '
                f'width="{w + 24:.1f}" height="{h + 20:.1f}" rx="10" fill="none" '
                f'stroke="{accent}" stroke-width="5"/>')
    if kind in ("underline", "highlight"):
        yy = cy + h / 2 + 14
        return (f'<line class="dg-emph" x1="{cx - w / 2:.1f}" y1="{yy:.1f}" '
                f'x2="{cx + w / 2:.1f}" y2="{yy:.1f}" stroke="{accent}" stroke-width="6"/>')
    if kind in ("strike", "cross-off"):
        return (f'<line class="dg-emph" x1="{cx - w / 2:.1f}" y1="{cy:.1f}" '
                f'x2="{cx + w / 2:.1f}" y2="{cy:.1f}" stroke="{accent}" stroke-width="6"/>')
    if kind == "bracket":
        return (f'<path class="dg-emph" fill="none" stroke="{accent}" stroke-width="5" '
                f'd="M{cx - w / 2 - 14:.1f},{cy - h / 2:.1f} l-10,0 0,{h:.0f} 10,0"/>')
    return ""


def validate_plan(plan: dict) -> list:
    """Return a list of closed-vocab violations (empty == valid). Mirrors the engine's
    'unknown token = hard error' discipline; the caller falls back on any violation."""
    errs = []
    lh = plan.get("layout_hint")
    if lh is not None and lh not in DIAGRAM_LAYOUTS:
        errs.append(f"unknown layout_hint {lh!r}")
    comps = plan.get("components")
    if not isinstance(comps, list) or not comps:
        errs.append("plan has no components")
        return errs
    ids = {c.get("id") for c in comps if isinstance(c, dict)}
    for i, c in enumerate(comps):
        if not isinstance(c, dict):
            errs.append(f"component {i} is not an object")
            continue
        t = c.get("type")
        if t not in DIAGRAM_COMPONENTS:
            errs.append(f"component {i}: unknown type {t!r}")
        g = c.get("of") or (c.get("glyph") if t == "glyph" else None)
        if g is not None and g not in DIAGRAM_GLYPHS:
            errs.append(f"component {i}: unknown glyph {g!r}")
        em = c.get("emphasis")
        if em is not None and em not in DIAGRAM_EMPHASIS:
            errs.append(f"component {i}: unknown emphasis {em!r}")
        an = c.get("anim")
        if an is not None and an not in DIAGRAM_ANIM:
            errs.append(f"component {i}: unknown anim {an!r}")
        for tgt in (c.get("to") or []):
            if tgt not in ids:
                errs.append(f"component {i}: edge to unknown id {tgt!r}")
    return errs


def render_diagram(plan: dict, *, seed: int, ink: str = "#F5F5F5",
                   accent: str = "#FFD000", muted: str = "#1c1c1c",
                   cls: str = "media") -> dict:
    """Render a validated DiagramPlan -> {svg, tl, n}. Raises ValueError on an invalid plan
    (closed-vocab violation) so the layout can fall back. Deterministic: identical plan +
    seed -> byte-identical SVG; motion rides the paused GSAP timeline (draw-on edges,
    pop-in nodes), never SMIL/clock/Math.random."""
    errs = validate_plan(plan)
    if errs:
        raise ValueError("invalid DiagramPlan: " + "; ".join(errs))
    rnd = _seeded(seed)
    comps = plan["components"]
    layout = plan.get("layout_hint") or "left-to-right"
    n = len(comps)
    centres = _slots(layout, n)

    # node footprint scales down as the count grows so a busy diagram still fits its slots
    w = max(180.0, min(360.0, 1600.0 / (n + 1.4)))
    h = max(120.0, w * 0.62)

    edges, nodes, emph = [], [], []
    edge_idx = 0
    drawn = set()                       # (i,j) index pairs already connected (no dupes)
    idx_of = {c.get("id"): i for i, c in enumerate(comps)}
    # explicit edges first (component.to)
    for i, c in enumerate(comps):
        cx, cy = centres[i]
        for tgt in (c.get("to") or []):
            j = idx_of[tgt]
            tx, ty = centres[j]
            edges.append(_arrow(cx, cy, tx, ty, ink, edge_idx,
                                curved=(layout in ("radial", "cycle"))))
            drawn.add((i, j))
            edge_idx += 1
    # implicit flow connectors: in a flow layout, never leave consecutive nodes unconnected
    # (a flow should always READ as connected even if the plan only linked some pairs).
    if layout in ("left-to-right", "stacked", "freeform") and n > 1:
        for i in range(n - 1):
            if (i, i + 1) in drawn or (i + 1, i) in drawn:
                continue
            (cx, cy), (tx, ty) = centres[i], centres[i + 1]
            edges.append(_arrow(cx, cy, tx, ty, ink, edge_idx))
            edge_idx += 1
    # a ring of implicit connectors for a cycle when the plan didn't wire one
    if layout in ("radial", "cycle") and not drawn and n > 2:
        for i in range(n):
            (cx, cy), (tx, ty) = centres[i], centres[(i + 1) % n]
            edges.append(_arrow(cx, cy, tx, ty, ink, edge_idx, curved=True))
            edge_idx += 1

    for i, c in enumerate(comps):
        cx, cy = centres[i]
        t = c["type"]
        label = c.get("label", "")
        glyph = c.get("of") or (c.get("glyph") if t == "glyph" else None)
        if t in ("speech-bubble", "thought-bubble"):
            nodes.append(_bubble(cx, cy, w, h * 0.84, label, ink, accent, muted,
                                 thought=(t == "thought-bubble"), glyph=glyph))
        elif t == "glyph":
            g = glyph or "gear"
            nodes.append(_glyph(g, cx, cy - 18, min(w, h) * 0.7, accent))
            if label:
                nodes.append(f'<text class="dg-label" x="{cx:.1f}" y="{cy + h * 0.42:.1f}" '
                             f'text-anchor="middle" fill="{ink}">{_esc(label)}</text>')
        elif t in ("container", "labeled-box", "node", "layer-stack", "grid", "before-after",
                   "cycle"):
            nodes.append(_node_box(cx, cy, w, h, label, ink, accent, muted, glyph=glyph))
        else:
            nodes.append(_node_box(cx, cy, w, h, label, ink, accent, muted, glyph=glyph))
        em = c.get("emphasis")
        if em:
            emph.append(_emphasis(em, cx, cy, w, h, accent))

    svg = (f'<svg class="{cls} diagram-svg" viewBox="0 0 {_VW:.0f} {_VH:.0f}" '
           f'preserveAspectRatio="xMidYMid meet" role="img">'
           + "".join(edges) + "".join(nodes) + "".join(emph) + "</svg>")

    # --- reveal timeline (build-time GSAP on the paused master timeline) ---
    tl = []
    # nodes pop in (scale+fade), staggered; bake a tiny seeded delay jitter for life
    jitter = round(rnd() * 0.08, 3)
    tl.append(f'tl.from(".dg-node,.dg-glyph,.dg-label",{{opacity:0,scale:0.7,duration:0.45,'
              f'ease:"back.out(1.4)",stagger:0.09,transformOrigin:"50% 50%"}},{jitter});')
    if edge_idx:
        # edges draw on after the first nodes land, in index order
        tl.append('tl.from(".dg-edge",{strokeDashoffset:1,duration:0.5,ease:"power1.inOut",'
                  'stagger:0.12},0.5);')
    if emph:
        tl.append('tl.from(".dg-emph",{opacity:0,duration:0.4,ease:"power2.out"},0.9);')
    return {"svg": svg, "tl": tl, "n": n}
