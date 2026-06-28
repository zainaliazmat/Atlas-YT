/* motion-library beat: checklist-draw
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
