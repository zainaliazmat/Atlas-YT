/* motion-library beat: map-draw
   makeMapDraw({ tl, mount, at, color, d, viewBox, width, height, strokeWidth, pinX, pinY, dur })
   Appends an inline SVG route path that self-draws (strokeDashoffset 1->0), then pops a
   destination pin (scale in, back.out). Deterministic (no clock/RNG). Returns the svg node. */
function makeMapDraw(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var dur = typeof opts.dur === "number" ? opts.dur : 1.6;
  var NS = "http://www.w3.org/2000/svg";
  var svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", opts.viewBox || "0 0 400 300");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.style.cssText = "position:absolute;left:0;top:0;width:" + (opts.width || 400) + "px;height:" + (opts.height || 300) + "px;overflow:visible;pointer-events:none";
  var route = document.createElementNS(NS, "path");
  route.setAttribute("class", "map-route");
  route.setAttribute("d", opts.d || "M30 250 C 120 200, 100 90, 220 110 S 360 60, 370 40");
  route.setAttribute("fill", "none");
  route.setAttribute("stroke", color);
  route.setAttribute("stroke-width", String(opts.strokeWidth || 4));
  route.setAttribute("stroke-linecap", "round");
  route.setAttribute("pathLength", "1");
  svg.appendChild(route);
  var pin = document.createElementNS(NS, "circle");
  pin.setAttribute("cx", String(opts.pinX || 370));
  pin.setAttribute("cy", String(opts.pinY || 40));
  pin.setAttribute("r", "9");
  pin.setAttribute("fill", color);
  svg.appendChild(pin);
  mount.appendChild(svg);
  tl.fromTo(route, { strokeDashoffset: 1, strokeDasharray: 1 }, { strokeDashoffset: 0, duration: dur, ease: "power1.inOut" }, at);
  tl.from(pin, { scale: 0, opacity: 0, duration: 0.4, ease: "back.out(2.6)", transformOrigin: "center" }, at + dur * 0.9);
  return svg;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeMapDraw };
