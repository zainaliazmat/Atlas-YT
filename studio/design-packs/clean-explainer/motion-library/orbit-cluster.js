/* motion-library beat: orbit-cluster
   makeOrbitCluster({ tl, mount, at, color, items, radius, node, dur })
   Appends a ring of icon nodes orbiting a center dot; inner icons counter-rotate
   so they stay upright. items = array of inner-SVG markup strings. Deterministic
   (trig placement, no RNG). */
function makeOrbitCluster(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var items = opts.items || [];
  var R = typeof opts.radius === "number" ? opts.radius : 90;
  var node = typeof opts.node === "number" ? opts.node : 54;
  var dur = typeof opts.dur === "number" ? opts.dur : 26;

  var ring = document.createElement("div");
  ring.style.cssText = "position:absolute;inset:0;will-change:transform";
  var center = document.createElement("div");
  center.style.cssText = "position:absolute;left:50%;top:50%;width:24px;height:24px;margin:-12px 0 0 -12px;border-radius:50%;background:" + color + ";box-shadow:0 0 0 7px rgba(46,94,31,0.13)";
  mount.appendChild(center);
  mount.appendChild(ring);

  var n = items.length || 1;
  var inner = [];
  for (var i = 0; i < n; i++) {
    var a = (i / n) * Math.PI * 2 - Math.PI / 2;
    var el = document.createElement("div");
    el.style.cssText =
      "position:absolute;left:50%;top:50%;width:" + node + "px;height:" + node + "px;margin:" + (-node / 2) +
      "px 0 0 " + (-node / 2) + "px;border-radius:50%;background:var(--paper,#f2eed6);border:2px solid var(--ink,#1f1f1e);" +
      "display:flex;align-items:center;justify-content:center;" +
      "transform:translate(" + (R * Math.cos(a)).toFixed(2) + "px," + (R * Math.sin(a)).toFixed(2) + "px)";
    var ico = document.createElement("div");
    ico.style.cssText = "width:56%;height:56%;display:flex;align-items:center;justify-content:center;will-change:transform";
    ico.innerHTML = items[i] || "";
    el.appendChild(ico);
    ring.appendChild(el);
    inner.push(ico);
  }
  tl.from(mount, { opacity: 0, scale: 0.82, duration: 0.6, ease: "power2.out", transformOrigin: "50% 50%" }, at);
  tl.to(ring, { rotation: 360, duration: dur, ease: "none", transformOrigin: "50% 50%" }, at + 0.4);
  for (var j = 0; j < inner.length; j++) {
    tl.to(inner[j], { rotation: -360, duration: dur, ease: "none" }, at + 0.4);
  }
  return ring;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeOrbitCluster };
