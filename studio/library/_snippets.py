"""Procedural SVG+GSAP snippet generators for studio.library.generate.

Each generator returns a small, deterministic JS module (a factory the Composer
calls with ``{ tl, mount, at, color, size, ... }``) that appends an SVG and
animates it on the shared timeline — the same hand-built approach the golden
reference used for its bell / checkmarks (no Lottie runtime, no sourced asset).

Rules every snippet obeys (GOLDEN_REFERENCE.md §1/§6/§9):
  - deterministic: no Math.random / Date.now / fetch;
  - color is a RUNTIME opt (default currentColor) so ONE cached snippet serves
    every pack color — recolor is free, no regeneration;
  - all motion is authored onto the passed timeline ``tl`` (seek-safe).

The registry maps a semantic name -> (factory function name, JS source).
"""

from __future__ import annotations

_FOOTER = "\nif (typeof module !== \"undefined\" && module.exports) module.exports = {{ {factory} }};\n"


def _wrap(name: str, factory: str, body: str) -> str:
    head = (
        f"/* studio.library procedural snippet: {name}\n"
        f"   Deterministic SVG+GSAP. Call {factory}({{ tl, mount, at, color, size }}).\n"
        f"   color is a runtime opt -> one cached snippet serves every pack color. */\n"
    )
    return head + body + _FOOTER.format(factory=factory)


_COMMON_HEAD = """function {factory}(opts) {{
  opts = opts || {{}};
  var tl = opts.tl;
  if (!tl) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var size = opts.size || 64;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var NS = "http://www.w3.org/2000/svg";
"""


def _svg_draw(factory: str, inner: str, stroke_w: str = "2.4", stagger: float = 0.3) -> str:
    """A factory that appends an SVG and self-draws its strokes (strokeDashoffset)."""
    return _COMMON_HEAD.format(factory=factory) + (
        f'  var svg = document.createElementNS(NS, "svg");\n'
        f'  svg.setAttribute("viewBox", "0 0 24 24");\n'
        f'  svg.setAttribute("width", size); svg.setAttribute("height", size);\n'
        f'  svg.setAttribute("fill", "none");\n'
        f'  svg.setAttribute("stroke", color);\n'
        f'  svg.setAttribute("stroke-width", "{stroke_w}");\n'
        f'  svg.setAttribute("stroke-linecap", "round");\n'
        f'  svg.setAttribute("stroke-linejoin", "round");\n'
        f"  svg.innerHTML = '{inner}';\n"
        f"  mount.appendChild(svg);\n"
        f'  var parts = svg.querySelectorAll("path, circle, line, polyline");\n'
        f"  for (var i = 0; i < parts.length; i++) {{\n"
        f'    parts[i].setAttribute("pathLength", "1");\n'
        f"    tl.fromTo(parts[i], {{ strokeDashoffset: 1, strokeDasharray: 1 }},\n"
        f'      {{ strokeDashoffset: 0, duration: 0.5, ease: "power1.inOut" }}, at + i * {stagger});\n'
        f"  }}\n"
        f"  return svg;\n"
        f"}}\n"
    )


# --- check: circle + tick, drawn on (reference S8) ---------------------------
CHECK = _wrap("check", "makeCheck", _svg_draw(
    "makeCheck", '<circle cx=\"12\" cy=\"12\" r=\"10\"/><path d=\"m9 12 2 2 4-4\"/>'))

# --- cross: two strokes drawn on (reference S7 strike, as an X) --------------
CROSS = _wrap("cross", "makeCross", _svg_draw(
    "makeCross", '<path d=\"M7 7 L17 17\"/><path d=\"M17 7 L7 17\"/>', stroke_w="3"))

# --- underline: hand-drawn underline, drawn on (reference accent.under) ------
UNDERLINE = _wrap("underline", "makeUnderline", _svg_draw(
    "makeUnderline",
    '<path d=\"M2 7 C 22 3, 44 11, 64 6 S 96 8, 98 7\"/>', stroke_w="4.5"))


