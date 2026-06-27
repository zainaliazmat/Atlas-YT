/* ============================================================
   Design Pack: dark-truth-social — transitions.js
   The news-intro transition library, lifted VERBATIM from
   reference/dark-truth-social/index.html (txPush / txTile / txWhip /
   txCut + greenSwipe). Authored on the REAL timeline with real
   new-timeline times so every boundary OVERLAPS the seam — no static
   frame ever sits between scenes. Every overlay fade is followed by a
   hard `set(...0)` "kill" (frame-seek-safe).

   Parameterized for reuse by other packs:
     - sceneSel(b)      -> the selector for scene b   (default "#s"+b)
     - boundaryTime(b)  -> the seam time for boundary b (e.g. NS[b])
     - swipeColor       -> the swipe bar color         (default --spray green)
   The tween math is unchanged from the reference.

   Deterministic: no Math.random / Date.now / fetch.

   Usage:
     const T = makeTransitions({
       tl: tlReal,                 // the real (paused) GSAP timeline
       swipe, flash, paper,        // the .tx-swipe / .tx-flash / .tx-paper nodes
       boundaryTime: (b) => NS[b],
       // sceneSel: (b) => "#s" + b,        // optional override
       // swipeColor: "#2e5e1f",            // optional override
     });
     T.txWhip(1); T.txPush(2); T.txCut(3, false); ...
   ============================================================ */
function makeTransitions(opts) {
  const tl = opts.tl;
  const swipe = opts.swipe;
  const flash = opts.flash;
  const paper = opts.paper;
  const sceneSel = opts.sceneSel || ((b) => "#s" + b);
  const boundaryTime = opts.boundaryTime;
  const swipeColor = opts.swipeColor || "var(--spray)";

  // Recolor the swipe bar so the green seam-sweep is pack-tunable.
  if (swipe && swipeColor) swipe.style.background = swipeColor;

  const greenSwipe = (t, dur) => {
    tl.fromTo(swipe, { xPercent: -170, opacity: 1 }, { xPercent: 270, duration: dur, ease: "power2.inOut", immediateRender: false }, t - 0.02);
    tl.set(swipe, { opacity: 0 }, t + dur - 0.02); // hard kill
  };
  // T-PUSH: outgoing slides left off-frame, incoming slides in from the right,
  // green spray swipe bar covers the seam.
  const txPush = (b) => {
    const t = boundaryTime(b);
    tl.fromTo(sceneSel(b), { xPercent: 0 }, { xPercent: -106, duration: 0.34, ease: "power3.in" }, t);
    tl.fromTo(sceneSel(b + 1), { xPercent: 106 }, { xPercent: 0, duration: 0.34, ease: "power3.out" }, t);
    greenSwipe(t, 0.4);
  };
  // T-TILE: outgoing lifts/fades out, incoming tiles up into frame; swipe accent.
  const txTile = (b) => {
    const t = boundaryTime(b);
    tl.fromTo(sceneSel(b), { yPercent: 0, opacity: 1 }, { yPercent: -10, opacity: 0, duration: 0.28, ease: "power3.in" }, t);
    tl.fromTo(sceneSel(b + 1), { yPercent: 12, opacity: 0 }, { yPercent: 0, opacity: 1, duration: 0.34, ease: "power3.out" }, t);
    greenSwipe(t, 0.4);
  };
  // T-WHIP: fast blurred whip-push into a white flash (installed flash overlay).
  const txWhip = (b) => {
    const t = boundaryTime(b);
    tl.fromTo(sceneSel(b), { xPercent: 0 }, { xPercent: -34, duration: 0.18, ease: "power3.in" }, t);
    tl.fromTo(sceneSel(b + 1), { xPercent: 44 }, { xPercent: 0, duration: 0.26, ease: "power3.out" }, t + 0.05);
    tl.to(flash, { opacity: 1, duration: 0.12, ease: "power2.in", immediateRender: false }, t - 0.02);
    tl.to(flash, { opacity: 0, duration: 0.22, ease: "power2.out" }, t + 0.12);
    tl.set(flash, { opacity: 0 }, t + 0.35); // hard kill
  };
  // T-CUT: hard cut on the beat, masked by a single-frame paper flash; optional
  // quick RGB-split glitch shudder on the incoming scene.
  const txCut = (b, glitch) => {
    const t = boundaryTime(b);
    tl.to(paper, { opacity: 0.92, duration: 0.05, ease: "steps(1)", immediateRender: false }, t - 0.04);
    tl.to(paper, { opacity: 0, duration: 0.13, ease: "power2.out" }, t + 0.02);
    tl.set(paper, { opacity: 0 }, t + 0.16); // hard kill
    if (glitch) {
      tl.fromTo(
        sceneSel(b + 1),
        { x: 9 },
        { keyframes: [{ x: -7, duration: 0.04 }, { x: 5, duration: 0.04 }, { x: -3, duration: 0.04 }, { x: 0, duration: 0.05 }], ease: "steps(1)", immediateRender: false },
        t,
      );
    }
  };

  return { greenSwipe, txPush, txTile, txWhip, txCut };
}

if (typeof module !== "undefined" && module.exports) module.exports = { makeTransitions };
