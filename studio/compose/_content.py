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


from studio.gate.parse import is_attributed_quote   # reuse the gate's quote detector (one source)


def _split_quote(text: str) -> tuple[str, str]:
    """('"quote body"', 'Attribution') from a `"..." — Name` string. Best-effort."""
    m = re.split(r"\s*[—–-]\s*", text.strip(), maxsplit=1)
    body = m[0].strip()
    who = m[1].strip() if len(m) > 1 else ""
    return body, who


def render_claims(scene: dict) -> str:
    """Render each scripted claim as a visible on-screen card so nothing is dropped.
    Attributed quotes become quote cards with a byline; other claims keep their text."""
    claims = scene.get("claims") or []
    if not claims:
        return ""
    cards = []
    for c in claims:
        text = (c.get("text") if isinstance(c, dict) else c) or ""
        if not text.strip():
            continue
        if is_attributed_quote(text):
            body, who = _split_quote(text)
            cards.append(
                f'<div class="claim-card quote-card anim">'
                f'<div class="quote-body">{html.escape(body)}</div>'
                f'<div class="byline mono">{html.escape(who)}</div></div>')
        else:
            cards.append(
                f'<div class="claim-card anim"><span class="claim mono">{html.escape(text)}</span></div>')
    if not cards:
        return ""
    return '<div class="claims">' + "".join(cards) + "</div>"
