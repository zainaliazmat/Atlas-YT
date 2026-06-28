/* motion-library beat: calendar-crumble
   makeCalendarCrumble({ tl, mount, at, color, cells, cols, fillStagger, fillDur, crumbleGap, crumbleDur })
   Builds a `cols`-wide grid of `cells` cells in mount (class 'calendar-grid'), fills each with
   `color` cell-by-cell (staggered), then crumbles: each cell drifts by index-derived offsets,
   rotates, falls and fades. Deterministic — scatter from element-index arithmetic only, no
   clock or RNG. Returns the grid node. */
function makeCalendarCrumble(opts) {
  opts = opts || {};
  var tl = opts.tl; if (!tl) return null;
  var mount = typeof opts.mount === "string" ? document.querySelector(opts.mount) : opts.mount;
  if (!mount) return null;
  var at = typeof opts.at === "number" ? opts.at : 0;
  var color = opts.color || "currentColor";
  var n = typeof opts.cells === "number" ? opts.cells : 36;
  var cols = typeof opts.cols === "number" ? opts.cols : 6;
  var fillStagger = typeof opts.fillStagger === "number" ? opts.fillStagger : 0.05;
  var fillDur = typeof opts.fillDur === "number" ? opts.fillDur : 0.22;
  var crumbleGap = typeof opts.crumbleGap === "number" ? opts.crumbleGap : 1.2;
  var crumbleDur = typeof opts.crumbleDur === "number" ? opts.crumbleDur : 1.1;
  var grid = document.createElement("div");
  grid.className = "calendar-grid";
  grid.style.cssText = "display:grid;grid-template-columns:repeat(" + cols + ",1fr);gap:6px;width:100%;height:100%";
  var cells = [];
  for (var i = 0; i < n; i++) {
    var c = document.createElement("div");
    c.className = "cal-cell";
    c.style.cssText = "background:rgba(0,0,0,0.08);border-radius:3px;aspect-ratio:1/1;will-change:transform,opacity";
    grid.appendChild(c); cells.push(c);
  }
  mount.appendChild(grid);
  var fillEnd = at;
  for (var i = 0; i < n; i++) {
    var ft = at + i * fillStagger;
    tl.to(cells[i], { backgroundColor: color, duration: fillDur, ease: "power1.out" }, ft);
    fillEnd = ft + fillDur;
  }
  var crumbleAt = fillEnd + crumbleGap;
  for (var i = 0; i < n; i++) {
    var dx = ((i * 53) % 100) - 50;
    var dy = ((i * 31) % 100);
    var rot = ((i * 47) % 160) - 80;
    tl.to(cells[i], { x: dx, y: dy + 120, rotation: rot, opacity: 0, duration: crumbleDur, ease: "power2.in" }, crumbleAt + (i % 6) * 0.02);
  }
  return grid;
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeCalendarCrumble };
