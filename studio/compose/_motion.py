"""Pack motion-library beats authored by the Composer + the write-back policy.

When the Composer needs a reusable bespoke beat the pack lacks, it authors it
here, saves it to ``design-packs/<pack>/motion-library/`` and registers it in
``pack.json``'s ``motion_index`` — so the pack's motion expressiveness COMPOUNDS
across videos (mirrors the asset-library write-back policy).

Each beat is a self-contained, deterministic factory (inline-styled SVG/DOM +
GSAP authored on the passed timeline ``tl``; no Math.random/Date.now/fetch). The
Composer inlines them into the composition and calls them per scene.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import config

# --- beat 1: outline self-draw (reference portrait outline / spray accents) --
OUTLINE_DRAW = """/* motion-library beat: outline-self-draw
   makeOutlineDraw({ tl, mount, at, color, d, viewBox, width, height, strokeWidth, dur })
   Appends an inline-styled SVG path and self-draws it (strokeDashoffset 1->0). */
function makeOutlineDraw(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var dur = typeof opts.dur === "number" ? opts.dur : 1.4;
  var NS = "http://www.w3.org/2000/svg";
  var svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", opts.viewBox || "0 0 100 100");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.style.cssText = "position:absolute;left:0;top:0;width:" + (opts.width || 100) + "px;height:" + (opts.height || 100) + "px;overflow:visible;pointer-events:none";
  var p = document.createElementNS(NS, "path");
  p.setAttribute("d", opts.d || "M2 50 C 30 20, 70 80, 98 50");
  p.setAttribute("fill", "none");
  p.setAttribute("stroke", color);
  p.setAttribute("stroke-width", String(opts.strokeWidth || 5));
  p.setAttribute("stroke-linecap", "round");
  p.setAttribute("stroke-linejoin", "round");
  p.setAttribute("pathLength", "1");
  svg.appendChild(p);
  mount.appendChild(svg);
  tl.fromTo(p, { strokeDashoffset: 1, strokeDasharray: 1 },
    { strokeDashoffset: 0, duration: dur, ease: "power1.inOut" }, at);
  return svg;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeOutlineDraw };
"""

# --- beat 2: highlighter swipe (reference S5 marker sweep) -------------------
HIGHLIGHTER_SWIPE = """/* motion-library beat: highlighter-swipe
   makeHighlighterSwipe({ tl, mount, at, color, width, height, top, dur })
   Appends a spray marker bar behind text and sweeps it on (scaleX 0->1). */
function makeHighlighterSwipe(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var dur = typeof opts.dur === "number" ? opts.dur : 0.5;
  var bar = document.createElement("div");
  bar.style.cssText =
    "position:absolute;left:-6px;top:" + (opts.top || "48%") + ";width:" + (opts.width || 260) + "px;height:" +
    (opts.height || 22) + "px;background:" + color + ";opacity:0.55;border-radius:4px 7px 5px 6px;" +
    "transform:scaleX(0);transform-origin:left center;pointer-events:none;z-index:0";
  mount.appendChild(bar);
  tl.fromTo(bar, { scaleX: 0 }, { scaleX: 1, duration: dur, ease: "power2.inOut" }, at);
  return bar;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeHighlighterSwipe };
"""

# --- beat 3: orbit cluster (reference S2/S3 icons orbiting a center) ---------
ORBIT_CLUSTER = """/* motion-library beat: orbit-cluster
   makeOrbitCluster({ tl, mount, at, color, items, radius, node, dur })
   Appends a ring of icon nodes orbiting a center dot; inner icons counter-rotate
   so they stay upright. items = array of inner-SVG markup strings. Deterministic
   (trig placement, no RNG). */
function makeOrbitCluster(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var items = opts.items || [];
  var R = typeof opts.radius === "number" ? opts.radius : 90;
  var node = typeof opts.node === "number" ? opts.node : 54;
  var dur = typeof opts.dur === "number" ? opts.dur : 26;

  var ring = document.createElement("div");
  ring.style.cssText = "position:absolute;inset:0;will-change:transform";
  var center = document.createElement("div");
  center.style.cssText = "position:absolute;left:50%;top:50%;width:24px;height:24px;margin:-12px 0 0 -12px;border-radius:50%;background:" + color + ";box-shadow:0 0 0 7px rgba(46,94,31,0.13)";
  mount.appendChild(center);
  mount.appendChild(ring);

  var n = items.length || 1;
  var inner = [];
  for (var i = 0; i < n; i++) {
    var a = (i / n) * Math.PI * 2 - Math.PI / 2;
    var el = document.createElement("div");
    el.style.cssText =
      "position:absolute;left:50%;top:50%;width:" + node + "px;height:" + node + "px;margin:" + (-node / 2) +
      "px 0 0 " + (-node / 2) + "px;border-radius:50%;background:var(--paper,#f2eed6);border:2px solid var(--ink,#1f1f1e);" +
      "display:flex;align-items:center;justify-content:center;" +
      "transform:translate(" + (R * Math.cos(a)).toFixed(2) + "px," + (R * Math.sin(a)).toFixed(2) + "px)";
    var ico = document.createElement("div");
    ico.style.cssText = "width:56%;height:56%;display:flex;align-items:center;justify-content:center;will-change:transform";
    ico.innerHTML = items[i] || "";
    el.appendChild(ico);
    ring.appendChild(el);
    inner.push(ico);
  }
  tl.from(mount, { opacity: 0, scale: 0.82, duration: 0.6, ease: "power2.out", transformOrigin: "50% 50%" }, at);
  tl.to(ring, { rotation: 360, duration: dur, ease: "none", transformOrigin: "50% 50%" }, at + 0.4);
  for (var j = 0; j < inner.length; j++) {
    tl.to(inner[j], { rotation: -360, duration: dur, ease: "none" }, at + 0.4);
  }
  return ring;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeOrbitCluster };
"""

# id -> (factory, filename, source)
BEATS: dict[str, tuple[str, str, str]] = {
    "outline-self-draw": ("makeOutlineDraw", "outline-self-draw.js", OUTLINE_DRAW),
    "highlighter-swipe": ("makeHighlighterSwipe", "highlighter-swipe.js", HIGHLIGHTER_SWIPE),
    "orbit-cluster": ("makeOrbitCluster", "orbit-cluster.js", ORBIT_CLUSTER),
}


def ensure_motion_library(pack) -> dict[str, str]:
    """Write the beats into the pack's motion-library/ and register them in
    pack.json.motion_index (idempotent). Returns {beat_id: js_source}.

    This is the write-back: a genuinely reusable beat authored once is saved to
    the pack so the next video gets it for free.
    """
    pack_dir = pack.dir
    ml = pack_dir / "motion-library"
    ml.mkdir(parents=True, exist_ok=True)

    manifest_path = pack_dir / "pack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    motion_index = manifest.setdefault("motion_index", [])
    known = {m.get("id") for m in motion_index}

    out: dict[str, str] = {}
    changed = False
    for beat_id, (factory, filename, source) in BEATS.items():
        path = ml / filename
        if not path.exists() or path.read_text(encoding="utf-8") != source:
            path.write_text(source, encoding="utf-8")
            changed = True
        out[beat_id] = source
        if beat_id not in known:
            motion_index.append({
                "id": beat_id, "partial": f"motion-library/{filename}",
                "kind": "js", "exports": [factory],
            })
            known.add(beat_id)
            changed = True

    if changed:
        tmp = manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(manifest_path)
    return out
