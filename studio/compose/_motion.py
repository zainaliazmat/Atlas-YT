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

# --- beat 4: big-number (count-up tick + stat-card punch-in) ------------------
BIG_NUMBER = """/* motion-library beat: big-number
   makeBigNumber({ tl, mount, at, color, target, dec, suffix, dur })
   `mount` is the .count-host element. Punches in the enclosing .stat-card (scale +
   back.out), then ticks the number 0->target with integer/decimal snap via onUpdate and a
   suffix. Deterministic (no Math.random/Date/fetch — Math.round/Math.pow are fine). */
function makeBigNumber(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var target = typeof opts.target === "number" ? opts.target : 0;
  var dec = typeof opts.dec === "number" ? opts.dec : 0;
  var suffix = opts.suffix || "";
  var dur = typeof opts.dur === "number" ? opts.dur : 1.5;
  var card = mount.closest ? mount.closest(".stat-card") : null;
  if (card) {
    tl.from(card, { scale: 0.6, opacity: 0, duration: 0.5, ease: "back.out(2.2)", transformOrigin: "50% 50%" }, at);
  }
  var box = { v: 0 };
  var pow = Math.pow(10, dec);
  tl.to(box, { v: target, duration: dur, ease: "power1.out", onUpdate: function () {
    var val = Math.round(box.v * pow) / pow;
    mount.textContent = val.toFixed(dec) + suffix;
  } }, at + (card ? 0.18 : 0));
  return mount;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeBigNumber };
"""

# --- beat 5: checklist-draw (sequential checkmark self-draw) ------------------
CHECKLIST_DRAW = """/* motion-library beat: checklist-draw
   makeChecklistDraw({ tl, mount, at, color, stagger, dur })
   For each `.check-row` inside mount: the row rises/fades in and its `.check-mark` SVG
   strokes self-draw (strokeDashoffset 1->0), staggered down the list. Deterministic
   (index-derived timing only; no clock/RNG). */
function makeChecklistDraw(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var stagger = typeof opts.stagger === "number" ? opts.stagger : 0.35;
  var dur = typeof opts.dur === "number" ? opts.dur : 0.5;
  var rows = mount.querySelectorAll(".check-row");
  for (var i = 0; i < rows.length; i++) {
    var t = at + i * stagger;
    tl.from(rows[i], { x: -20, opacity: 0, duration: 0.4, ease: "power2.out" }, t);
    var strokes = rows[i].querySelectorAll(".check-mark path, .check-mark circle");
    for (var j = 0; j < strokes.length; j++) {
      tl.fromTo(strokes[j], { strokeDashoffset: 1, strokeDasharray: 1 },
        { strokeDashoffset: 0, duration: dur, ease: "power1.inOut" }, t + 0.1);
    }
  }
  return mount;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeChecklistDraw };
"""

# --- beat 9: device-loop (phone mockup: infinite feed + cursor + slot-reel) ---
DEVICE_LOOP = """/* motion-library beat: device-loop
   makeDeviceLoop({ tl, mount, at, color, rows, scrollDur, flicks })
   Builds a phone frame in mount with an infinite-scrolling feed, a fake cursor doing `flicks`
   periodic flick gestures (index-timed), and a slot-reel that rolls. Deterministic — all timing
   from element-index arithmetic; no clock or RNG. Returns the phone node. */
function makeDeviceLoop(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var rows = typeof opts.rows === "number" ? opts.rows : 12;
  var scrollDur = typeof opts.scrollDur === "number" ? opts.scrollDur : 8;
  var flicks = typeof opts.flicks === "number" ? opts.flicks : 5;
  var phone = document.createElement("div");
  phone.className = "device-frame";
  phone.style.cssText = "position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:300px;height:560px;border:3px solid " + color + ";border-radius:32px;overflow:hidden;background:rgba(0,0,0,0.04)";
  var feed = document.createElement("div");
  feed.className = "device-feed";
  feed.style.cssText = "position:absolute;left:0;top:0;width:100%;will-change:transform";
  var rowH = 84;
  for (var i = 0; i < rows; i++) {
    var r = document.createElement("div");
    r.style.cssText = "height:" + (rowH - 12) + "px;margin:6px 10px;border-radius:8px;background:rgba(0,0,0,0.10);opacity:" + (0.5 + (i % 5) * 0.1);
    feed.appendChild(r);
  }
  phone.appendChild(feed);
  mount.appendChild(phone);
  var dist = rows * rowH - 560 + 100;
  tl.fromTo(feed, { y: 0 }, { y: -dist, duration: scrollDur, ease: "none" }, at);
  var cur = document.createElement("div");
  cur.className = "device-cursor";
  cur.style.cssText = "position:absolute;left:50%;top:60%;width:18px;height:18px;border-radius:50%;background:" + color + ";opacity:0.8;will-change:transform";
  phone.appendChild(cur);
  for (var k = 0; k < flicks; k++) {
    var ft = at + 0.5 + k * (scrollDur / (flicks + 1));
    tl.to(cur, { y: -40, duration: 0.18, ease: "power2.in" }, ft);
    tl.to(cur, { y: 0, duration: 0.3, ease: "power1.out" }, ft + 0.18);
  }
  var reel = document.createElement("div");
  reel.className = "slot-reel";
  reel.style.cssText = "position:absolute;left:50%;bottom:18px;transform:translateX(-50%);width:60px;height:40px;border:2px solid " + color + ";border-radius:6px;overflow:hidden";
  var strip = document.createElement("div");
  strip.style.cssText = "will-change:transform";
  for (var j = 0; j < 8; j++) {
    var cell = document.createElement("div");
    cell.style.cssText = "height:40px;display:flex;align-items:center;justify-content:center;font:700 22px monospace;color:" + color;
    cell.textContent = String(j % 10);
    strip.appendChild(cell);
  }
  reel.appendChild(strip); phone.appendChild(reel);
  tl.fromTo(strip, { y: 0 }, { y: -280, duration: 1.4, ease: "back.out(1.05)" }, at + 0.4);
  return phone;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeDeviceLoop };
"""

# --- beat 8: strike-stamp (strike-through + spray-over restatement + stamp) ----
STRIKE_STAMP = """/* motion-library beat: strike-stamp
   makeStrikeStamp({ tl, mount, at, color })
   Draws a strike line across the statement (scaleX 0->1), re-pulses it, rises in a spray-over
   restatement, then punches in a stamp (back.out). Deterministic (no clock/RNG). Returns mount. */
function makeStrikeStamp(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var line = mount.querySelector(".strike-line");
  var over = mount.querySelector(".spray-over");
  var stamp = mount.querySelector(".stamp");
  if (line) {
    line.style.background = color;
    tl.fromTo(line, { scaleX: 0 }, { scaleX: 1, duration: 0.45, ease: "power2.inOut", transformOrigin: "left center" }, at);
    tl.to(line, { opacity: 0.6, duration: 0.2, yoyo: true, repeat: 2, ease: "sine.inOut" }, at + 0.8);
  }
  if (over) { tl.from(over, { y: 18, opacity: 0, duration: 0.5, ease: "power3.out" }, at + 0.5); }
  if (stamp) { tl.from(stamp, { scale: 1.8, opacity: 0, rotation: -12, duration: 0.4, ease: "back.out(2.4)", transformOrigin: "50% 50%" }, at + 0.9); }
  return mount;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeStrikeStamp };
"""

# --- beat 7: shatter-glitch (focus bar shatters + stepped RGB-split) ----------
SHATTER_GLITCH = """/* motion-library beat: shatter-glitch
   makeShatterGlitch({ tl, mount, at, color, shards, dur })
   A focus bar splits into `shards` pieces that drift apart by index-derived offsets, while a
   stepped red/cyan RGB-split ghost flickers on the mount (tl.set steps — seek-safe).
   Deterministic (no clock/RNG). Returns mount. */
function makeShatterGlitch(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var nshards = typeof opts.shards === "number" ? opts.shards : 8;
  var dur = typeof opts.dur === "number" ? opts.dur : 1.0;
  var bar = document.createElement("div");
  bar.className = "shatter-bar";
  bar.style.cssText = "position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);display:flex;gap:2px";
  var shards = [];
  for (var i = 0; i < nshards; i++) {
    var s = document.createElement("div");
    s.style.cssText = "width:26px;height:90px;background:" + color + ";will-change:transform,opacity";
    bar.appendChild(s); shards.push(s);
  }
  mount.appendChild(bar);
  tl.from(bar, { scaleY: 0, opacity: 0, duration: 0.4, ease: "power2.out", transformOrigin: "50% 50%" }, at);
  var shatterAt = at + 0.6;
  for (var i = 0; i < nshards; i++) {
    var dx = ((i * 37) % 120) - 60;
    var dy = ((i * 53) % 140) - 70;
    var rot = ((i * 41) % 90) - 45;
    tl.to(shards[i], { x: dx, y: dy, rotation: rot, opacity: 0, duration: dur, ease: "power2.in" }, shatterAt + (i % 4) * 0.03);
  }
  var ghost = document.createElement("div");
  ghost.className = "rgb-ghost";
  ghost.style.cssText = "position:absolute;inset:0;mix-blend-mode:screen;pointer-events:none";
  mount.appendChild(ghost);
  var flick = [{ x: -6, o: 0.5 }, { x: 5, o: 0.35 }, { x: -3, o: 0.6 }, { x: 0, o: 0 }];
  for (var k = 0; k < flick.length; k++) {
    tl.set(ghost, { x: flick[k].x, opacity: flick[k].o,
      backgroundColor: (k % 2) ? "rgba(255,0,0,0.25)" : "rgba(0,255,255,0.25)" }, shatterAt + k * 0.06);
  }
  return mount;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeShatterGlitch };
"""

