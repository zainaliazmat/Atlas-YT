"""TDD tests for studio/compose/archetypes/title_card.py — Task C8.

Tests:
  1. registration + parity: 'title-card' in REGISTRY; token_for == 'orbit';
     'orbit' in _BEAT_TOKENS names.
  2. determinism: builder html+beats_js — no banned primitives (strip /* */ comments first).
  3. keys + token 'orbit'.
  4. content: html contains 'title-card', 'portrait-mount', 'orbit-mount'.
  5. beats calls + anchor: beats_js contains BOTH 'makeOutlineDraw(' and 'makeOrbitCluster(';
     ctx={"at":18.3} -> 18.3 in beats_js (the outline-draw anchor).
  6. signature: scene_signature(html, beats_js, sid) == 'orbit' (not 'plain').
  7. parity regression over all REGISTRY archetypes.
"""
from __future__ import annotations

import re


# --- helpers -----------------------------------------------------------------

def _make_scene():
    return {
        "scene_no": 1,
        "on_screen_text": "THE AGE OF AI AGENTS",
        "narration": "AI agents are reshaping the landscape.",
        "duration_est_sec": 8,
        "claims": [],
    }


# === 1. Registration + token parity ==========================================

def test_title_card_is_registered():
    """title_card.py must call register() so 'title-card' appears in REGISTRY."""
    import studio.compose.archetypes.title_card  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    assert "title-card" in A.REGISTRY, (
        "'title-card' not found in archetypes.REGISTRY"
    )


def test_title_card_token_for_returns_orbit():
    """token_for('title-card') must return 'orbit'."""
    from studio.compose import archetypes as A
    tok = A.token_for("title-card")
    assert tok == "orbit", (
        f"token_for('title-card') returned {tok!r}, expected 'orbit'"
    )


def test_orbit_token_in_beat_tokens():
    """The 'orbit' token must be present in gate.parse._BEAT_TOKENS (parity invariant)."""
    from studio.gate import parse as P
    token_names = {name for name, _pat in P._BEAT_TOKENS}
    assert "orbit" in token_names, (
        "'orbit' not found in gate.parse._BEAT_TOKENS — parity broken"
    )


# === 2. Determinism ==========================================================

def test_build_output_has_no_banned_primitives():
    """html + beats_js must not contain Math.random/Date.now/new Date/fetch/XMLHttpRequest."""
    import studio.compose.archetypes.title_card as tc
    scene = _make_scene()
    ctx = {"sid": "s1", "spray": "#2e5e1f", "ink": "#1f1f1e"}
    result = tc.build(scene, ctx)
    combined = result["html"] + result["beats_js"]
    # Strip block comments before scanning
    combined = re.sub(r"/\*.*?\*/", "", combined, flags=re.DOTALL)
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bfetch\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(combined), (
        f"Banned non-deterministic primitive found in build() output: "
        f"{banned.findall(combined)}"
    )


# === 3. Required keys + token ================================================

def test_build_returns_required_keys():
    """build() must return dict with html, beats_js, token."""
    import studio.compose.archetypes.title_card as tc
    result = tc.build(_make_scene(), {"sid": "s1"})
    assert "html" in result, "Missing 'html' key in build() result"
    assert "beats_js" in result, "Missing 'beats_js' key in build() result"
    assert "token" in result, "Missing 'token' key in build() result"


def test_build_token_is_orbit():
    """build() must return token == 'orbit'."""
    import studio.compose.archetypes.title_card as tc
    result = tc.build(_make_scene(), {"sid": "s1"})
    assert result["token"] == "orbit", (
        f"Expected token 'orbit', got {result['token']!r}"
    )


# === 4. Content ==============================================================

def test_build_html_contains_title_card_class():
    """html must contain the 'title-card' class."""
    import studio.compose.archetypes.title_card as tc
    result = tc.build(_make_scene(), {"sid": "s1"})
    assert "title-card" in result["html"], (
        "'title-card' class not found in html"
    )


def test_build_html_contains_portrait_mount():
    """html must contain 'portrait-mount' (mount point for makeOutlineDraw)."""
    import studio.compose.archetypes.title_card as tc
    result = tc.build(_make_scene(), {"sid": "s1"})
    assert "portrait-mount" in result["html"], (
        "'portrait-mount' not found in html — it is the mount point for makeOutlineDraw"
    )


def test_build_html_contains_orbit_mount():
    """html must contain 'orbit-mount' (mount point for makeOrbitCluster)."""
    import studio.compose.archetypes.title_card as tc
    result = tc.build(_make_scene(), {"sid": "s1"})
    assert "orbit-mount" in result["html"], (
        "'orbit-mount' not found in html — it is the mount point for makeOrbitCluster"
    )


def test_build_html_contains_orbit_fx():
    """html must contain 'orbit-fx' for static signature match by scene_signature."""
    import studio.compose.archetypes.title_card as tc
    result = tc.build(_make_scene(), {"sid": "s1"})
    assert "orbit-fx" in result["html"], (
        "'orbit-fx' not found in html — needed for static signature match"
    )


# === 5. Beats calls + anchor =================================================

def test_beats_js_calls_make_outline_draw():
    """beats_js must invoke makeOutlineDraw(."""
    import studio.compose.archetypes.title_card as tc
    result = tc.build(_make_scene(), {"sid": "s1"})
    assert "makeOutlineDraw(" in result["beats_js"], (
        "beats_js does not call makeOutlineDraw"
    )


def test_beats_js_calls_make_orbit_cluster():
    """beats_js must invoke makeOrbitCluster(."""
    import studio.compose.archetypes.title_card as tc
    result = tc.build(_make_scene(), {"sid": "s1"})
    assert "makeOrbitCluster(" in result["beats_js"], (
        "beats_js does not call makeOrbitCluster"
    )


def test_beats_js_anchored_at_ctx_at():
    """beats_js must embed ctx['at'] as the outline-draw anchor."""
    import studio.compose.archetypes.title_card as tc
    scene = _make_scene()
    ctx = {"sid": "s1", "spray": "#2e5e1f", "at": 18.3}
    result = tc.build(scene, ctx)
    assert "18.3" in result["beats_js"], (
        f"Expected ctx['at']=18.3 in beats_js but not found.\n"
        f"beats_js:\n{result['beats_js']}"
    )


def test_beats_js_default_anchor_is_0_6():
    """When ctx has no 'at', the default fallback is 0.6."""
    import studio.compose.archetypes.title_card as tc
    result = tc.build(_make_scene(), {"sid": "s1"})
    assert "0.6" in result["beats_js"], (
        f"Expected default anchor 0.6 in beats_js when ctx has no 'at'.\n{result['beats_js']}"
    )


def test_beats_js_sid_scoped():
    """beats_js must scope the mount selector to the scene sid."""
    import studio.compose.archetypes.title_card as tc
    ctx = {"sid": "s11", "spray": "#2e5e1f", "at": 5.0}
    result = tc.build(_make_scene(), ctx)
    assert "s11" in result["beats_js"], (
        "sid 's11' not found in beats_js — mount selector must include the sid"
    )


# === 6. scene_signature ======================================================

def test_scene_signature_returns_orbit():
    """scene_signature must return 'orbit' for a title-card scene output."""
    import studio.compose.archetypes.title_card as tc
    from studio.gate.parse import scene_signature

    scene = _make_scene()
    sid = "s1"
    ctx = {"sid": sid, "spray": "#2e5e1f"}
    result = tc.build(scene, ctx)

    sig = scene_signature(result["html"], result["beats_js"], sid)
    assert sig == "orbit", (
        f"Expected scene_signature == 'orbit' but got {sig!r}. "
        f"Check that html contains 'orbit-fx'/'orbit-mount' "
        f"or beats_js calls 'makeOrbitCluster'."
    )


def test_scene_signature_not_plain():
    """Explicit guard: the signature must never fall back to 'plain'."""
    import studio.compose.archetypes.title_card as tc
    from studio.gate.parse import scene_signature

    result = tc.build(_make_scene(), {"sid": "s1"})
    sig = scene_signature(result["html"], result["beats_js"], "s1")
    assert sig != "plain", (
        "scene_signature fell back to 'plain' — the gate cannot distinguish this archetype"
    )


# === 7. Parity regression ====================================================

def test_parity_invariant_still_holds():
    """Every registered archetype's token must be in _BEAT_TOKENS (the parity invariant)."""
    import studio.compose.archetypes.title_card  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    from studio.gate import parse as P

    token_names = {name for name, _pat in P._BEAT_TOKENS}
    for arch in A.REGISTRY:
        tok = A.token_for(arch)
        assert tok in token_names, (
            f"Parity broken: archetype {arch!r} emits token {tok!r} "
            f"not present in _BEAT_TOKENS"
        )
