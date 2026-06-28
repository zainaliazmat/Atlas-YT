"""TDD tests for studio/compose/archetypes/split_screen.py — Task C10.

Tests:
  1. registration + parity: 'split-screen' in REGISTRY; token_for == 'tile-parallax';
     'tile-parallax' in _BEAT_TOKENS names.
  2. determinism: builder html+beats_js and _motion.TILE_PARALLAX factory — no banned
     primitives (strip /* */ comments).
  3. keys + token 'tile-parallax'.
  4. content: html contains 'split-screen', two 'tile-panel', two 'tile-inner', both tags;
     a scene on_screen_text 'POOR VS RICH' yields LEFT containing 'POOR', RIGHT containing 'RICH'.
  5. beats call + anchor: beats_js contains 'makeTileParallax('; ctx={"at":18.3} -> 18.3.
  6. signature: scene_signature(html, beats_js, sid) == 'tile-parallax' (not 'plain').
  7. parity regression over all REGISTRY archetypes.
"""
from __future__ import annotations

import re


# --- helpers -----------------------------------------------------------------

def _make_scene(on_screen_text="POOR VS RICH"):
    return {
        "scene_no": 5,
        "on_screen_text": on_screen_text,
        "point": "split comparison",
        "narration": "Here is the contrast.",
        "duration_est_sec": 9,
        "claims": [],
    }


# === 1. Registration + token parity ==========================================

def test_split_screen_is_registered():
    """split_screen.py must call register() so 'split-screen' appears in REGISTRY."""
    import studio.compose.archetypes.split_screen  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    assert "split-screen" in A.REGISTRY, "'split-screen' not found in archetypes.REGISTRY"


def test_split_screen_token_for_returns_tile_parallax():
    """token_for('split-screen') must return 'tile-parallax'."""
    from studio.compose import archetypes as A
    assert A.token_for("split-screen") == "tile-parallax", (
        f"token_for('split-screen') returned {A.token_for('split-screen')!r}, "
        f"expected 'tile-parallax'"
    )


def test_tile_parallax_token_in_beat_tokens():
    """The 'tile-parallax' token must be present in gate.parse._BEAT_TOKENS (parity invariant)."""
    from studio.gate import parse as P
    token_names = {name for name, _pat in P._BEAT_TOKENS}
    assert "tile-parallax" in token_names, (
        "'tile-parallax' not found in gate.parse._BEAT_TOKENS — parity broken"
    )


# === 2. Determinism ==========================================================

def test_build_output_has_no_banned_primitives():
    """html + beats_js must not contain Math.random/Date.now/new Date/fetch/XMLHttpRequest."""
    import studio.compose.archetypes.split_screen as ss
    scene = _make_scene()
    ctx = {"sid": "s5", "spray": "#2e5e1f", "ink": "#1f1f1e"}
    result = ss.build(scene, ctx)
    combined = result["html"] + result["beats_js"]
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bfetch\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(combined), (
        f"Banned non-deterministic primitive found in build() output: "
        f"{banned.findall(combined)}"
    )


def test_tile_parallax_factory_string_has_no_banned_primitives():
    """_motion.TILE_PARALLAX factory executable code must not contain any banned
    primitives. Strip /* */ comments before scanning."""
    from studio.compose import _motion
    assert hasattr(_motion, "TILE_PARALLAX"), (
        "_motion.TILE_PARALLAX not found — add the factory string to _motion.py"
    )
    # Strip block comments before scanning
    code = re.sub(r"/\*.*?\*/", "", _motion.TILE_PARALLAX, flags=re.DOTALL)
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(code), (
        f"Banned primitive in _motion.TILE_PARALLAX executable code: "
        f"{banned.findall(code)}"
    )
    # fetch() call pattern
    fetch_call = re.compile(r"\bfetch\s*\(")
    assert not fetch_call.search(code), (
        "fetch() call found in _motion.TILE_PARALLAX executable code"
    )


def test_tile_parallax_factory_uses_index_arithmetic():
    """The factory must use deterministic index-derived arithmetic (i % 2) for parallax
    direction — no Math.random."""
    from studio.compose import _motion
    assert "i % 2" in _motion.TILE_PARALLAX or "i%2" in _motion.TILE_PARALLAX, (
        "Expected index-derived direction arithmetic (i % 2) in TILE_PARALLAX"
    )
    assert "Math.random" not in _motion.TILE_PARALLAX, (
        "Math.random found in TILE_PARALLAX — must be deterministic"
    )


def test_tile_parallax_factory_uses_yoyo():
    """The parallax inner drift must use yoyo for the back-and-forth motion."""
    from studio.compose import _motion
    assert "yoyo" in _motion.TILE_PARALLAX, (
        "yoyo not found in TILE_PARALLAX — inner parallax drift should yoyo"
    )


# === 3. Required keys + token ================================================

def test_build_returns_required_keys():
    """build() must return dict with html, beats_js, token."""
    import studio.compose.archetypes.split_screen as ss
    result = ss.build(_make_scene(), {"sid": "s5"})
    assert "html" in result, "Missing 'html' key in build() result"
    assert "beats_js" in result, "Missing 'beats_js' key in build() result"
    assert "token" in result, "Missing 'token' key in build() result"


def test_build_token_is_tile_parallax():
    """build() must return token == 'tile-parallax'."""
    import studio.compose.archetypes.split_screen as ss
    result = ss.build(_make_scene(), {"sid": "s5"})
    assert result["token"] == "tile-parallax", (
        f"Expected token 'tile-parallax', got {result['token']!r}"
    )


# === 4. Content ==============================================================

def test_build_html_contains_split_screen_class():
    """html must contain the 'split-screen' class."""
    import studio.compose.archetypes.split_screen as ss
    result = ss.build(_make_scene(), {"sid": "s5"})
    assert "split-screen" in result["html"], "'split-screen' class not found in html"


def test_build_html_contains_two_tile_panels():
    """html must contain exactly two tile-panel elements."""
    import studio.compose.archetypes.split_screen as ss
    result = ss.build(_make_scene(), {"sid": "s5"})
    panels = re.findall(r'class="[^"]*tile-panel', result["html"])
    assert len(panels) == 2, (
        f"Expected 2 tile-panel elements in html, found {len(panels)}. html:\n{result['html']}"
    )


def test_build_html_contains_two_tile_inners():
    """html must contain exactly two tile-inner elements."""
    import studio.compose.archetypes.split_screen as ss
    result = ss.build(_make_scene(), {"sid": "s5"})
    inners = re.findall(r'class="[^"]*tile-inner', result["html"])
    assert len(inners) == 2, (
        f"Expected 2 tile-inner elements in html, found {len(inners)}. html:\n{result['html']}"
    )


def test_build_html_contains_tile_left_and_right():
    """html must contain tile-left and tile-right panel classes."""
    import studio.compose.archetypes.split_screen as ss
    result = ss.build(_make_scene(), {"sid": "s5"})
    assert "tile-left" in result["html"], "'tile-left' class not found in html"
    assert "tile-right" in result["html"], "'tile-right' class not found in html"


def test_build_vs_split_poor_vs_rich():
    """on_screen_text 'POOR VS RICH' → LEFT contains 'POOR', RIGHT contains 'RICH'."""
    import studio.compose.archetypes.split_screen as ss
    scene = _make_scene("POOR VS RICH")
    result = ss.build(scene, {"sid": "s5"})
    html = result["html"]
    assert "POOR" in html, f"LEFT tag 'POOR' not found in html:\n{html}"
    assert "RICH" in html, f"RIGHT tag 'RICH' not found in html:\n{html}"


def test_build_vs_split_case_insensitive():
    """VS split is case-insensitive (lowercase 'vs' also works)."""
    import studio.compose.archetypes.split_screen as ss
    scene = _make_scene("before vs after")
    result = ss.build(scene, {"sid": "s5"})
    html = result["html"]
    assert "BEFORE" in html, f"LEFT 'BEFORE' not in html:\n{html}"
    assert "AFTER" in html, f"RIGHT 'AFTER' not in html:\n{html}"


def test_build_vs_split_slash_delimiter():
    """VS split also works with '/' delimiter."""
    import studio.compose.archetypes.split_screen as ss
    scene = _make_scene("LEFT/RIGHT")
    result = ss.build(scene, {"sid": "s5"})
    html = result["html"]
    assert "LEFT" in html, f"LEFT tag 'LEFT' not found in html:\n{html}"
    assert "RIGHT" in html, f"RIGHT tag 'RIGHT' not found in html:\n{html}"


def test_build_html_default_tags_when_no_delimiter():
    """When on_screen_text has no VS or /, defaults to 'BEFORE' / 'AFTER'."""
    import studio.compose.archetypes.split_screen as ss
    scene = _make_scene("some statement without delimiter")
    result = ss.build(scene, {"sid": "s5"})
    html = result["html"]
    assert "BEFORE" in html, f"Default LEFT tag not found in html:\n{html}"
    assert "AFTER" in html, f"Default RIGHT tag not found in html:\n{html}"


def test_build_html_tag_capped_at_20_chars():
    """Side tags are capped at 20 characters."""
    import studio.compose.archetypes.split_screen as ss
    long_left = "A" * 30
    long_right = "B" * 30
    scene = _make_scene(f"{long_left} VS {long_right}")
    result = ss.build(scene, {"sid": "s5"})
    tags = re.findall(r'class="tile-tag mono">([^<]+)<', result["html"])
    assert len(tags) == 2, f"Expected 2 tile-tag spans, found {len(tags)}"
    for tag in tags:
        assert len(tag) <= 20, f"Tag '{tag}' exceeds 20 chars (len={len(tag)})"


def test_build_html_contains_tile_parallax_fx_class():
    """html must contain 'tile-parallax-fx' class so scene_signature can match 'tile-parallax'
    via the static html even without scoped beats_js."""
    import studio.compose.archetypes.split_screen as ss
    result = ss.build(_make_scene(), {"sid": "s5"})
    assert "tile-parallax-fx" in result["html"], (
        "'tile-parallax-fx' class not found in html — needed for static scene_signature match"
    )


def test_build_html_contains_tile_tag_mono():
    """Each panel's inner span must use 'tile-tag mono' classes."""
    import studio.compose.archetypes.split_screen as ss
    result = ss.build(_make_scene(), {"sid": "s5"})
    tags = re.findall(r'class="tile-tag mono"', result["html"])
    assert len(tags) == 2, (
        f"Expected 2 'tile-tag mono' spans in html, found {len(tags)}. html:\n{result['html']}"
    )


# === 5. Beats call + anchor ==================================================

def test_beats_js_calls_make_tile_parallax():
    """beats_js must invoke makeTileParallax(."""
    import studio.compose.archetypes.split_screen as ss
    result = ss.build(_make_scene(), {"sid": "s5"})
    assert "makeTileParallax(" in result["beats_js"], (
        "beats_js does not call makeTileParallax"
    )


def test_beats_js_anchored_at_ctx_at():
    """beats_js must embed ctx['at'] as the anchor."""
    import studio.compose.archetypes.split_screen as ss
    scene = _make_scene()
    ctx = {"sid": "s5", "spray": "#2e5e1f", "at": 18.3}
    result = ss.build(scene, ctx)
    assert "18.3" in result["beats_js"], (
        f"Expected ctx['at']=18.3 in beats_js but not found.\n"
        f"beats_js:\n{result['beats_js']}"
    )


def test_beats_js_default_anchor_is_0_6():
    """When ctx has no 'at', the default fallback is 0.6 (not 0)."""
    import studio.compose.archetypes.split_screen as ss
    result = ss.build(_make_scene(), {"sid": "s5"})
    assert "0.6" in result["beats_js"], (
        f"Expected default anchor 0.6 in beats_js when ctx has no 'at'.\n{result['beats_js']}"
    )


def test_beats_js_sid_scoped():
    """beats_js must scope the mount selector to the scene sid."""
    import studio.compose.archetypes.split_screen as ss
    ctx = {"sid": "s11", "spray": "#2e5e1f", "at": 5.0}
    result = ss.build(_make_scene(), ctx)
    assert "s11" in result["beats_js"], (
        "sid 's11' not found in beats_js — mount selector must include the sid"
    )


def test_beats_js_passes_dur_6():
    """beats_js must pass dur: 6 to makeTileParallax for the parallax drift duration."""
    import studio.compose.archetypes.split_screen as ss
    result = ss.build(_make_scene(), {"sid": "s5"})
    assert "dur: 6" in result["beats_js"] or "dur:6" in result["beats_js"], (
        "beats_js does not pass dur: 6 to makeTileParallax"
    )


# === 6. scene_signature ======================================================

def test_scene_signature_returns_tile_parallax():
    """scene_signature must return 'tile-parallax' for a split-screen scene output."""
    import studio.compose.archetypes.split_screen as ss
    from studio.gate.parse import scene_signature

    scene = _make_scene()
    sid = "s5"
    ctx = {"sid": sid, "spray": "#2e5e1f"}
    result = ss.build(scene, ctx)

    sig = scene_signature(result["html"], result["beats_js"], sid)
    assert sig == "tile-parallax", (
        f"Expected scene_signature == 'tile-parallax' but got {sig!r}. "
        f"Check that html contains 'tile-parallax-fx' or beats_js calls 'makeTileParallax'."
    )


def test_scene_signature_not_plain():
    """Explicit guard: the signature must never fall back to 'plain'."""
    import studio.compose.archetypes.split_screen as ss
    from studio.gate.parse import scene_signature

    result = ss.build(_make_scene(), {"sid": "s5"})
    sig = scene_signature(result["html"], result["beats_js"], "s5")
    assert sig != "plain", (
        "scene_signature fell back to 'plain' — the gate cannot distinguish this archetype"
    )


# === 7. Parity regression ====================================================

def test_parity_invariant_still_holds():
    """Every registered archetype's token must be in _BEAT_TOKENS (the parity invariant)."""
    import studio.compose.archetypes.split_screen  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    from studio.gate import parse as P

    token_names = {name for name, _pat in P._BEAT_TOKENS}
    for arch in A.REGISTRY:
        tok = A.token_for(arch)
        assert tok in token_names, (
            f"Parity broken: archetype {arch!r} emits token {tok!r} "
            f"not present in _BEAT_TOKENS"
        )


def test_split_screen_in_closed_vocab():
    """'split-screen' must be in the closed ARCHETYPES vocab."""
    from studio.compose import archetypes as A
    assert "split-screen" in A.ARCHETYPES, "'split-screen' not in ARCHETYPES closed vocab"
