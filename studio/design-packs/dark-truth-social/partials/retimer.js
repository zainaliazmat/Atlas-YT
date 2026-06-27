/* ============================================================
   Design Pack: dark-truth-social — retimer.js
   The VO-lock RE-TIMER proxy, lifted from
   reference/dark-truth-social/index.html (GOLDEN_REFERENCE.md §2).
   Every authored tween is written against a clean nominal "old grid"
   (OS/OD). This proxy remaps each tween's POSITION and scales its
   DURATION/STAGGER/KEYFRAMES into the real VO-driven window (NS/ND),
   so all per-scene choreography auto-fits with zero dead air. When the
   VO changes, regenerate NS/ND — no tween is re-timed by hand.

   Exported as a factory:
     makeRetimer(oldStarts, oldDurs, newStarts, newDurs[, timeline])
       -> the `tl` proxy: { to, from, fromTo, set, real }

   `tl.to/from/fromTo/set` are the remapping methods ALL authored
   choreography calls. `tl.real` is the underlying real (paused) GSAP
   timeline — author transitions / ticker / the global texture motion
   on `tl.real` directly (real new-timeline times, NOT remapped), and
   register `tl.real` on window.__timelines. If no `timeline` arg is
   passed, a fresh paused timeline is created.

   Deterministic: no Math.random / Date.now / fetch.
   ============================================================ */
function makeRetimer(oldStarts, oldDurs, newStarts, newDurs, timeline) {
  const OS = oldStarts; // old scene starts
  const OD = oldDurs;   // old scene durations
  const NS = newStarts; // new (VO-driven) starts
  const ND = newDurs;   // new durations
  const tlReal = timeline || gsap.timeline({ paused: true });

  const SCALE = ND.map((d, i) => d / OD[i]);
  const sceneOf = (t) => {
    for (let n = OS.length - 1; n >= 0; n--) if (t >= OS[n]) return n;
    return 0;
  };
  const RT = (t) => {
    const n = sceneOf(t);
    return +(NS[n] + (t - OS[n]) * SCALE[n]).toFixed(4);
  };
  const scaleVars = (v, n) => {
    if (!v || typeof v !== "object" || v.nodeType) return;
    if (typeof v.duration === "number") v.duration *= SCALE[n];
    if (typeof v.stagger === "number") v.stagger *= SCALE[n];
    if (Array.isArray(v.keyframes)) v.keyframes.forEach((k) => { if (k && typeof k.duration === "number") k.duration *= SCALE[n]; });
  };
  const wrap = (method) => (...args) => {
    const pos = args[args.length - 1];
    if (typeof pos === "number") {
      const n = sceneOf(pos);
      for (let i = 0; i < args.length - 1; i++) scaleVars(args[i], n);
      args[args.length - 1] = RT(pos);
    }
    return tlReal[method](...args);
  };
  // `tl` is the remapping proxy used by ALL the authored choreography.
  const tl = { to: wrap("to"), from: wrap("from"), fromTo: wrap("fromTo"), set: wrap("set"), real: tlReal };
  return tl;
}

if (typeof module !== "undefined" && module.exports) module.exports = { makeRetimer };
