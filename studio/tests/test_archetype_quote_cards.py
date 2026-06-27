"""TDD tests for studio/compose/archetypes/quote_cards.py — Task C1.

Tests:
  (a) registered: 'quote-card' is in archetypes.REGISTRY and its token 'quote-cards'
      is present in gate.parse._BEAT_TOKENS.
  (b) determinism + content: build() produces html + beats_js with no banned
      primitives; html contains the quote body and attribution; beats_js triggers
      makeHighlighterSwipe.
  (c) signature: a scene built by this archetype yields
      scene_signature(html, beats_js, sid) == "quote-cards" (distinct, not "plain").
  (d) parity: the existing parity test remains green (imported directly here as a
      regression guard; the canonical run is test_archetype_token_parity.py).
"""
from __future__ import annotations

import importlib
import re


# --- helpers -----------------------------------------------------------------

def _make_scene(claims=None):
    return {
        "scene_no": 3,
        "on_screen_text": "QUOTE SCENE",
        "narration": "Here is what they said.",
        "duration_est_sec": 8,
        "claims": claims or [
            {
                "claim_id": "c1",
                "text": '"Sprinkling behavioral cocaine over your interface." — Aza Raskin',
                "source_ref": "F1",
            }
        ],
    }


# === (a) Registration + token parity =========================================

def test_quote_card_is_registered():
    """quote_cards.py must call register() so 'quote-card' appears in REGISTRY."""
    # importing the module triggers the register() side-effect
    import studio.compose.archetypes.quote_cards  # noqa: F401
    from studio.compose import archetypes as A
    assert "quote-card" in A.REGISTRY, "'quote-card' not found in archetypes.REGISTRY"


def test_quote_cards_token_in_beat_tokens():
    """The 'quote-cards' token must be present in gate.parse._BEAT_TOKENS so the gate
    can recognise a scene produced by this archetype (the parity invariant)."""
    from studio.gate import parse as P
    token_names = {name for name, _pat in P._BEAT_TOKENS}
    assert "quote-cards" in token_names, (
        "'quote-cards' not found in gate.parse._BEAT_TOKENS — parity broken"
    )


# === (b) Determinism + content ===============================================

def test_build_is_deterministic_no_banned_primitives():
    """beats_js and html must not use Math.random / Date.now / new Date / fetch /
    XMLHttpRequest — all of which would break render determinism."""
    import studio.compose.archetypes.quote_cards as qc
    scene = _make_scene()
    ctx = {"sid": "s3", "spray": "#2e5e1f", "ink": "#1f1f1e"}
    result = qc.build(scene, ctx)
    combined = result["html"] + result["beats_js"]
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bfetch\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(combined), (
        f"Banned non-deterministic primitive found in build() output: "
        f"{banned.findall(combined)}"
    )


def test_build_returns_required_keys():
    import studio.compose.archetypes.quote_cards as qc
    result = qc.build(_make_scene(), {"sid": "s3"})
    assert "html" in result and "beats_js" in result and "token" in result
    assert result["token"] == "quote-cards"


def test_build_html_contains_quote_body():
    import studio.compose.archetypes.quote_cards as qc
    scene = _make_scene()
    result = qc.build(scene, {"sid": "s3"})
    assert "behavioral cocaine" in result["html"].lower(), (
        "Quote body not found in built html"
    )


def test_build_html_contains_attribution():
    import studio.compose.archetypes.quote_cards as qc
    result = qc.build(_make_scene(), {"sid": "s3"})
    assert "Aza Raskin" in result["html"], "Attribution (byline) not found in built html"


def test_build_html_contains_quote_body_and_byline_classes():
    import studio.compose.archetypes.quote_cards as qc
    result = qc.build(_make_scene(), {"sid": "s3"})
    assert "quote-body" in result["html"], ".quote-body class missing"
    assert "byline" in result["html"], ".byline class missing"


def test_beats_js_calls_highlighter_swipe():
    """beats_js must invoke makeHighlighterSwipe (the signature beat for this archetype)."""
    import studio.compose.archetypes.quote_cards as qc
    result = qc.build(_make_scene(), {"sid": "s3"})
    assert "makeHighlighterSwipe" in result["beats_js"], (
        "beats_js does not call makeHighlighterSwipe"
    )


def test_beats_js_includes_parallax_entry():
    """beats_js must include a parallax y/opacity tween for each card."""
    import studio.compose.archetypes.quote_cards as qc
    result = qc.build(_make_scene(), {"sid": "s3"})
    # Either a tl.from/fromTo with y: or opacity: means parallax entry
    assert re.search(r"tl\.(from|fromTo|to)\b", result["beats_js"]), (
        "beats_js has no GSAP tween calls — parallax entry is missing"
    )


def test_build_multiple_claims():
    """build() must handle scenes with >1 attributed quote."""
    import studio.compose.archetypes.quote_cards as qc
    scene = _make_scene(claims=[
        {"claim_id": "c1",
         "text": '"Sprinkling behavioral cocaine." — Aza Raskin',
         "source_ref": "F1"},
        {"claim_id": "c2",
         "text": '"Pull-to-refresh is addictive." — Loren Brichter',
         "source_ref": "F2"},
    ])
    result = qc.build(scene, {"sid": "s3"})
    assert result["html"].count("quote-card") >= 2, (
        "Expected ≥2 quote-card elements for 2-claim scene"
    )


# === (c) scene_signature roundtrip ==========================================

def test_scene_signature_returns_quote_cards():
    """A scene built by this archetype must yield scene_signature == 'quote-cards',
    proving the gate sees a DISTINCT signature (not 'plain')."""
    import studio.compose.archetypes.quote_cards as qc
    from studio.gate.parse import scene_signature

    scene = _make_scene()
    sid = "s3"
    ctx = {"sid": sid, "spray": "#2e5e1f"}
    result = qc.build(scene, ctx)

    # scene_signature receives: inner block html, ALL the composition js, sid
    sig = scene_signature(result["html"], result["beats_js"], sid)
    assert sig == "quote-cards", (
        f"Expected scene_signature == 'quote-cards' but got {sig!r}. "
        f"Check that _BEAT_TOKENS pattern matches the built output."
    )


def test_scene_signature_not_plain():
    """Explicit guard: the signature must be 'quote-cards', never the fallback 'plain'."""
    import studio.compose.archetypes.quote_cards as qc
    from studio.gate.parse import scene_signature

    result = qc.build(_make_scene(), {"sid": "s5"})
    sig = scene_signature(result["html"], result["beats_js"], "s5")
    assert sig != "plain", (
        "scene_signature fell back to 'plain' — the gate cannot distinguish this archetype"
    )


# === (c2) non-quote "cards" class must NOT be mislabelled ===================

def test_non_quote_cards_class_not_mislabelled_as_quote_cards():
    """Regression guard for the greedy bare-`cards` terminal that was removed.

    A scene whose ONLY card-ish class is a non-quote class ending in 'cards'
    (e.g. 'stat-cards') must NOT be labelled 'quote-cards'.  Before the fix the
    pattern `class=["'][^"']*cards` would match any class ending in 'cards',
    causing false positives that corrupt the motion_variety distinctness metric.
    After the fix only 'quoteCards', 'makeHighlighterSwipe', or 'quote-card'
    class names trigger the 'quote-cards' token.
    """
    from studio.gate.parse import scene_signature

    # Scene with stat-cards class only — no quoteCards / makeHighlighterSwipe / quote-card
    inner_html = "<div class='stat-cards'><p>Some stats here</p></div>"
    choreo_js  = ""
    sid        = "s3"

    sig = scene_signature(inner_html, choreo_js, sid)
    assert sig != "quote-cards", (
        f"scene_signature returned 'quote-cards' for a stat-cards scene — "
        f"the greedy 'cards' pattern is still active (got {sig!r})"
    )


# === (d) parity regression guard ============================================

def test_parity_invariant_still_holds():
    """Regression: importing quote_cards must not break the parity invariant."""
    import studio.compose.archetypes.quote_cards  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    from studio.gate import parse as P

    token_names = {name for name, _pat in P._BEAT_TOKENS}
    for arch in A.REGISTRY:
        tok = A.token_for(arch)
        assert tok in token_names, (
            f"Parity broken: archetype {arch!r} emits token {tok!r} "
            f"not present in _BEAT_TOKENS"
        )