# --- bell: decaying ring swing (reference S1 bell) ---------------------------
BELL = _wrap("bell", "makeBell", _COMMON_HEAD.format(factory="makeBell") + (
    '  var svg = document.createElementNS(NS, "svg");\n'
    '  svg.setAttribute("viewBox", "0 0 24 24");\n'
    '  svg.setAttribute("width", size); svg.setAttribute("height", size);\n'
    '  svg.setAttribute("fill", "none");\n'
    '  svg.setAttribute("stroke", color);\n'
    '  svg.setAttribute("stroke-width", "1.7");\n'
    '  svg.setAttribute("stroke-linecap", "round");\n'
    '  svg.setAttribute("stroke-linejoin", "round");\n'
    '  svg.style.transformOrigin = "50% 14%";\n'
    "  svg.innerHTML = '<path d=\"M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9\"/>"
    "<path d=\"M10.3 21a1.94 1.94 0 0 0 3.4 0\"/>';\n"
    "  mount.appendChild(svg);\n"
    "  tl.fromTo(svg, { rotation: 0 }, {\n"
    "    keyframes: [\n"
    "      { rotation: 17, duration: 0.12 }, { rotation: -13, duration: 0.16 },\n"
    "      { rotation: 9, duration: 0.14 }, { rotation: -6, duration: 0.13 },\n"
    "      { rotation: 3, duration: 0.12 }, { rotation: 0, duration: 0.13 }\n"
    '    ], ease: "sine.inOut"\n'
    "  }, at);\n"
    "  return svg;\n"
    "}\n"
))


# --- spinner: continuous rotation (reference S4 refresh spinner) -------------
SPINNER = _wrap("spinner", "makeSpinner", _COMMON_HEAD.format(factory="makeSpinner") + (
    "  var spin = typeof opts.spin === \"number\" ? opts.spin : 1.0;\n"
    "  var repeat = typeof opts.repeat === \"number\" ? opts.repeat : 0;\n"
    '  var svg = document.createElementNS(NS, "svg");\n'
    '  svg.setAttribute("viewBox", "0 0 24 24");\n'
    '  svg.setAttribute("width", size); svg.setAttribute("height", size);\n'
    '  svg.setAttribute("fill", "none");\n'
    '  svg.setAttribute("stroke", color);\n'
    '  svg.setAttribute("stroke-width", "3");\n'
    '  svg.setAttribute("stroke-linecap", "round");\n'
    '  svg.style.transformOrigin = "50% 50%";\n'
    "  svg.innerHTML = '<path d=\"M21 12a9 9 0 1 1-6.2-8.5\"/>';\n"
    "  mount.appendChild(svg);\n"
    '  tl.fromTo(svg, { rotation: 0 }, { rotation: 360, duration: spin, ease: "none", repeat: repeat }, at);\n'
    "  return svg;\n"
    "}\n"
))


# --- progress: a fill bar scaling 0 -> pct ----------------------------------
PROGRESS = _wrap("progress", "makeProgress", _COMMON_HEAD.format(factory="makeProgress") + (
    "  var pct = typeof opts.pct === \"number\" ? opts.pct : 1;\n"
    "  var dur = typeof opts.duration === \"number\" ? opts.duration : 1.2;\n"
    "  var width = opts.width || 320;\n"
    '  var track = document.createElement("div");\n'
    '  track.style.cssText = "position:relative;height:10px;border-radius:6px;overflow:hidden;background:rgba(0,0,0,0.12);width:" + width + "px";\n'
    '  var fill = document.createElement("div");\n'
    '  fill.style.cssText = "position:absolute;left:0;top:0;bottom:0;width:100%;border-radius:6px;transform-origin:left center;background:" + color;\n'
    "  track.appendChild(fill);\n"
    "  mount.appendChild(track);\n"
    '  tl.fromTo(fill, { scaleX: 0 }, { scaleX: pct, duration: dur, ease: "power2.out" }, at);\n'
    "  return track;\n"
    "}\n"
))


