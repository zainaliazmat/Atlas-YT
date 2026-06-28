/* motion-library beat: tile-parallax
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
