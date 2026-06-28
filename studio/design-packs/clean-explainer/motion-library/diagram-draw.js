/* motion-library beat: diagram-draw
   makeDiagramDraw({ tl, mount, at, color, stagger })
   Each `.diagram-edge` path in mount self-draws (strokeDashoffset 1->0) and each
   `.diagram-node` pops in (scale + back.out), staggered. Deterministic (index-derived; no
   clock/RNG). Returns mount. */
function makeDiagramDraw(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var stagger = typeof opts.stagger === "number" ? opts.stagger : 0.5;
  var edges = mount.querySelectorAll(".diagram-edge");
  for (var e = 0; e < edges.length; e++) {
    tl.fromTo(edges[e], { strokeDashoffset: 1, strokeDasharray: 1 }, { strokeDashoffset: 0, duration: 0.6, ease: "power1.inOut" }, at + e * stagger);
  }
  var nodes = mount.querySelectorAll(".diagram-node");
  for (var i = 0; i < nodes.length; i++) {
    tl.from(nodes[i], { scale: 0, opacity: 0, duration: 0.45, ease: "back.out(2)", transformOrigin: "center" }, at + i * stagger);
  }
  return mount;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeDiagramDraw };
