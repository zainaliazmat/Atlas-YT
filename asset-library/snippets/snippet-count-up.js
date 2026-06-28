/* studio.library procedural snippet: count-up
   Deterministic SVG+GSAP. Call makeCountUp({ tl, mount, at, color, size }).
   color is a runtime opt -> one cached snippet serves every pack color. */
function makeCountUp(opts) {
  opts = opts || {};
  var tl = opts.tl;
  if (!tl) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var size = opts.size || 64;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var NS = "http://www.w3.org/2000/svg";
  var target = typeof opts.target === "number" ? opts.target : 100;
  var dec = typeof opts.dec === "number" ? opts.dec : 0;
  var suffix = opts.suffix || "";
  var dur = typeof opts.duration === "number" ? opts.duration : 1.5;
  var el = document.createElement("span");
  el.style.color = color;
  var fmt = function (v) { return dec > 0 ? v.toFixed(dec) : String(Math.round(v)); };
  el.textContent = fmt(0) + suffix;
  mount.appendChild(el);
  var o = { v: 0 };
  tl.to(o, { v: target, duration: dur, ease: "power2.out", snap: { v: dec > 0 ? Math.pow(10, -dec) : 1 },
    onUpdate: function () { el.textContent = fmt(o.v) + suffix; } }, at);
  tl.fromTo(el, { scale: 1 }, { scale: 1.16, duration: 0.1, ease: "power2.out", transformOrigin: "50% 60%" }, at + dur);
  tl.to(el, { scale: 1, duration: 0.28, ease: "back.out(2.2)" }, at + dur + 0.1);
  return el;
}

if (typeof module !== "undefined" && module.exports) module.exports = { makeCountUp };
