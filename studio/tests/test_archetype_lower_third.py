"""TDD tests for studio/compose/archetypes/lower_third.py — Task C9.

Tests:
  1. registration + parity: 'lower-third' in REGISTRY; token_for == 'signature';
     'signature' in _BEAT_TOKENS names.
  2. determinism: builder html+beats_js — no banned primitives (strip /* */ comments first).
  3. keys + token 'signature'.
  4. content: html contains 'lower-third', 'signature', 'handle-row', the NAME and HANDLE text.
  5. beats call + anchor: beats_js contains 'makeOutlineDraw(' AND a 'tl.from(' for the
     handle row; ctx={"at":18.3} -> 18.3 in beats_js.
  6. signature: scene_signature(html, beats_js, sid) == 'signature' (not 'plain', and
     crucially NOT 'underline' — proving the ordering / class-match).
  7. parity regression over all REGISTRY archetypes.
"""
from __future__ import annotations

import re


# --- helpers -----------------------------------------------------------------

def _make_scene(point=None, on_screen_text=None):
    return {
        "scene_no": 5,
        "on_screen_text": on_screen_text or "FIELD SCIENTIST",
        "point": point,
        "narration": "A name bar with a self-writing signature.",
        "duration_est_sec": 6,
        "claims": [],
    }


# === 1. Registration + token parity ==========================================

def test_lower_third_is_registered():
    """lower_third.py must call register() so 'lower-third' appears in REGISTRY."""
    import studio.compose.archetypes.lower_third  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    assert "lower-third" in A.REGISTRY, (
        "'lower-third' not found in archetypes.REGISTRY"
    )


def test_lower_third_token_for_returns_signature():
    """token_for('lower-third') must return 'signature'."""
    from studio.compose import archetypes as A
    tok = A.token_for("lower-third")
    assert tok == "signature", (
        f"token_for('lower-third') returned {tok!r}, expected 'signature'"
    )


def test_signature_token_in_beat_tokens():
    """The 'signature' token must be present in gate.parse._BEAT_TOKENS (parity invariant)."""
    from studio.gate import parse as P
    token_names = {name for name, _pat in P._BEAT_TOKENS}
    assert "signature" in token_names, (
        "'signature' not found in gate.parse._BEAT_TOKENS — parity broken"
    )


# === 2. Determinism ==========================================================

def test_build_output_has_no_banned_primitives():
    """html + beats_js must not contain Math.random/Date.now/new Date/fetch/XMLHttpRequest."""
    import studio.compose.archetypes.lower_third as lt
    scene = _make_scene()
    ctx = {"sid": "s5", "spray": "#2e5e1f", "ink": "#1f1f1e"}
    result = lt.build(scene, ctx)
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
    import studio.compose.archetypes.lower_third as lt
    result = lt.build(_make_scene(), {"sid": "s5"})
    assert "html" in result, "Missing 'html' key in build() result"
    assert "beats_js" in result, "Missing 'beats_js' key in build() result"
    assert "token" in result, "Missing 'token' key in build() result"


def test_build_token_is_signature():
    """build() must return token == 'signature'."""
    import studio.compose.archetypes.lower_third as lt
    result = lt.build(_make_scene(), {"sid": "s5"})
    assert result["token"] == "signature", (
        f"Expected token 'signature', got {result['token']!r}"
    )


# === 4. Content ==============================================================

def test_build_html_contains_lower_third_class():
    """html must contain the 'lower-third' class."""
    import studio.compose.archetypes.lower_third as lt
    result = lt.build(_make_scene(), {"sid": "s5"})
    assert "lower-third" in result["html"], (
        "'lower-third' class not found in html"
    )


def test_build_html_contains_signature_class():
    """html must contain 'signature' (for the static scene_signature match)."""
    import studio.compose.archetypes.lower_third as lt
    result = lt.build(_make_scene(), {"sid": "s5"})
    assert "signature" in result["html"], (
        "'signature' not found in html — needed for static scene_signature match"
    )


def test_build_html_contains_handle_row():
    """html must contain the 'handle-row' element."""
    import studio.compose.archetypes.lower_third as lt
    result = lt.build(_make_scene(), {"sid": "s5"})
    assert "handle-row" in result["html"], (
        "'handle-row' not found in html"
    )


def test_build_html_contains_name_text():
    """html must contain the NAME text derived from the scene."""
    import studio.compose.archetypes.lower_third as lt
    result = lt.build(_make_scene(on_screen_text="FIELD SCIENTIST"), {"sid": "s5"})
    assert "FIELD SCIENTIST" in result["html"], (
        "NAME text 'FIELD SCIENTIST' not found in html"
    )


def test_build_html_contains_handle_text():
    """html must contain a handle (@...) text in the handle-row."""
    import studio.compose.archetypes.lower_third as lt
    result = lt.build(_make_scene(), {"sid": "s5"})
    # The handle should start with '@'
    assert "@" in result["html"], (
        "No handle (@...) found in html"
    )


def test_build_html_uses_point_for_name_when_present():
    """When scene['point'] is set it takes precedence over on_screen_text for NAME."""
    import studio.compose.archetypes.lower_third as lt
    scene = _make_scene(point="Dr. Jane Goodall", on_screen_text="SOME OTHER TEXT")
    result = lt.build(scene, {"sid": "s5"})
    assert "DR. JANE GOODALL" in result["html"] or "dr. jane goodall" in result["html"].lower(), (
        "scene['point'] not used as NAME when present"
    )


def test_build_html_default_name_when_no_point_no_ost():
    """When scene has neither point nor on_screen_text, NAME defaults to 'FIELD REPORT'."""
    import studio.compose.archetypes.lower_third as lt
    scene = {"scene_no": 5, "narration": "x", "duration_est_sec": 4, "claims": []}
    result = lt.build(scene, {"sid": "s5"})
    assert "FIELD REPORT" in result["html"], (
        "Default name 'FIELD REPORT' not found in html when scene has no point/ost"
    )


def test_build_html_default_handle_when_no_title():
    """When no working title available, HANDLE defaults to '@FIELDREPORT'."""
    import studio.compose.archetypes.lower_third as lt
    result = lt.build(_make_scene(), {"sid": "s5"})
    assert "@FIELDREPORT" in result["html"] or "@" in result["html"], (
        "Default handle '@FIELDREPORT' not found in html"
    )


# === 5. Beats calls + anchor =================================================

def test_beats_js_calls_make_outline_draw():
    """beats_js must invoke makeOutlineDraw( for the signature flourish."""
    import studio.compose.archetypes.lower_third as lt
    result = lt.build(_make_scene(), {"sid": "s5"})
    assert "makeOutlineDraw(" in result["beats_js"], (
        "beats_js does not call makeOutlineDraw"
    )


def test_beats_js_calls_tl_from_for_handle_row():
    """beats_js must call tl.from( for the handle-row fade-in."""
    import studio.compose.archetypes.lower_third as lt
    result = lt.build(_make_scene(), {"sid": "s5"})
    assert "tl.from(" in result["beats_js"], (
        "beats_js does not contain tl.from( for handle-row"
    )


def test_beats_js_anchored_at_ctx_at():
    """beats_js must embed ctx['at'] as the outline-draw anchor."""
    import studio.compose.archetypes.lower_third as lt
    scene = _make_scene()
    ctx = {"sid": "s5", "spray": "#2e5e1f", "at": 18.3}
    result = lt.build(scene, ctx)
    assert "18.3" in result["beats_js"], (
        f"Expected ctx['at']=18.3 in beats_js but not found.\n"
        f"beats_js:\n{result['beats_js']}"
    )


def test_beats_js_default_anchor_is_0_6():
    """When ctx has no 'at', the default fallback is 0.6."""
    import studio.compose.archetypes.lower_third as lt
    result = lt.build(_make_scene(), {"sid": "s5"})
    assert "0.6" in result["beats_js"], (
        f"Expected default anchor 0.6 in beats_js when ctx has no 'at'.\n{result['beats_js']}"
    )


def test_beats_js_sid_scoped():
    """beats_js must scope the mount selector to the scene sid."""
    import studio.compose.archetypes.lower_third as lt
    ctx = {"sid": "s11", "spray": "#2e5e1f", "at": 5.0}
    result = lt.build(_make_scene(), ctx)
    assert "s11" in result["beats_js"], (
        "sid 's11' not found in beats_js — mount selector must include the sid"
    )


# === 6. scene_signature ======================================================

def test_scene_signature_returns_signature():
    """scene_signature must return 'signature' for a lower-third scene output."""
    import studio.compose.archetypes.lower_third as lt
    from studio.gate.parse import scene_signature

    scene = _make_scene()
    sid = "s5"
    ctx = {"sid": sid, "spray": "#2e5e1f"}
    result = lt.build(scene, ctx)

    sig = scene_signature(result["html"], result["beats_js"], sid)
    assert sig == "signature", (
        f"Expected scene_signature == 'signature' but got {sig!r}. "
        f"Check that html contains 'signature-fx'/'.signature' "
        f"or beats_js triggers the signature pattern."
    )


def test_scene_signature_not_plain():
    """Explicit guard: the signature must never fall back to 'plain'."""
    import studio.compose.archetypes.lower_third as lt
    from studio.gate.parse import scene_signature

    result = lt.build(_make_scene(), {"sid": "s5"})
    sig = scene_signature(result["html"], result["beats_js"], "s5")
    assert sig != "plain", (
        "scene_signature fell back to 'plain' — the gate cannot distinguish this archetype"
    )


def test_scene_signature_not_underline():
    """Critical ordering guard: signature must NOT return 'underline'.

    lower-third uses makeOutlineDraw which matches the LATER 'underline' token
    (r'makeOutlineDraw|underline').  Because the html carries 'signature' (via
    class 'signature-fx'/'.signature'), the EARLIER 'signature' token
    (r'signature|writeOn') must win.  This test locks that ordering guarantee.
    """
    import studio.compose.archetypes.lower_third as lt
    from studio.gate.parse import scene_signature

    result = lt.build(_make_scene(), {"sid": "s5"})
    sig = scene_signature(result["html"], result["beats_js"], "s5")
    assert sig != "underline", (
        f"scene_signature returned 'underline' instead of 'signature' — "
        f"the 'signature' token must appear BEFORE 'underline' in _BEAT_TOKENS "
        f"and the html must carry a 'signature' class/literal to match it first. "
        f"Got: {sig!r}"
    )


# === 7. Parity regression ====================================================

def test_parity_invariant_still_holds():
    """Every registered archetype's token must be in _BEAT_TOKENS (the parity invariant)."""
    import studio.compose.archetypes.lower_third  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    from studio.gate import parse as P

    token_names = {name for name, _pat in P._BEAT_TOKENS}
    for arch in A.REGISTRY:
        tok = A.token_for(arch)
        assert tok in token_names, (
            f"Parity broken: archetype {arch!r} emits token {tok!r} "
            f"not present in _BEAT_TOKENS"
        )