# --- beat 6: calendar-crumble (grid fills cell-by-cell, then disintegrates) ---
CALENDAR_CRUMBLE = """/* motion-library beat: calendar-crumble
   makeCalendarCrumble({ tl, mount, at, color, cells, cols, fillStagger, fillDur, crumbleGap, crumbleDur })
   Builds a `cols`-wide grid of `cells` cells in mount (class 'calendar-grid'), fills each with
   `color` cell-by-cell (staggered), then crumbles: each cell drifts by index-derived offsets,
   rotates, falls and fades. Deterministic — scatter from element-index arithmetic only, no
   clock or RNG. Returns the grid node. */
function makeCalendarCrumble(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var n = typeof opts.cells === "number" ? opts.cells : 36;
  var cols = typeof opts.cols === "number" ? opts.cols : 6;
  var fillStagger = typeof opts.fillStagger === "number" ? opts.fillStagger : 0.05;
  var fillDur = typeof opts.fillDur === "number" ? opts.fillDur : 0.22;
  var crumbleGap = typeof opts.crumbleGap === "number" ? opts.crumbleGap : 1.2;
  var crumbleDur = typeof opts.crumbleDur === "number" ? opts.crumbleDur : 1.1;
  var grid = document.createElement("div");
  grid.className = "calendar-grid";
  grid.style.cssText = "display:grid;grid-template-columns:repeat(" + cols + ",1fr);gap:6px;width:100%;height:100%";
  var cells = [];
  for (var i = 0; i < n; i++) {
    var c = document.createElement("div");
    c.className = "cal-cell";
    c.style.cssText = "background:rgba(0,0,0,0.08);border-radius:3px;aspect-ratio:1/1;will-change:transform,opacity";
    grid.appendChild(c); cells.push(c);
  }
  mount.appendChild(grid);
  var fillEnd = at;
  for (var i = 0; i < n; i++) {
    var ft = at + i * fillStagger;
    tl.to(cells[i], { backgroundColor: color, duration: fillDur, ease: "power1.out" }, ft);
    fillEnd = ft + fillDur;
  }
  var crumbleAt = fillEnd + crumbleGap;
  for (var i = 0; i < n; i++) {
    var dx = ((i * 53) % 100) - 50;
    var dy = ((i * 31) % 100);
    var rot = ((i * 47) % 160) - 80;
    tl.to(cells[i], { x: dx, y: dy + 120, rotation: rot, opacity: 0, duration: crumbleDur, ease: "power2.in" }, crumbleAt + (i % 6) * 0.02);
  }
  return grid;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeCalendarCrumble };
"""

# --- beat 10: tile-parallax (two panels tile in + internal parallax drift) ----
TILE_PARALLAX = """/* motion-library beat: tile-parallax
   makeTileParallax({ tl, mount, at, dur })
   Each `.tile-panel` in mount tiles in (yPercent 12->0, opacity 0->1, staggered) and its
   `.tile-inner` drifts with a gentle yoyo parallax (opposite directions per panel index).
   Deterministic (index-derived; no clock/RNG). Returns mount. */
function makeTileParallax(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var dur = typeof opts.dur === "number" ? opts.dur : 6;
  var panels = mount.querySelectorAll(".tile-panel");
  for (var i = 0; i < panels.length; i++) {
    tl.fromTo(panels[i], { yPercent: 12, opacity: 0 }, { yPercent: 0, opacity: 1, duration: 0.6, ease: "power3.out" }, at + i * 0.12);
    var inner = panels[i].querySelector(".tile-inner");
    if (inner) {
      var dir = (i % 2) ? 1 : -1;
      tl.fromTo(inner, { y: -20 * dir }, { y: 20 * dir, duration: dur, ease: "sine.inOut", yoyo: true, repeat: 1 }, at + 0.4);
    }
  }
  return mount;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeTileParallax };
"""

# id -> (factory, filename, source)
BEATS: dict[str, tuple[str, str, str]] = {
    "outline-self-draw": ("makeOutlineDraw", "outline-self-draw.js", OUTLINE_DRAW),
    "highlighter-swipe": ("makeHighlighterSwipe", "highlighter-swipe.js", HIGHLIGHTER_SWIPE),
    "orbit-cluster": ("makeOrbitCluster", "orbit-cluster.js", ORBIT_CLUSTER),
    "big-number": ("makeBigNumber", "big-number.js", BIG_NUMBER),
    "checklist-draw": ("makeChecklistDraw", "checklist-draw.js", CHECKLIST_DRAW),
    "calendar-crumble": ("makeCalendarCrumble", "calendar-crumble.js", CALENDAR_CRUMBLE),
    "shatter-glitch": ("makeShatterGlitch", "shatter-glitch.js", SHATTER_GLITCH),
    "strike-stamp": ("makeStrikeStamp", "strike-stamp.js", STRIKE_STAMP),
    "device-loop": ("makeDeviceLoop", "device-loop.js", DEVICE_LOOP),
    "tile-parallax": ("makeTileParallax", "tile-parallax.js", TILE_PARALLAX),
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
