/* motion-library beat: timeline-rail
   makeTimelineRail({ tl, mount, at, color, stagger })
   The `.rail-line` in mount draws across (scaleX 0->1) while each `.rail-node` pops in
   sequentially (scale + back.out, staggered). Deterministic (index-derived; no clock/RNG).
   Returns mount. */
function makeTimelineRail(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var stagger = typeof opts.stagger === "number" ? opts.stagger : 0.4;
  var line = mount.querySelector(".rail-line");
  if (line) {
    line.style.background = color;
    tl.fromTo(line, { scaleX: 0 }, { scaleX: 1, duration: 1.2, ease: "power1.inOut", transformOrigin: "left center" }, at);
  }
  var nodes = mount.querySelectorAll(".rail-node");
  for (var i = 0; i < nodes.length; i++) {
    tl.from(nodes[i], { scale: 0, opacity: 0, duration: 0.4, ease: "back.out(2.2)", transformOrigin: "center" }, at + 0.2 + i * stagger);
  }
  return mount;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeTimelineRail };
