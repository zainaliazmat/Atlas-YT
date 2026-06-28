"""TDD tests for studio/compose/archetypes/map_focus.py — Task C11.

Tests:
  1. registration + parity: 'map-focus' in REGISTRY; token_for == 'map-draw';
     'map-draw' in _BEAT_TOKENS names.
  2. determinism: builder html+beats_js and the _motion.MAP_DRAW factory contain
     none of the banned primitives (strip /* */ comments before scanning; no RNG/Date).
  3. keys + token: html, beats_js, token in result; token == 'map-draw'.
  4. content: html contains 'map-focus', 'map-draw-fx', 'map-mount', and the LABEL.
  5. beats call + anchor: beats_js contains 'makeMapDraw('; ctx={"at":18.3} -> 18.3
     in beats_js.
  6. signature: scene_signature(html, beats_js, sid) == 'map-draw' (not 'plain').
  7. parity regression over all REGISTRY archetypes.
"""
from __future__ import annotations

import re


# --- helpers -----------------------------------------------------------------

def _make_scene(on_screen_text="MAP SCENE", point="San Francisco"):
    return {
        "scene_no": 7,
        "on_screen_text": on_screen_text,
        "point": point,
        "narration": "The route leads to San Francisco.",
        "duration_est_sec": 9,
        "claims": [],
    }


# === 1. Registration + token parity ==========================================

def test_map_focus_is_registered():
    """map_focus.py must call register() so 'map-focus' appears in REGISTRY."""
    import studio.compose.archetypes.map_focus  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    assert "map-focus" in A.REGISTRY, "'map-focus' not found in archetypes.REGISTRY"


def test_map_focus_token_for_returns_map_draw():
    """token_for('map-focus') must return 'map-draw'."""
    from studio.compose import archetypes as A
    assert A.token_for("map-focus") == "map-draw", (
        f"token_for('map-focus') returned {A.token_for('map-focus')!r}, "
        f"expected 'map-draw'"
    )


def test_map_draw_token_in_beat_tokens():
    """The 'map-draw' token must be present in gate.parse._BEAT_TOKENS (parity invariant)."""
    from studio.gate import parse as P
    token_names = {name for name, _pat in P._BEAT_TOKENS}
    assert "map-draw" in token_names, (
        "'map-draw' not found in gate.parse._BEAT_TOKENS — parity broken"
    )


# === 2. Determinism ==========================================================

def test_build_output_has_no_banned_primitives():
    """html + beats_js must not contain Math.random/Date.now/new Date/fetch/XMLHttpRequest."""
    import studio.compose.archetypes.map_focus as mf
    scene = _make_scene()
    ctx = {"sid": "s7", "spray": "#2e5e1f", "ink": "#1f1f1e"}
    result = mf.build(scene, ctx)
    combined = result["html"] + result["beats_js"]
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bfetch\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(combined), (
        f"Banned non-deterministic primitive found in build() output: "
        f"{banned.findall(combined)}"
    )


def test_map_draw_factory_string_has_no_banned_primitives():
    """_motion.MAP_DRAW factory executable code must not contain any banned
    primitives. Strip /* */ comments first."""
    from studio.compose import _motion
    assert hasattr(_motion, "MAP_DRAW"), (
        "_motion.MAP_DRAW not found — add the factory string to _motion.py"
    )
    # Strip block comments before scanning
    code = re.sub(r"/\*.*?\*/", "", _motion.MAP_DRAW, flags=re.DOTALL)
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(code), (
        f"Banned primitive in _motion.MAP_DRAW executable code: "
        f"{banned.findall(code)}"
    )
    # fetch() call pattern
    fetch_call = re.compile(r"\bfetch\s*\(")
    assert not fetch_call.search(code), (
        "fetch() call found in _motion.MAP_DRAW executable code"
    )


def test_map_draw_factory_no_math_random():
    """The factory must not use Math.random — no RNG anywhere."""
    from studio.compose import _motion
    assert "Math.random" not in _motion.MAP_DRAW, (
        "Math.random found in MAP_DRAW — must be deterministic"
    )


# === 3. Required keys + token ================================================

def test_build_returns_required_keys():
    """build() must return dict with html, beats_js, token."""
    import studio.compose.archetypes.map_focus as mf
    result = mf.build(_make_scene(), {"sid": "s7"})
    assert "html" in result, "Missing 'html' key in build() result"
    assert "beats_js" in result, "Missing 'beats_js' key in build() result"
    assert "token" in result, "Missing 'token' key in build() result"


def test_build_token_is_map_draw():
    """build() must return token == 'map-draw'."""
    import studio.compose.archetypes.map_focus as mf
    result = mf.build(_make_scene(), {"sid": "s7"})
    assert result["token"] == "map-draw", (
        f"Expected token 'map-draw', got {result['token']!r}"
    )


# === 4. Content ==============================================================

def test_build_html_contains_map_focus_class():
    """html must contain the 'map-focus' class."""
    import studio.compose.archetypes.map_focus as mf
    result = mf.build(_make_scene(), {"sid": "s7"})
    assert "map-focus" in result["html"], "map-focus class not found in html"


def test_build_html_contains_map_draw_fx_class():
    """html must contain 'map-draw-fx' (carries the 'map-draw' literal for static signature match)."""
    import studio.compose.archetypes.map_focus as mf
    result = mf.build(_make_scene(), {"sid": "s7"})
    assert "map-draw-fx" in result["html"], "map-draw-fx class not found in html"


def test_build_html_contains_map_mount():
    """html must contain the 'map-mount' class for the SVG mount point."""
    import studio.compose.archetypes.map_focus as mf
    result = mf.build(_make_scene(), {"sid": "s7"})
    assert "map-mount" in result["html"], "map-mount class not found in html"


def test_build_html_contains_label():
    """html must contain the label derived from scene['point'] (upper-cased, ≤24 chars)."""
    import studio.compose.archetypes.map_focus as mf
    result = mf.build(_make_scene(point="San Francisco"), {"sid": "s7"})
    assert "SAN FRANCISCO" in result["html"].upper(), (
        f"Label from scene['point'] not found in html. Got:\n{result['html']}"
    )


def test_build_html_default_label_fallback():
    """html must default to 'FROM HERE' when scene has no point or on_screen_text."""
    import studio.compose.archetypes.map_focus as mf
    scene = {"scene_no": 7, "duration_est_sec": 9, "claims": []}
    result = mf.build(scene, {"sid": "s7"})
    assert "FROM HERE" in result["html"], (
        f"Default label 'FROM HERE' not found in html. Got:\n{result['html']}"
    )


def test_build_html_label_capped_at_24_chars():
    """html label must be capped at 24 characters."""
    import studio.compose.archetypes.map_focus as mf
    long_point = "this is a very long destination label exceeding 24 chars"
    result = mf.build(_make_scene(point=long_point), {"sid": "s7"})
    # Find the label text in the .map-label element
    label_m = re.search(r'class="map-label[^"]*">([^<]+)<', result["html"])
    assert label_m, "Could not find .map-label element in html"
    label_text = label_m.group(1)
    assert len(label_text) <= 24, (
        f"Label '{label_text}' exceeds 24 chars (len={len(label_text)})"
    )


# === 5. Beats call + anchor ==================================================

def test_beats_js_calls_make_map_draw():
    """beats_js must invoke makeMapDraw(."""
    import studio.compose.archetypes.map_focus as mf
    result = mf.build(_make_scene(), {"sid": "s7"})
    assert "makeMapDraw(" in result["beats_js"], (
        "beats_js does not call makeMapDraw"
    )


def test_beats_js_anchored_at_ctx_at():
    """beats_js must embed ctx['at'] as the anchor."""
    import studio.compose.archetypes.map_focus as mf
    scene = _make_scene()
    ctx = {"sid": "s7", "spray": "#2e5e1f", "at": 18.3}
    result = mf.build(scene, ctx)
    assert "18.3" in result["beats_js"], (
        f"Expected ctx['at']=18.3 in beats_js but not found.\n"
        f"beats_js:\n{result['beats_js']}"
    )


def test_beats_js_default_anchor_is_0_6():
    """When ctx has no 'at', the default fallback is 0.6."""
    import studio.compose.archetypes.map_focus as mf
    result = mf.build(_make_scene(), {"sid": "s7"})
    assert "0.6" in result["beats_js"], (
        f"Expected default anchor 0.6 in beats_js when ctx has no 'at'.\n{result['beats_js']}"
    )


def test_beats_js_sid_scoped():
    """beats_js must scope the mount selector to the scene sid."""
    import studio.compose.archetypes.map_focus as mf
    ctx = {"sid": "s11", "spray": "#2e5e1f", "at": 5.0}
    result = mf.build(_make_scene(), ctx)
    assert "s11" in result["beats_js"], (
        "sid 's11' not found in beats_js — mount selector must include the sid"
    )


# === 6. scene_signature ======================================================

def test_scene_signature_returns_map_draw():
    """scene_signature must return 'map-draw' for a map-focus scene output."""
    import studio.compose.archetypes.map_focus as mf
    from studio.gate.parse import scene_signature

    scene = _make_scene()
    sid = "s7"
    ctx = {"sid": sid, "spray": "#2e5e1f"}
    result = mf.build(scene, ctx)

    sig = scene_signature(result["html"], result["beats_js"], sid)
    assert sig == "map-draw", (
        f"Expected scene_signature == 'map-draw' but got {sig!r}. "
        f"Check that beats_js contains 'makeMapDraw' or html contains 'map-route'."
    )


def test_scene_signature_not_plain():
    """Explicit guard: the signature must never fall back to 'plain'."""
    import studio.compose.archetypes.map_focus as mf
    from studio.gate.parse import scene_signature

    result = mf.build(_make_scene(), {"sid": "s7"})
    sig = scene_signature(result["html"], result["beats_js"], "s7")
    assert sig != "plain", (
        "scene_signature fell back to 'plain' — the gate cannot distinguish this archetype"
    )


# === 7. Parity regression ====================================================

def test_parity_invariant_still_holds():
    """Every registered archetype's token must be in _BEAT_TOKENS (the parity invariant)."""
    import studio.compose.archetypes.map_focus  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    from studio.gate import parse as P

    token_names = {name for name, _pat in P._BEAT_TOKENS}
    for arch in A.REGISTRY:
        tok = A.token_for(arch)
        assert tok in token_names, (
            f"Parity broken: archetype {arch!r} emits token {tok!r} "
            f"not present in _BEAT_TOKENS"
        )


def test_map_focus_in_closed_vocab():
    """'map-focus' must be in the closed ARCHETYPES vocab."""
    from studio.compose import archetypes as A
    assert "map-focus" in A.ARCHETYPES, "'map-focus' not in ARCHETYPES closed vocab"
