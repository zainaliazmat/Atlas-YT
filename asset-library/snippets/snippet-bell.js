/* studio.library procedural snippet: bell
   Deterministic SVG+GSAP. Call makeBell({ tl, mount, at, color, size }).
   color is a runtime opt -> one cached snippet serves every pack color. */
function makeBell(opts) {
  opts = opts || {};
  var tl = opts.tl;
  if (!tl) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var size = opts.size || 64;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var NS = "http://www.w3.org/2000/svg";
  var svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size); svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", color);
  svg.setAttribute("stroke-width", "1.7");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.style.transformOrigin = "50% 14%";
  svg.innerHTML = '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>';
  mount.appendChild(svg);
  tl.fromTo(svg, { rotation: 0 }, {
    keyframes: [
      { rotation: 17, duration: 0.12 }, { rotation: -13, duration: 0.16 },
      { rotation: 9, duration: 0.14 }, { rotation: -6, duration: 0.13 },
      { rotation: 3, duration: 0.12 }, { rotation: 0, duration: 0.13 }
    ], ease: "sine.inOut"
  }, at);
  return svg;
}

if (typeof module !== "undefined" && module.exports) module.exports = { makeBell };
