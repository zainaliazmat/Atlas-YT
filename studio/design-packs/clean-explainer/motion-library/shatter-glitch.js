/* motion-library beat: shatter-glitch
   makeShatterGlitch({ tl, mount, at, color, shards, dur })
   A focus bar splits into `shards` pieces that drift apart by index-derived offsets, while a
   stepped red/cyan RGB-split ghost flickers on the mount (tl.set steps — seek-safe).
   Deterministic (no clock/RNG). Returns mount. */
function makeShatterGlitch(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var nshards = typeof opts.shards === "number" ? opts.shards : 8;
  var dur = typeof opts.dur === "number" ? opts.dur : 1.0;
  var bar = document.createElement("div");
  bar.className = "shatter-bar";
  bar.style.cssText = "position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);display:flex;gap:2px";
  var shards = [];
  for (var i = 0; i < nshards; i++) {
    var s = document.createElement("div");
    s.style.cssText = "width:26px;height:90px;background:" + color + ";will-change:transform,opacity";
    bar.appendChild(s); shards.push(s);
  }
  mount.appendChild(bar);
  tl.from(bar, { scaleY: 0, opacity: 0, duration: 0.4, ease: "power2.out", transformOrigin: "50% 50%" }, at);
  var shatterAt = at + 0.6;
  for (var i = 0; i < nshards; i++) {
    var dx = ((i * 37) % 120) - 60;
    var dy = ((i * 53) % 140) - 70;
    var rot = ((i * 41) % 90) - 45;
    tl.to(shards[i], { x: dx, y: dy, rotation: rot, opacity: 0, duration: dur, ease: "power2.in" }, shatterAt + (i % 4) * 0.03);
  }
  var ghost = document.createElement("div");
  ghost.className = "rgb-ghost";
  ghost.style.cssText = "position:absolute;inset:0;mix-blend-mode:screen;pointer-events:none";
  mount.appendChild(ghost);
  var flick = [{ x: -6, o: 0.5 }, { x: 5, o: 0.35 }, { x: -3, o: 0.6 }, { x: 0, o: 0 }];
  for (var k = 0; k < flick.length; k++) {
    tl.set(ghost, { x: flick[k].x, opacity: flick[k].o,
      backgroundColor: (k % 2) ? "rgba(255,0,0,0.25)" : "rgba(0,255,255,0.25)" }, shatterAt + k * 0.06);
  }
  return mount;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeShatterGlitch };
