"""studio.compose._content — the content blocks EVERY scene must render so nothing the
scriptwriter put on screen is silently dropped (the gate's content_fidelity dimension).
Pure HTML-string builders; no GSAP (motion is layered by the archetype). Deterministic."""
from __future__ import annotations

import html
import re


def render_on_screen_text(text: str) -> str:
    """Stacked `.lead` block preserving every line. Lines split on `/` or newline; the last
    word of the LAST line is emphasized. Empty → a non-breaking space so the slot still lays
    out."""
    raw = (text or "").strip()
    if not raw:
        return '<div class="lead"><span class="lead-line">&nbsp;</span></div>'
    lines = [seg.strip() for seg in re.split(r"\s*/\s*|\n", raw) if seg.strip()]
    out = []
    for i, line in enumerate(lines):
        words = [html.escape(w) for w in line.split()]
        if i == len(lines) - 1 and words:
            words[-1] = f'<span class="em">{words[-1]}</span>'
        out.append(f'<span class="lead-line">{" ".join(words)}</span>')
    return '<div class="lead">' + "".join(out) + "</div>"
