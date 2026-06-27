/* motion-library beat: strike-stamp
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
