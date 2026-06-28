/* motion-library beat: outline-self-draw
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
