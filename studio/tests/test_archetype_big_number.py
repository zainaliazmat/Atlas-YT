"""TDD tests for studio/compose/archetypes/big_number.py — Task C2.

Tests:
  1. registration + parity: 'big-number' is in archetypes.REGISTRY; token_for() == 'count-up';
     'count-up' is present in gate.parse._BEAT_TOKENS.
  2. determinism: build() output has no Math.random/Date.now/new Date/fetch/XMLHttpRequest;
     _motion.BIG_NUMBER factory string has none of them.
  3. required keys + token: html, beats_js, token in result; token == 'count-up'.
  4. content: for a "5.66B USERS" scene, html contains count-host, data-count="5.66",
     data-dec="2", data-suffix contains "B", and the label text.
  5. beats call + anchor: beats_js contains 'makeBigNumber('; ctx at=18.3 appears in beats_js.
  6. signature: scene_signature(html, beats_js, sid) == 'count-up'.
  7. parity regression: every archetype in REGISTRY emits a token in _BEAT_TOKENS names.
"""
from __future__ import annotations

import re


# --- helpers -----------------------------------------------------------------

def _make_scene(on_screen_text="5.66B USERS", point="users worldwide"):
    return {
        "scene_no": 3,
        "on_screen_text": on_screen_text,
        "point": point,
        "narration": "5.66 billion users active daily.",
        "duration_est_sec": 8,
        "claims": [],
    }


# === 1. Registration + token parity ==========================================

def test_big_number_is_registered():
    """big_number.py must call register() so 'big-number' appears in REGISTRY."""
    import studio.compose.archetypes.big_number  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    assert "big-number" in A.REGISTRY, "'big-number' not found in archetypes.REGISTRY"


def test_big_number_token_for_returns_count_up():
    """token_for('big-number') must return 'count-up'."""
    from studio.compose import archetypes as A
    assert A.token_for("big-number") == "count-up", (
        f"token_for('big-number') returned {A.token_for('big-number')!r}, expected 'count-up'"
    )


def test_count_up_token_in_beat_tokens():
    """The 'count-up' token must be present in gate.parse._BEAT_TOKENS (parity invariant)."""
    from studio.gate import parse as P
    token_names = {name for name, _pat in P._BEAT_TOKENS}
    assert "count-up" in token_names, (
        "'count-up' not found in gate.parse._BEAT_TOKENS — parity broken"
    )


# === 2. Determinism ==========================================================

def test_build_output_has_no_banned_primitives():
    """html + beats_js must not contain Math.random/Date.now/new Date/fetch/XMLHttpRequest."""
    import studio.compose.archetypes.big_number as bn
    scene = _make_scene()
    ctx = {"sid": "s3", "spray": "#2e5e1f", "ink": "#1f1f1e"}
    result = bn.build(scene, ctx)
    combined = result["html"] + result["beats_js"]
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bfetch\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(combined), (
        f"Banned non-deterministic primitive found in build() output: "
        f"{banned.findall(combined)}"
    )


def test_big_number_factory_string_has_no_banned_primitives():
    """_motion.BIG_NUMBER factory executable code must not contain any banned primitives.
    Math.round and Math.pow are explicitly allowed. The leading block comment is excluded
    from this check since it names forbidden primitives in its description."""
    from studio.compose import _motion
    assert hasattr(_motion, "BIG_NUMBER"), (
        "_motion.BIG_NUMBER not found — add the factory string to _motion.py"
    )
    # Strip the leading block comment (/* ... */) before scanning for banned primitives.
    # The comment intentionally names what's forbidden; the executable code must not use them.
    code = re.sub(r"/\*.*?\*/", "", _motion.BIG_NUMBER, flags=re.DOTALL)
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(code), (
        f"Banned primitive in _motion.BIG_NUMBER executable code: "
        f"{banned.findall(code)}"
    )
    # 'fetch' must not appear in any executable statement (not in strings/comments):
    # check for fetch( call pattern specifically
    fetch_call = re.compile(r"\bfetch\s*\(")
    assert not fetch_call.search(code), (
        "fetch() call found in _motion.BIG_NUMBER executable code"
    )


def test_big_number_factory_string_allows_math_round_and_pow():
    """Math.round and Math.pow are fine (deterministic) — confirm they're actually present."""
    from studio.compose import _motion
    assert "Math.round" in _motion.BIG_NUMBER, "Math.round should be in BIG_NUMBER factory"
    assert "Math.pow" in _motion.BIG_NUMBER, "Math.pow should be in BIG_NUMBER factory"


# === 3. Required keys + token ================================================

def test_build_returns_required_keys():
    """build() must return dict with html, beats_js, token."""
    import studio.compose.archetypes.big_number as bn
    result = bn.build(_make_scene(), {"sid": "s3"})
    assert "html" in result, "Missing 'html' key in build() result"
    assert "beats_js" in result, "Missing 'beats_js' key in build() result"
    assert "token" in result, "Missing 'token' key in build() result"


def test_build_token_is_count_up():
    """build() must return token == 'count-up'."""
    import studio.compose.archetypes.big_number as bn
    result = bn.build(_make_scene(), {"sid": "s3"})
    assert result["token"] == "count-up", (
        f"Expected token 'count-up', got {result['token']!r}"
    )


# === 4. Content ==============================================================

def test_build_html_contains_count_host():
    """html must contain the count-host class."""
    import studio.compose.archetypes.big_number as bn
    result = bn.build(_make_scene("5.66B USERS", "users worldwide"), {"sid": "s3"})
    assert "count-host" in result["html"], "count-host class not found in html"


def test_build_html_contains_stat_card():
    """html must contain stat-card class (the enclosing card that punches in)."""
    import studio.compose.archetypes.big_number as bn
    result = bn.build(_make_scene(), {"sid": "s3"})
    assert "stat-card" in result["html"], "stat-card class not found in html"


def test_build_html_has_data_count_for_5_66b():
    """For '5.66B USERS', data-count must contain 5.66 (the parsed float)."""
    import studio.compose.archetypes.big_number as bn
    result = bn.build(_make_scene("5.66B USERS"), {"sid": "s3"})
    assert 'data-count="5.66"' in result["html"], (
        f"Expected data-count=\"5.66\" in html. Got:\n{result['html']}"
    )


def test_build_html_has_data_dec_2_for_5_66b():
    """For '5.66B USERS', data-dec must be 2 (two decimal places)."""
    import studio.compose.archetypes.big_number as bn
    result = bn.build(_make_scene("5.66B USERS"), {"sid": "s3"})
    assert 'data-dec="2"' in result["html"], (
        f"Expected data-dec=\"2\" in html. Got:\n{result['html']}"
    )


def test_build_html_has_suffix_containing_b_for_5_66b():
    """For '5.66B USERS', data-suffix must contain 'B' (the unit)."""
    import studio.compose.archetypes.big_number as bn
    result = bn.build(_make_scene("5.66B USERS"), {"sid": "s3"})
    # suffix could be " B" (space-prefixed) or "B"
    assert "B" in result["html"] and "data-suffix=" in result["html"], (
        f"Expected data-suffix with 'B' in html. Got:\n{result['html']}"
    )


def test_build_html_label_is_scene_point():
    """html must include the scene's 'point' as a stat label (uppercased, ≤24 chars)."""
    import studio.compose.archetypes.big_number as bn
    result = bn.build(_make_scene("5.66B USERS", "users worldwide"), {"sid": "s3"})
    assert "USERS WORLDWIDE" in result["html"].upper(), (
        f"Label from scene['point'] not found in html. Got:\n{result['html']}"
    )


def test_build_html_has_stat_label_class():
    """html must contain the stat-label class."""
    import studio.compose.archetypes.big_number as bn
    result = bn.build(_make_scene(), {"sid": "s3"})
    assert "stat-label" in result["html"], "stat-label class not found in html"


def test_build_no_number_scene_still_emits_card():
    """When no number is found, target=0 dec=0 suffix='' but card is still emitted."""
    import studio.compose.archetypes.big_number as bn
    scene = _make_scene("THE DARK SIDE", "no numbers here")
    result = bn.build(scene, {"sid": "s3"})
    assert "stat-card" in result["html"], "stat-card must still be emitted when no number"
    assert "count-host" in result["html"], "count-host must still be emitted when no number"
    assert 'data-count="0"' in result["html"], (
        f"Fallback target=0 expected. Got:\n{result['html']}"
    )


def test_build_percentage_suffix():
    """For '96%', data-suffix must be '%' (tight, no space)."""
    import studio.compose.archetypes.big_number as bn
    result = bn.build(_make_scene("96% accuracy"), {"sid": "s3"})
    assert 'data-suffix="%"' in result["html"], (
        f"Expected data-suffix=\"%\" for 96% scene. Got:\n{result['html']}"
    )


def test_build_x_suffix():
    """For '96x faster', data-suffix must be 'x' (tight, no space)."""
    import studio.compose.archetypes.big_number as bn
    result = bn.build(_make_scene("96x faster"), {"sid": "s3"})
    assert 'data-suffix="x"' in result["html"] or 'data-suffix="x"' in result["html"].lower(), (
        f"Expected tight data-suffix for x-multiplier. Got:\n{result['html']}"
    )


# === 5. Beats call + anchor ==================================================

def test_beats_js_calls_make_big_number():
    """beats_js must invoke makeBigNumber(."""
    import studio.compose.archetypes.big_number as bn
    result = bn.build(_make_scene(), {"sid": "s3"})
    assert "makeBigNumber(" in result["beats_js"], (
        "beats_js does not call makeBigNumber"
    )


def test_beats_js_anchored_at_ctx_at():
    """beats_js must embed ctx['at'] as the anchor, not 0."""
    import studio.compose.archetypes.big_number as bn
    scene = _make_scene()
    ctx = {"sid": "s3", "spray": "#2e5e1f", "at": 18.3}
    result = bn.build(scene, ctx)
    assert "18.3" in result["beats_js"], (
        f"Expected ctx['at']=18.3 in beats_js but not found.\n"
        f"beats_js:\n{result['beats_js']}"
    )


def test_beats_js_default_anchor_not_zero():
    """When ctx has no 'at', the default fallback is 0.6 (not 0)."""
    import studio.compose.archetypes.big_number as bn
    result = bn.build(_make_scene(), {"sid": "s3"})
    # Default anchor should be 0.6 (per brief: ctx.get("at", 0.6))
    beats = result["beats_js"]
    assert "0.6" in beats, (
        f"Expected default anchor 0.6 in beats_js when ctx has no 'at'.\n{beats}"
    )


def test_beats_js_sid_scoped():
    """beats_js must scope the mount selector to the scene sid."""
    import studio.compose.archetypes.big_number as bn
    ctx = {"sid": "s7", "spray": "#2e5e1f", "at": 5.0}
    result = bn.build(_make_scene(), ctx)
    assert "s7" in result["beats_js"], (
        "sid 's7' not found in beats_js — mount selector must include the sid"
    )


# === 6. scene_signature ======================================================

def test_scene_signature_returns_count_up():
    """scene_signature must return 'count-up' for a big-number scene output."""
    import studio.compose.archetypes.big_number as bn
    from studio.gate.parse import scene_signature

    scene = _make_scene()
    sid = "s3"
    ctx = {"sid": sid, "spray": "#2e5e1f"}
    result = bn.build(scene, ctx)

    sig = scene_signature(result["html"], result["beats_js"], sid)
    assert sig == "count-up", (
        f"Expected scene_signature == 'count-up' but got {sig!r}. "
        f"Check that html contains 'count-host' or beats_js contains 'count-up'."
    )


def test_scene_signature_not_plain():
    """Explicit guard: the signature must never fall back to 'plain'."""
    import studio.compose.archetypes.big_number as bn
    from studio.gate.parse import scene_signature

    result = bn.build(_make_scene(), {"sid": "s5"})
    sig = scene_signature(result["html"], result["beats_js"], "s5")
    assert sig != "plain", (
        "scene_signature fell back to 'plain' — the gate cannot distinguish this archetype"
    )


# === 7. Parity regression ====================================================

def test_parity_invariant_still_holds():
    """Every registered archetype's token must be in _BEAT_TOKENS (the parity invariant)."""
    import studio.compose.archetypes.big_number  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    from studio.gate import parse as P

    token_names = {name for name, _pat in P._BEAT_TOKENS}
    for arch in A.REGISTRY:
        tok = A.token_for(arch)
        assert tok in token_names, (
            f"Parity broken: archetype {arch!r} emits token {tok!r} "
            f"not present in _BEAT_TOKENS"
        )


def test_big_number_in_closed_vocab():
    """'big-number' must be in the closed ARCHETYPES vocab."""
    from studio.compose import archetypes as A
    assert "big-number" in A.ARCHETYPES, "'big-number' not in ARCHETYPES closed vocab"