# --- count-up: number ticks up then settles (reference S1/S3 count-up) -------
COUNT_UP = _wrap("count-up", "makeCountUp", _COMMON_HEAD.format(factory="makeCountUp") + (
    "  var target = typeof opts.target === \"number\" ? opts.target : 100;\n"
    "  var dec = typeof opts.dec === \"number\" ? opts.dec : 0;\n"
    '  var suffix = opts.suffix || "";\n'
    "  var dur = typeof opts.duration === \"number\" ? opts.duration : 1.5;\n"
    '  var el = document.createElement("span");\n'
    "  el.style.color = color;\n"
    "  var fmt = function (v) { return dec > 0 ? v.toFixed(dec) : String(Math.round(v)); };\n"
    "  el.textContent = fmt(0) + suffix;\n"
    "  mount.appendChild(el);\n"
    "  var o = { v: 0 };\n"
    "  tl.to(o, { v: target, duration: dur, ease: \"power2.out\", snap: { v: dec > 0 ? Math.pow(10, -dec) : 1 },\n"
    "    onUpdate: function () { el.textContent = fmt(o.v) + suffix; } }, at);\n"
    '  tl.fromTo(el, { scale: 1 }, { scale: 1.16, duration: 0.1, ease: "power2.out", transformOrigin: "50% 60%" }, at + dur);\n'
    '  tl.to(el, { scale: 1, duration: 0.28, ease: "back.out(2.2)" }, at + dur + 0.1);\n'
    "  return el;\n"
    "}\n"
))


# --- pulse: a dot scale-pulses (reference "alive" beat) ----------------------
PULSE = _wrap("pulse", "makePulse", _COMMON_HEAD.format(factory="makePulse") + (
    "  var repeat = typeof opts.repeat === \"number\" ? opts.repeat : 5;\n"
    "  var dur = typeof opts.duration === \"number\" ? opts.duration : 1.2;\n"
    '  var dot = document.createElement("div");\n'
    '  dot.style.cssText = "width:" + size + "px;height:" + size + "px;border-radius:50%;background:" + color;\n'
    "  mount.appendChild(dot);\n"
    '  tl.to(dot, { scale: 1.18, duration: dur, ease: "sine.inOut", yoyo: true, repeat: repeat, transformOrigin: "50% 50%" }, at);\n'
    "  return dot;\n"
    "}\n"
))


# semantic name -> (factory function name, JS source)
GENERATORS: dict[str, tuple[str, str]] = {
    "check": ("makeCheck", CHECK),
    "cross": ("makeCross", CROSS),
    "underline": ("makeUnderline", UNDERLINE),
    "bell": ("makeBell", BELL),
    "spinner": ("makeSpinner", SPINNER),
    "progress": ("makeProgress", PROGRESS),
    "count-up": ("makeCountUp", COUNT_UP),
    "pulse": ("makePulse", PULSE),
}

# common aliases -> canonical semantic name
ALIASES: dict[str, str] = {
    "checkmark": "check", "tick": "check", "done": "check", "verified": "check",
    "close": "cross", "x-mark": "cross", "dismiss": "cross", "wrong": "cross",
    "ring": "bell", "notification": "bell", "alert": "bell",
    "loading": "spinner", "loader": "spinner", "refresh": "spinner", "refresh-cw": "spinner",
    "bar": "progress", "progress-bar": "progress", "meter": "progress",
    "counter": "count-up", "countup": "count-up", "number": "count-up", "stat": "count-up",
    "ping": "pulse", "throb": "pulse", "heartbeat": "pulse",
    "highlight": "underline", "draw": "underline", "underline-draw": "underline",
}


def resolve_semantic(names) -> str | None:
    """Map a list of tag/name strings to a canonical procedural semantic, or None."""
    for n in names:
        key = str(n).strip().lower()
        if key in GENERATORS:
            return key
        if key in ALIASES:
            return ALIASES[key]
    return None
