/* motion-library beat: highlighter-swipe
   makeHighlighterSwipe({ tl, mount, at, color, width, height, top, dur })
   Appends a spray marker bar behind text and sweeps it on (scaleX 0->1). */
function makeHighlighterSwipe(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var dur = typeof opts.dur === "number" ? opts.dur : 0.5;
  var bar = document.createElement("div");
  bar.style.cssText =
    "position:absolute;left:-6px;top:" + (opts.top || "48%") + ";width:" + (opts.width || 260) + "px;height:" +
    (opts.height || 22) + "px;background:" + color + ";opacity:0.55;border-radius:4px 7px 5px 6px;" +
    "transform:scaleX(0);transform-origin:left center;pointer-events:none;z-index:0";
  mount.appendChild(bar);
  tl.fromTo(bar, { scaleX: 0 }, { scaleX: 1, duration: dur, ease: "power2.inOut" }, at);
  return bar;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeHighlighterSwipe };
