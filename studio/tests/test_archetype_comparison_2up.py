"""TDD tests for studio/compose/archetypes/comparison_2up.py — Task C5.

Tests:
  1. registration + parity: 'comparison-2up' in REGISTRY; token_for == 'shatter';
     'shatter' in _BEAT_TOKENS names.
  2. determinism: builder html+beats_js and the _motion.SHATTER_GLITCH factory contain
     none of the banned primitives (strip /* */ comments before scanning; index arithmetic
     / tl.set / rgba strings are fine).
  3. keys + token: html, beats_js, token in result; token == 'shatter'.
  4. content: html contains 'comparison-2up', two 'cmp-panel' (left+right),
     'cmp-shatter-mount', and both side tags; a scene with on_screen_text
     'YOUR WORST VS THEIR HIGHLIGHT' yields LEFT containing 'WORST' and RIGHT
     containing 'HIGHLIGHT'.
  5. beats call + anchor: beats_js contains 'makeShatterGlitch('; ctx={"at":18.3} -> 18.3.
  6. signature: scene_signature(html, beats_js, sid) == 'shatter' (not 'plain', and NOT
     'calendar-crumble' — proving the C4 entry doesn't steal it).
  7. parity regression over all REGISTRY archetypes.
"""
from __future__ import annotations

import re


# --- helpers -----------------------------------------------------------------

def _make_scene(on_screen_text="YOUR REALITY VS THEIR HIGHLIGHT"):
    return {
        "scene_no": 7,
        "on_screen_text": on_screen_text,
        "point": "comparison",
        "narration": "This is how reality compares.",
        "duration_est_sec": 9,
        "claims": [],
    }


# === 1. Registration + token parity ==========================================

def test_comparison_2up_is_registered():
    """comparison_2up.py must call register() so 'comparison-2up' appears in REGISTRY."""
    import studio.compose.archetypes.comparison_2up  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    assert "comparison-2up" in A.REGISTRY, "'comparison-2up' not found in archetypes.REGISTRY"


def test_comparison_2up_token_for_returns_shatter():
    """token_for('comparison-2up') must return 'shatter'."""
    from studio.compose import archetypes as A
    assert A.token_for("comparison-2up") == "shatter", (
        f"token_for('comparison-2up') returned {A.token_for('comparison-2up')!r}, "
        f"expected 'shatter'"
    )


def test_shatter_token_in_beat_tokens():
    """The 'shatter' token must be present in gate.parse._BEAT_TOKENS (parity invariant)."""
    from studio.gate import parse as P
    token_names = {name for name, _pat in P._BEAT_TOKENS}
    assert "shatter" in token_names, (
        "'shatter' not found in gate.parse._BEAT_TOKENS — parity broken"
    )


# === 2. Determinism ==========================================================

def test_build_output_has_no_banned_primitives():
    """html + beats_js must not contain Math.random/Date.now/new Date/fetch/XMLHttpRequest."""
    import studio.compose.archetypes.comparison_2up as c2
    scene = _make_scene()
    ctx = {"sid": "s7", "spray": "#2e5e1f", "ink": "#1f1f1e"}
    result = c2.build(scene, ctx)
    combined = result["html"] + result["beats_js"]
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bfetch\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(combined), (
        f"Banned non-deterministic primitive found in build() output: "
        f"{banned.findall(combined)}"
    )


def test_shatter_glitch_factory_string_has_no_banned_primitives():
    """_motion.SHATTER_GLITCH factory executable code must not contain any banned
    primitives. Index arithmetic and tl.set / rgba strings are fine. Strip /* */ comments first."""
    from studio.compose import _motion
    assert hasattr(_motion, "SHATTER_GLITCH"), (
        "_motion.SHATTER_GLITCH not found — add the factory string to _motion.py"
    )
    # Strip block comments before scanning
    code = re.sub(r"/\*.*?\*/", "", _motion.SHATTER_GLITCH, flags=re.DOTALL)
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(code), (
        f"Banned primitive in _motion.SHATTER_GLITCH executable code: "
        f"{banned.findall(code)}"
    )
    # fetch() call pattern
    fetch_call = re.compile(r"\bfetch\s*\(")
    assert not fetch_call.search(code), (
        "fetch() call found in _motion.SHATTER_GLITCH executable code"
    )


def test_shatter_glitch_factory_uses_index_arithmetic():
    """The factory must use deterministic index-derived arithmetic (37, 53, 41)
    for shard drift — no Math.random."""
    from studio.compose import _motion
    assert "37" in _motion.SHATTER_GLITCH, "Expected shard-offset multiplier 37 in SHATTER_GLITCH"
    assert "53" in _motion.SHATTER_GLITCH, "Expected shard-offset multiplier 53 in SHATTER_GLITCH"
    assert "41" in _motion.SHATTER_GLITCH, "Expected shard-offset multiplier 41 in SHATTER_GLITCH"
    assert "Math.random" not in _motion.SHATTER_GLITCH, (
        "Math.random found in SHATTER_GLITCH — must be deterministic"
    )


def test_shatter_glitch_factory_uses_tl_set_for_glitch():
    """The RGB-split ghost must use tl.set steps (seek-safe determinism)."""
    from studio.compose import _motion
    assert "tl.set(" in _motion.SHATTER_GLITCH, (
        "tl.set( not found in SHATTER_GLITCH — RGB-split ghost must use tl.set for seek-safety"
    )


# === 3. Required keys + token ================================================

def test_build_returns_required_keys():
    """build() must return dict with html, beats_js, token."""
    import studio.compose.archetypes.comparison_2up as c2
    result = c2.build(_make_scene(), {"sid": "s7"})
    assert "html" in result, "Missing 'html' key in build() result"
    assert "beats_js" in result, "Missing 'beats_js' key in build() result"
    assert "token" in result, "Missing 'token' key in build() result"


def test_build_token_is_shatter():
    """build() must return token == 'shatter'."""
    import studio.compose.archetypes.comparison_2up as c2
    result = c2.build(_make_scene(), {"sid": "s7"})
    assert result["token"] == "shatter", (
        f"Expected token 'shatter', got {result['token']!r}"
    )


# === 4. Content ==============================================================

def test_build_html_contains_comparison_2up_class():
    """html must contain the 'comparison-2up' class."""
    import studio.compose.archetypes.comparison_2up as c2
    result = c2.build(_make_scene(), {"sid": "s7"})
    assert "comparison-2up" in result["html"], "'comparison-2up' class not found in html"


def test_build_html_contains_two_cmp_panels():
    """html must contain exactly two cmp-panel elements (left + right)."""
    import studio.compose.archetypes.comparison_2up as c2
    result = c2.build(_make_scene(), {"sid": "s7"})
    panels = re.findall(r'class="cmp-panel', result["html"])
    assert len(panels) == 2, (
        f"Expected 2 cmp-panel elements in html, found {len(panels)}. html:\n{result['html']}"
    )


def test_build_html_contains_cmp_shatter_mount():
    """html must contain 'cmp-shatter-mount' (the static shatter class for signature detection)."""
    import studio.compose.archetypes.comparison_2up as c2
    result = c2.build(_make_scene(), {"sid": "s7"})
    assert "cmp-shatter-mount" in result["html"], (
        "'cmp-shatter-mount' class not found in html — needed for static scene_signature match"
    )


def test_build_html_contains_left_panel():
    """html must contain cmp-left panel."""
    import studio.compose.archetypes.comparison_2up as c2
    result = c2.build(_make_scene(), {"sid": "s7"})
    assert "cmp-left" in result["html"], "'cmp-left' class not found in html"


def test_build_html_contains_right_panel():
    """html must contain cmp-right panel."""
    import studio.compose.archetypes.comparison_2up as c2
    result = c2.build(_make_scene(), {"sid": "s7"})
    assert "cmp-right" in result["html"], "'cmp-right' class not found in html"


def test_build_vs_split_left_right():
    """on_screen_text 'YOUR WORST VS THEIR HIGHLIGHT' → LEFT contains 'WORST',
    RIGHT contains 'HIGHLIGHT'."""
    import studio.compose.archetypes.comparison_2up as c2
    scene = _make_scene("YOUR WORST VS THEIR HIGHLIGHT")
    result = c2.build(scene, {"sid": "s7"})
    html = result["html"]
    assert "WORST" in html, f"LEFT tag 'WORST' not found in html:\n{html}"
    assert "HIGHLIGHT" in html, f"RIGHT tag 'HIGHLIGHT' not found in html:\n{html}"


def test_build_vs_split_case_insensitive():
    """VS split is case-insensitive (lowercase 'vs' also works)."""
    import studio.compose.archetypes.comparison_2up as c2
    scene = _make_scene("before vs after")
    result = c2.build(scene, {"sid": "s7"})
    html = result["html"]
    # After upper-casing + split
    assert "BEFORE" in html, f"LEFT 'BEFORE' not in html:\n{html}"
    assert "AFTER" in html, f"RIGHT 'AFTER' not in html:\n{html}"


def test_build_html_default_tags_when_no_vs():
    """When on_screen_text has no ' VS ', defaults to 'YOUR REALITY' / 'THEIR HIGHLIGHT'."""
    import studio.compose.archetypes.comparison_2up as c2
    scene = _make_scene("some statement without the delimiter")
    result = c2.build(scene, {"sid": "s7"})
    html = result["html"]
    assert "YOUR REALITY" in html, f"Default LEFT tag not found in html:\n{html}"
    assert "THEIR HIGHLIGHT" in html, f"Default RIGHT tag not found in html:\n{html}"


def test_build_html_tag_capped_at_22_chars():
    """Side tags are capped at 22 characters."""
    import studio.compose.archetypes.comparison_2up as c2
    long_left = "A" * 30
    long_right = "B" * 30
    scene = _make_scene(f"{long_left} VS {long_right}")
    result = c2.build(scene, {"sid": "s7"})
    # Find all cmp-tag spans
    tags = re.findall(r'class="cmp-tag mono">([^<]+)<', result["html"])
    assert len(tags) == 2, f"Expected 2 cmp-tag spans, found {len(tags)}"
    for tag in tags:
        assert len(tag) <= 22, f"Tag '{tag}' exceeds 22 chars (len={len(tag)})"


# === 5. Beats call + anchor ==================================================

def test_beats_js_calls_make_shatter_glitch():
    """beats_js must invoke makeShatterGlitch(."""
    import studio.compose.archetypes.comparison_2up as c2
    result = c2.build(_make_scene(), {"sid": "s7"})
    assert "makeShatterGlitch(" in result["beats_js"], (
        "beats_js does not call makeShatterGlitch"
    )


def test_beats_js_anchored_at_ctx_at():
    """beats_js must embed ctx['at'] as the anchor."""
    import studio.compose.archetypes.comparison_2up as c2
    scene = _make_scene()
    ctx = {"sid": "s7", "spray": "#2e5e1f", "at": 18.3}
    result = c2.build(scene, ctx)
    assert "18.3" in result["beats_js"], (
        f"Expected ctx['at']=18.3 in beats_js but not found.\n"
        f"beats_js:\n{result['beats_js']}"
    )


def test_beats_js_default_anchor_is_0_6():
    """When ctx has no 'at', the default fallback is 0.6 (not 0)."""
    import studio.compose.archetypes.comparison_2up as c2
    result = c2.build(_make_scene(), {"sid": "s7"})
    assert "0.6" in result["beats_js"], (
        f"Expected default anchor 0.6 in beats_js when ctx has no 'at'.\n{result['beats_js']}"
    )


def test_beats_js_sid_scoped():
    """beats_js must scope the mount selector to the scene sid."""
    import studio.compose.archetypes.comparison_2up as c2
    ctx = {"sid": "s11", "spray": "#2e5e1f", "at": 5.0}
    result = c2.build(_make_scene(), ctx)
    assert "s11" in result["beats_js"], (
        "sid 's11' not found in beats_js — mount selector must include the sid"
    )


# === 6. scene_signature ======================================================

def test_scene_signature_returns_shatter():
    """scene_signature must return 'shatter' for a comparison-2up scene output."""
    import studio.compose.archetypes.comparison_2up as c2
    from studio.gate.parse import scene_signature

    scene = _make_scene()
    sid = "s7"
    ctx = {"sid": sid, "spray": "#2e5e1f"}
    result = c2.build(scene, ctx)

    sig = scene_signature(result["html"], result["beats_js"], sid)
    assert sig == "shatter", (
        f"Expected scene_signature == 'shatter' but got {sig!r}. "
        f"Check that html contains 'cmp-shatter-mount' or beats_js calls 'makeShatterGlitch'."
    )


def test_scene_signature_not_plain():
    """Explicit guard: the signature must never fall back to 'plain'."""
    import studio.compose.archetypes.comparison_2up as c2
    from studio.gate.parse import scene_signature

    result = c2.build(_make_scene(), {"sid": "s7"})
    sig = scene_signature(result["html"], result["beats_js"], "s7")
    assert sig != "plain", (
        "scene_signature fell back to 'plain' — the gate cannot distinguish this archetype"
    )


def test_scene_signature_not_calendar_crumble():
    """Critical ordering test: comparison-2up must NOT be mislabelled 'calendar-crumble'.
    The C4 'calendar-crumble' entry is BEFORE 'shatter'; our html must not contain
    any calendar/data-chart markers that would match it."""
    import studio.compose.archetypes.comparison_2up as c2
    from studio.gate.parse import scene_signature

    result = c2.build(_make_scene(), {"sid": "s7"})
    sig = scene_signature(result["html"], result["beats_js"], "s7")
    assert sig != "calendar-crumble", (
        "comparison-2up scene was mislabelled 'calendar-crumble' — the html must NOT "
        "contain 'calendar-crumble', 'makeCalendarCrumble', 'calendar-grid', or 'data-chart' markers."
    )


def test_html_contains_no_calendar_or_data_chart_markers():
    """html must not contain any calendar-crumble/data-chart class names that would
    trigger the C4 regex before the shatter regex fires."""
    import studio.compose.archetypes.comparison_2up as c2
    result = c2.build(_make_scene(), {"sid": "s7"})
    html = result["html"]
    forbidden = ["calendar-crumble", "makeCalendarCrumble", "calendar-grid", "data-chart"]
    for marker in forbidden:
        assert marker not in html, (
            f"Forbidden marker '{marker}' found in html — would cause mislabelling as "
            f"'calendar-crumble' by the C4 regex."
        )


# === 7. Parity regression ====================================================

def test_parity_invariant_still_holds():
    """Every registered archetype's token must be in _BEAT_TOKENS (the parity invariant)."""
    import studio.compose.archetypes.comparison_2up  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    from studio.gate import parse as P

    token_names = {name for name, _pat in P._BEAT_TOKENS}
    for arch in A.REGISTRY:
        tok = A.token_for(arch)
        assert tok in token_names, (
            f"Parity broken: archetype {arch!r} emits token {tok!r} "
            f"not present in _BEAT_TOKENS"
        )


def test_comparison_2up_in_closed_vocab():
    """'comparison-2up' must be in the closed ARCHETYPES vocab."""
    from studio.compose import archetypes as A
    assert "comparison-2up" in A.ARCHETYPES, "'comparison-2up' not in ARCHETYPES closed vocab"
