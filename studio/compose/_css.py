"""Composition CSS — the shell/type/overlay/ticker styles the Composer emits.

base.css (from the pack) carries the palette, grain, metadata and captions. This
module adds the rest the reference kept in its main <style>: the scene shell,
the type-role classes (driven by tokens.type), the kinetic-typography word unit,
the transition overlays (.tx-*) and the news-ticker band — all tinted from the
pack's tokens so a different pack restyles them for free.

Everything here is static CSS. Time-based motion (grain drift, reg-tick flicker)
is driven by GSAP on the seekable timeline, so we DISABLE base.css's CSS
@keyframes for those (they would run on wall-clock and desync under frame-seek).
"""

from __future__ import annotations


def _font(tokens: dict, role: str, fallback: str) -> str:
    spec = tokens.get("type", {}).get(role)
    if spec and spec.get("font"):
        return f'"{spec["font"]}", {fallback}'
    return fallback


def composition_css(tokens: dict, width: int, height: int) -> str:
    hero = _font(tokens, "hero", "sans-serif")
    slab = _font(tokens, "slab", "serif")
    mono = _font(tokens, "mono", "monospace")
    body = _font(tokens, "body", "sans-serif")
    signature = _font(tokens, "signature", "cursive")

    return f"""
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{
        width: {width}px; height: {height}px; overflow: hidden;
        background: var(--paper, #f2eed6);
        font-family: {body}; color: var(--ink, #1f1f1e);
      }}
      #root {{ position: relative; }}

      /* Frame-seek safety: the seekable GSAP timeline drives grain + tick flicker,
         so the pack's wall-clock CSS @keyframes must not fight it. */
      .grain {{ animation: none !important; }}
      .reg-ticks {{ animation: none !important; }}

      /* ---- Scene shell ---- */
      .scene {{
        position: absolute; inset: 0;
        background: radial-gradient(ellipse at 50% 42%, var(--paper) 58%, var(--paper-shade) 100%);
        color: var(--ink);
      }}
      .scene::before {{
        content: ""; position: absolute; inset: 36px;
        border: 1px solid var(--paper-line); opacity: 0.45;
        pointer-events: none; z-index: 2;
      }}
      .scene-content {{
        position: relative; z-index: 1;
        display: flex; flex-direction: column; align-items: center; justify-content: center;
        width: 100%; height: 100%; padding: 120px 170px; gap: 34px;
        box-sizing: border-box; text-align: center;
      }}

      /* ---- Kinetic typography word unit ---- */
      .word {{ display: inline-block; will-change: clip-path, transform, opacity; }}

      /* ---- Type roles ---- */
      .lead {{
        font-family: {hero}; font-size: 92px; font-weight: 400; line-height: 1.07;
        max-width: 1500px; letter-spacing: 0.5px; color: var(--ink);
        filter: url(#spray-rough);
        text-shadow: 0 0 5px rgba(26,86,20,0.06), 0 1px 0 rgba(31,31,30,0.05);
      }}
      .lead .em {{ color: var(--spray); }}
      .lead .em, .lead .mark {{ margin: 0 0.1em; }}
      .slab {{ font-family: {slab}; font-weight: 900; filter: none; text-shadow: none; letter-spacing: -1px; }}
      .kicker {{
        font-family: {mono}; font-size: 24px; font-weight: 700; letter-spacing: 5px;
        color: var(--charcoal); text-transform: uppercase;
      }}
      .kicker .em {{ color: var(--spray); }}
      .label {{ font-family: {mono}; font-size: 22px; letter-spacing: 3px; color: var(--charcoal); }}
      .label.faint {{ color: var(--paper-line); }}
      .footnote {{ font-family: {mono}; font-size: 24px; color: var(--charcoal); max-width: 1100px; }}
      .stat-src {{ font-family: {mono}; font-size: 16px; letter-spacing: 1.5px; color: var(--paper-line); }}
      .signature {{ font-family: {signature}; font-size: 104px; line-height: 1; color: var(--ink); }}

      /* ---- Stat card (count-up host) ---- */
      .row {{ display: flex; gap: 28px; align-items: center; justify-content: center; flex-wrap: wrap; }}
      .stat {{
        width: 380px; height: 240px; display: flex; flex-direction: column;
        align-items: center; justify-content: center; gap: 10px;
        border: 1.5px solid var(--paper-line); border-top: 5px solid var(--spray);
        background: var(--paper-shade); font-family: {slab}; font-size: 56px; font-weight: 900;
        letter-spacing: -1px; color: var(--ink); font-variant-numeric: tabular-nums;
      }}
      .stat small {{ font-family: {mono}; font-size: 20px; font-weight: 400; letter-spacing: 3px; color: var(--charcoal); }}

      /* ---- icon chip ---- */
      .chip-row {{ display: flex; gap: 26px; align-items: center; justify-content: center; flex-wrap: wrap; }}
      .chip {{
        width: 118px; height: 118px; border: 2px solid var(--paper-line);
        background: var(--paper-shade); border-radius: 16px;
        display: flex; align-items: center; justify-content: center;
      }}
      .chip svg {{ width: 60px; height: 60px; }}
      .chip.brand svg {{ fill: var(--ink); }}
      .chip.ui svg {{ stroke: var(--spray); fill: none; }}

      /* ---- halftone portrait ---- */
      .portrait-stage {{ position: relative; width: 560px; height: 700px; }}
      .portrait-img {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }}

      /* ---- decorative overlay host (motion-library beats append here) ---- */
      .fx {{ position: absolute; inset: 0; pointer-events: none; z-index: 1; }}

      /* ============ news-intro transition overlays ============ */
      .tx-swipe {{
        position: absolute; top: -6%; bottom: -6%; left: 0; width: 44%;
        background: var(--spray); transform: translateX(-170%) skewX(-9deg);
        opacity: 0; z-index: 80; pointer-events: none; will-change: transform, opacity;
        box-shadow: 0 0 60px rgba(46,94,31,0.4);
      }}
      .tx-paper {{ position: absolute; inset: 0; background: var(--paper); opacity: 0; z-index: 81; pointer-events: none; }}
      .tx-flash {{ position: absolute; inset: 0; background: #ffffff; opacity: 0; z-index: 82; pointer-events: none; }}

      /* ============ continuous news ticker band ============ */
      .ticker-band {{
        position: absolute; top: 10px; left: 36px; right: 36px; height: 24px;
        overflow: hidden; z-index: 6; display: flex; align-items: center; pointer-events: none;
      }}
      .ticker-band::before {{ content: ""; position: absolute; inset: 0; background: var(--paper-line); opacity: 0.1; }}
      .ticker-band::after {{ content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 10px; background: var(--spray); opacity: 0.55; }}
      .ticker-track {{
        position: relative; display: inline-flex; white-space: nowrap; will-change: transform;
        font-family: {mono}; font-size: 13px; font-weight: 700; letter-spacing: 4px;
        color: var(--charcoal); opacity: 0.72; padding-left: 22px;
      }}
      .ticker-track .tk {{ padding: 0 26px; }}
      .ticker-track .tk b {{ color: var(--spray); font-weight: 700; }}

      /* ---- content blocks: multi-line lead text ---- */
      .lead-line {{ display: block; }}

      /* ---- content blocks: claims / attributed quote cards ---- */
      .claims {{ display: flex; flex-direction: column; gap: 18px; margin-top: 28px; }}
      .claim-card {{
        background: var(--paper-shade, #e4e0c8);
        border-left: 4px solid var(--spray, #2e5e1f);
        padding: 18px 22px; border-radius: 6px;
      }}
      .quote-card .quote-body {{ font-size: 34px; line-height: 1.25; color: var(--ink, #1f1f1e); }}
      .quote-card .byline {{ margin-top: 10px; color: var(--spray, #2e5e1f); font-size: 18px; }}
      .claim {{ color: var(--ink, #1f1f1e); font-size: 24px; }}
"""
