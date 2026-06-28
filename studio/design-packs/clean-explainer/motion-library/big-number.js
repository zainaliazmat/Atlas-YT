/* motion-library beat: big-number
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
