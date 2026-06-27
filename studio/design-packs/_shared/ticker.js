/* ============================================================
   Design Pack: dark-truth-social — ticker.js
   The continuous news ticker through-line, lifted from
   reference/dark-truth-social/index.html. A thin mono band scrolls
   leftward across every scene for the whole runtime — the through-line
   that gives the news-conveyor feel and binds the scenes into one
   broadcast. Authored on the REAL timeline (spans the full runtime).

   Parameterized for reuse by other packs:
     - labels      -> the per-scene label strings
     - prefix      -> the repeated lead-in text (default field-report style)
     - textColor   -> the track text color (default --charcoal)
     - accentColor -> the <b> accent color  (default --spray)
     - duration    -> full-runtime scroll duration in seconds
   The "2x unit for a seamless leftward loop" + xPercent:-50 scroll math
   is unchanged from the reference.

   Deterministic: no Math.random / Date.now / fetch.

   Usage:
     makeTicker({
       tl: tlReal,
       track: document.querySelector(".ticker-track"),
       labels: ["HOOK","TITLE","SCALE", ... ],
       duration: 85,
     });
   ============================================================ */
function makeTicker(opts) {
  const tl = opts.tl;
  const track = opts.track || document.querySelector(opts.trackSel || ".ticker-track");
  if (!track) return null;

  const labels = opts.labels || ["HOOK", "TITLE", "SCALE", "MACHINE", "ENGINEERS", "COST", "WILLPOWER", "TAKEBACK", "OUTRO"];
  const prefix = opts.prefix || "AR/17—20 <b>// LIVE //</b> FIELD REPORT <b>//</b> ";
  const duration = typeof opts.duration === "number" ? opts.duration : 85;
  const textColor = opts.textColor;
  const accentColor = opts.accentColor;

  if (textColor) track.style.color = textColor;

  const unit = labels.map((l) => '<span class="tk">' + prefix + l + "</span>").join("");
  track.innerHTML = unit + unit; // 2x for a seamless leftward loop

  // tune the <b> accent color if the pack overrides it
  if (accentColor) {
    track.querySelectorAll(".tk b").forEach((b) => { b.style.color = accentColor; });
  }

  // scroll one full copy width across the whole runtime (continuous, no freeze)
  tl.to(track, { xPercent: -50, duration: duration, ease: "none" }, 0);
  return track;
}

if (typeof module !== "undefined" && module.exports) module.exports = { makeTicker };
