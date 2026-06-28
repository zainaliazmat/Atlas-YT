/* motion-library beat: device-loop
   makeDeviceLoop({ tl, mount, at, color, rows, scrollDur, flicks })
   Builds a phone frame in mount with an infinite-scrolling feed, a fake cursor doing `flicks`
   periodic flick gestures (index-timed), and a slot-reel that rolls. Deterministic — all timing
   from element-index arithmetic; no clock or RNG. Returns the phone node. */
function makeDeviceLoop(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var rows = typeof opts.rows === "number" ? opts.rows : 12;
  var scrollDur = typeof opts.scrollDur === "number" ? opts.scrollDur : 8;
  var flicks = typeof opts.flicks === "number" ? opts.flicks : 5;
  var phone = document.createElement("div");
  phone.className = "device-frame";
  phone.style.cssText = "position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:300px;height:560px;border:3px solid " + color + ";border-radius:32px;overflow:hidden;background:rgba(0,0,0,0.04)";
  var feed = document.createElement("div");
  feed.className = "device-feed";
  feed.style.cssText = "position:absolute;left:0;top:0;width:100%;will-change:transform";
  var rowH = 84;
  for (var i = 0; i < rows; i++) {
    var r = document.createElement("div");
    r.style.cssText = "height:" + (rowH - 12) + "px;margin:6px 10px;border-radius:8px;background:rgba(0,0,0,0.10);opacity:" + (0.5 + (i % 5) * 0.1);
    feed.appendChild(r);
  }
  phone.appendChild(feed);
  mount.appendChild(phone);
  var dist = rows * rowH - 560 + 100;
  tl.fromTo(feed, { y: 0 }, { y: -dist, duration: scrollDur, ease: "none" }, at);
  var cur = document.createElement("div");
  cur.className = "device-cursor";
  cur.style.cssText = "position:absolute;left:50%;top:60%;width:18px;height:18px;border-radius:50%;background:" + color + ";opacity:0.8;will-change:transform";
  phone.appendChild(cur);
  for (var k = 0; k < flicks; k++) {
    var ft = at + 0.5 + k * (scrollDur / (flicks + 1));
    tl.to(cur, { y: -40, duration: 0.18, ease: "power2.in" }, ft);
    tl.to(cur, { y: 0, duration: 0.3, ease: "power1.out" }, ft + 0.18);
  }
  var reel = document.createElement("div");
  reel.className = "slot-reel";
  reel.style.cssText = "position:absolute;left:50%;bottom:18px;transform:translateX(-50%);width:60px;height:40px;border:2px solid " + color + ";border-radius:6px;overflow:hidden";
  var strip = document.createElement("div");
  strip.style.cssText = "will-change:transform";
  for (var j = 0; j < 8; j++) {
    var cell = document.createElement("div");
    cell.style.cssText = "height:40px;display:flex;align-items:center;justify-content:center;font:700 22px monospace;color:" + color;
    cell.textContent = String(j % 10);
    strip.appendChild(cell);
  }
  reel.appendChild(strip); phone.appendChild(reel);
  tl.fromTo(strip, { y: 0 }, { y: -280, duration: 1.4, ease: "back.out(1.05)" }, at + 0.4);
  return phone;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeDeviceLoop };
