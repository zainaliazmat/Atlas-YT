"""TDD tests for studio/compose/archetypes/centered_statement.py — Task C6.

Tests:
  1. registration + parity: 'centered-statement' in REGISTRY; token_for == 'strike';
     'strike' in _BEAT_TOKENS names.
  2. determinism: builder html+beats_js and _motion.STRIKE_STAMP factory — no banned
     primitives (strip /* */ comments first).
  3. keys + token 'strike'.
  4. content: html contains 'centered-statement', 'strike-line', 'spray-over', 'stamp',
     and the OVER/STAMP text.
  5. beats call + anchor: beats_js contains 'makeStrikeStamp('; ctx={"at":18.3} -> 18.3.
  6. signature: scene_signature(html, beats_js, sid) == 'strike' (not 'plain', not
     'underline').
  7. parity regression over all REGISTRY archetypes.
"""
from __future__ import annotations

import re


# --- helpers -----------------------------------------------------------------

def _make_scene(point="BY DESIGN", on_screen_text="CENTERED SCENE"):
    return {
        "scene_no": 6,
        "on_screen_text": on_screen_text,
        "point": point,
        "narration": "The statement drives the point home.",
        "duration_est_sec": 7,
        "claims": [],
    }


# === 1. Registration + token parity ==========================================

def test_centered_statement_is_registered():
    """centered_statement.py must call register() so 'centered-statement' appears in REGISTRY."""
    import studio.compose.archetypes.centered_statement  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    assert "centered-statement" in A.REGISTRY, (
        "'centered-statement' not found in archetypes.REGISTRY"
    )


def test_centered_statement_token_for_returns_strike():
    """token_for('centered-statement') must return 'strike' (not 'underline')."""
    from studio.compose import archetypes as A
    tok = A.token_for("centered-statement")
    assert tok == "strike", (
        f"token_for('centered-statement') returned {tok!r}, expected 'strike'"
    )


def test_strike_token_in_beat_tokens():
    """The 'strike' token must be present in gate.parse._BEAT_TOKENS (parity invariant)."""
    from studio.gate import parse as P
    token_names = {name for name, _pat in P._BEAT_TOKENS}
    assert "strike" in token_names, (
        "'strike' not found in gate.parse._BEAT_TOKENS — parity broken"
    )


# === 2. Determinism ==========================================================

def test_build_output_has_no_banned_primitives():
    """html + beats_js must not contain Math.random/Date.now/new Date/fetch/XMLHttpRequest."""
    import studio.compose.archetypes.centered_statement as cs
    scene = _make_scene()
    ctx = {"sid": "s6", "spray": "#2e5e1f", "ink": "#1f1f1e"}
    result = cs.build(scene, ctx)
    combined = result["html"] + result["beats_js"]
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bfetch\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(combined), (
        f"Banned non-deterministic primitive found in build() output: "
        f"{banned.findall(combined)}"
    )


def test_strike_stamp_factory_string_has_no_banned_primitives():
    """_motion.STRIKE_STAMP factory executable code must not contain any banned
    primitives. Strip /* */ comments first before scanning."""
    from studio.compose import _motion
    assert hasattr(_motion, "STRIKE_STAMP"), (
        "_motion.STRIKE_STAMP not found — add the factory string to _motion.py"
    )
    # Strip block comments before scanning
    code = re.sub(r"/\*.*?\*/", "", _motion.STRIKE_STAMP, flags=re.DOTALL)
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(code), (
        f"Banned primitive in _motion.STRIKE_STAMP executable code: "
        f"{banned.findall(code)}"
    )
    # fetch() call pattern
    fetch_call = re.compile(r"\bfetch\s*\(")
    assert not fetch_call.search(code), (
        "fetch() call found in _motion.STRIKE_STAMP executable code"
    )


def test_strike_stamp_in_beats_dict():
    """'strike-stamp' must be registered in _motion.BEATS."""
    from studio.compose import _motion
    assert "strike-stamp" in _motion.BEATS, (
        "'strike-stamp' not found in _motion.BEATS — add it alongside the factory"
    )


def test_strike_stamp_beats_entry_has_correct_factory():
    """BEATS['strike-stamp'] entry must reference makeStrikeStamp."""
    from studio.compose import _motion
    factory_name, filename, source = _motion.BEATS["strike-stamp"]
    assert factory_name == "makeStrikeStamp", (
        f"Expected factory name 'makeStrikeStamp', got {factory_name!r}"
    )
    assert filename == "strike-stamp.js", (
        f"Expected filename 'strike-stamp.js', got {filename!r}"
    )


# === 3. Required keys + token ================================================

def test_build_returns_required_keys():
    """build() must return dict with html, beats_js, token."""
    import studio.compose.archetypes.centered_statement as cs
    result = cs.build(_make_scene(), {"sid": "s6"})
    assert "html" in result, "Missing 'html' key in build() result"
    assert "beats_js" in result, "Missing 'beats_js' key in build() result"
    assert "token" in result, "Missing 'token' key in build() result"


def test_build_token_is_strike():
    """build() must return token == 'strike'."""
    import studio.compose.archetypes.centered_statement as cs
    result = cs.build(_make_scene(), {"sid": "s6"})
    assert result["token"] == "strike", (
        f"Expected token 'strike', got {result['token']!r}"
    )


# === 4. Content ==============================================================

def test_build_html_contains_centered_statement_class():
    """html must contain the 'centered-statement' class."""
    import studio.compose.archetypes.centered_statement as cs
    result = cs.build(_make_scene(), {"sid": "s6"})
    assert "centered-statement" in result["html"], (
        "'centered-statement' class not found in html"
    )


def test_build_html_contains_strike_line():
    """html must contain 'strike-line' (the strike-through element)."""
    import studio.compose.archetypes.centered_statement as cs
    result = cs.build(_make_scene(), {"sid": "s6"})
    assert "strike-line" in result["html"], (
        "'strike-line' not found in html — needed for the scaleX strike animation"
    )


def test_build_html_contains_spray_over():
    """html must contain 'spray-over' (the restatement element)."""
    import studio.compose.archetypes.centered_statement as cs
    result = cs.build(_make_scene(), {"sid": "s6"})
    assert "spray-over" in result["html"], (
        "'spray-over' not found in html — needed for the restatement rise-in"
    )


def test_build_html_contains_stamp():
    """html must contain 'stamp' (the punch-in stamp element)."""
    import studio.compose.archetypes.centered_statement as cs
    result = cs.build(_make_scene(), {"sid": "s6"})
    assert "stamp" in result["html"], (
        "'stamp' not found in html — needed for the stamp punch-in"
    )


def test_build_html_contains_strike_fx():
    """html must contain 'strike-fx' class (carries literal 'strike' for signature match)."""
    import studio.compose.archetypes.centered_statement as cs
    result = cs.build(_make_scene(), {"sid": "s6"})
    assert "strike-fx" in result["html"], (
        "'strike-fx' class not found in html"
    )


def test_build_html_over_text_is_upper_case_point():
    """The spray-over text must be the upper-cased scene['point'] (≤28 chars)."""
    import studio.compose.archetypes.centered_statement as cs
    scene = _make_scene(point="deliberately designed")
    result = cs.build(scene, {"sid": "s6"})
    assert "DELIBERATELY DESIGNED" in result["html"], (
        "Upper-cased point not found as spray-over text in html"
    )


def test_build_html_over_text_capped_at_28_chars():
    """spray-over text must be capped at 28 characters."""
    import studio.compose.archetypes.centered_statement as cs
    scene = _make_scene(point="A" * 40)
    result = cs.build(scene, {"sid": "s6"})
    # Find the spray-over div text
    m = re.search(r'class="spray-over[^"]*">([^<]+)<', result["html"])
    assert m is not None, "spray-over div not found in html"
    over_text = m.group(1)
    assert len(over_text) <= 28, (
        f"spray-over text exceeds 28 chars: {over_text!r} (len={len(over_text)})"
    )


def test_build_html_stamp_text_is_first_word_of_point():
    """STAMP must be the upper-cased first word of scene['point'] (≤14 chars)."""
    import studio.compose.archetypes.centered_statement as cs
    scene = _make_scene(point="deliberately designed")
    result = cs.build(scene, {"sid": "s6"})
    assert "DELIBERATELY" in result["html"], (
        "First word of point (upper-cased) not found as stamp text in html"
    )


def test_build_html_stamp_default_when_no_point():
    """When scene has no 'point', STAMP defaults to 'FACT'."""
    import studio.compose.archetypes.centered_statement as cs
    scene = _make_scene(point="")
    result = cs.build(scene, {"sid": "s6"})
    assert "FACT" in result["html"], (
        "Default STAMP 'FACT' not found when scene point is empty"
    )


def test_build_html_over_default_when_no_point():
    """When scene has no 'point', OVER defaults to 'BY DESIGN'."""
    import studio.compose.archetypes.centered_statement as cs
    scene = _make_scene(point="")
    result = cs.build(scene, {"sid": "s6"})
    assert "BY DESIGN" in result["html"], (
        "Default OVER 'BY DESIGN' not found when scene point is empty"
    )


# === 5. Beats call + anchor ==================================================

def test_beats_js_calls_make_strike_stamp():
    """beats_js must invoke makeStrikeStamp(."""
    import studio.compose.archetypes.centered_statement as cs
    result = cs.build(_make_scene(), {"sid": "s6"})
    assert "makeStrikeStamp(" in result["beats_js"], (
        "beats_js does not call makeStrikeStamp"
    )


def test_beats_js_anchored_at_ctx_at():
    """beats_js must embed ctx['at'] as the anchor."""
    import studio.compose.archetypes.centered_statement as cs
    scene = _make_scene()
    ctx = {"sid": "s6", "spray": "#2e5e1f", "at": 18.3}
    result = cs.build(scene, ctx)
    assert "18.3" in result["beats_js"], (
        f"Expected ctx['at']=18.3 in beats_js but not found.\n"
        f"beats_js:\n{result['beats_js']}"
    )


def test_beats_js_default_anchor_is_0_6():
    """When ctx has no 'at', the default fallback is 0.6."""
    import studio.compose.archetypes.centered_statement as cs
    result = cs.build(_make_scene(), {"sid": "s6"})
    assert "0.6" in result["beats_js"], (
        f"Expected default anchor 0.6 in beats_js when ctx has no 'at'.\n{result['beats_js']}"
    )


def test_beats_js_sid_scoped():
    """beats_js must scope the mount selector to the scene sid."""
    import studio.compose.archetypes.centered_statement as cs
    ctx = {"sid": "s11", "spray": "#2e5e1f", "at": 5.0}
    result = cs.build(_make_scene(), ctx)
    assert "s11" in result["beats_js"], (
        "sid 's11' not found in beats_js — mount selector must include the sid"
    )


# === 6. scene_signature ======================================================

def test_scene_signature_returns_strike():
    """scene_signature must return 'strike' for a centered-statement scene output."""
    import studio.compose.archetypes.centered_statement as cs
    from studio.gate.parse import scene_signature

    scene = _make_scene()
    sid = "s6"
    ctx = {"sid": sid, "spray": "#2e5e1f"}
    result = cs.build(scene, ctx)

    sig = scene_signature(result["html"], result["beats_js"], sid)
    assert sig == "strike", (
        f"Expected scene_signature == 'strike' but got {sig!r}. "
        f"Check that html contains 'strike-fx'/'strike-line' or beats_js calls 'makeStrikeStamp'."
    )


def test_scene_signature_not_plain():
    """Explicit guard: the signature must never fall back to 'plain'."""
    import studio.compose.archetypes.centered_statement as cs
    from studio.gate.parse import scene_signature

    result = cs.build(_make_scene(), {"sid": "s6"})
    sig = scene_signature(result["html"], result["beats_js"], "s6")
    assert sig != "plain", (
        "scene_signature fell back to 'plain' — the gate cannot distinguish this archetype"
    )


def test_scene_signature_not_underline():
    """Critical: signature must be 'strike', never 'underline' (the placeholder)."""
    import studio.compose.archetypes.centered_statement as cs
    from studio.gate.parse import scene_signature

    result = cs.build(_make_scene(), {"sid": "s6"})
    sig = scene_signature(result["html"], result["beats_js"], "s6")
    assert sig != "underline", (
        f"scene_signature returned 'underline' — expected 'strike' (the real contract). "
        f"Check that the html does NOT contain 'makeOutlineDraw'."
    )


# === 7. Parity regression ====================================================

def test_parity_invariant_still_holds():
    """Every registered archetype's token must be in _BEAT_TOKENS (the parity invariant)."""
    import studio.compose.archetypes.centered_statement  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    from studio.gate import parse as P

    token_names = {name for name, _pat in P._BEAT_TOKENS}
    for arch in A.REGISTRY:
        tok = A.token_for(arch)
        assert tok in token_names, (
            f"Parity broken: archetype {arch!r} emits token {tok!r} "
            f"not present in _BEAT_TOKENS"
        )


def test_centered_statement_in_closed_vocab():
    """'centered-statement' must be in the closed ARCHETYPES vocab."""
    from studio.compose import archetypes as A
    assert "centered-statement" in A.ARCHETYPES, (
        "'centered-statement' not in ARCHETYPES closed vocab"
    )
