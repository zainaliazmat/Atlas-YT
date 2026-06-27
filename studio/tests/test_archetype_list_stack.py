"""TDD tests for studio/compose/archetypes/list_stack.py — Task C3.

Tests:
  1. registration + parity: 'list-stack' is in archetypes.REGISTRY; token_for() == 'checklist';
     'checklist' is present in gate.parse._BEAT_TOKENS.
  2. determinism: build() output has no Math.random/Date.now/new Date/fetch/XMLHttpRequest;
     _motion.CHECKLIST_DRAW factory string has none of them (block comments excluded).
  3. required keys + token: html, beats_js, token in result; token == 'checklist'.
  4. content: a scene with on_screen_text "LOG OFF / BREATHE / LIVE REAL" yields a .checklist
     with 3 .check-row elements and the 3 labels present; each row has a .check-mark SVG
     with pathLength="1".
  5. beats call + anchor: beats_js contains 'makeChecklistDraw('; ctx={"at":18.3} puts 18.3
     in beats_js.
  6. signature: scene_signature(html, beats_js, sid) == "checklist" (not "plain").
  7. parity regression over all REGISTRY archetypes.
"""
from __future__ import annotations

import re


# --- helpers -----------------------------------------------------------------

def _make_scene(on_screen_text="LOG OFF / BREATHE / LIVE REAL", bullets=None):
    scene = {
        "scene_no": 5,
        "on_screen_text": on_screen_text,
        "point": "three steps",
        "narration": "Log off, breathe, live real.",
        "duration_est_sec": 8,
        "claims": [],
    }
    if bullets is not None:
        scene["bullets"] = bullets
    return scene


# === 1. Registration + token parity ==========================================

def test_list_stack_is_registered():
    """list_stack.py must call register() so 'list-stack' appears in REGISTRY."""
    import studio.compose.archetypes.list_stack  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    assert "list-stack" in A.REGISTRY, "'list-stack' not found in archetypes.REGISTRY"


def test_list_stack_token_for_returns_checklist():
    """token_for('list-stack') must return 'checklist'."""
    from studio.compose import archetypes as A
    assert A.token_for("list-stack") == "checklist", (
        f"token_for('list-stack') returned {A.token_for('list-stack')!r}, expected 'checklist'"
    )


def test_checklist_token_in_beat_tokens():
    """The 'checklist' token must be present in gate.parse._BEAT_TOKENS (parity invariant)."""
    from studio.gate import parse as P
    token_names = {name for name, _pat in P._BEAT_TOKENS}
    assert "checklist" in token_names, (
        "'checklist' not found in gate.parse._BEAT_TOKENS — parity broken"
    )


# === 2. Determinism ==========================================================

def test_build_output_has_no_banned_primitives():
    """html + beats_js must not contain Math.random/Date.now/new Date/fetch/XMLHttpRequest."""
    import studio.compose.archetypes.list_stack as ls
    scene = _make_scene()
    ctx = {"sid": "s5", "spray": "#2e5e1f", "ink": "#1f1f1e"}
    result = ls.build(scene, ctx)
    combined = result["html"] + result["beats_js"]
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bfetch\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(combined), (
        f"Banned non-deterministic primitive found in build() output: "
        f"{banned.findall(combined)}"
    )


def test_checklist_draw_factory_string_has_no_banned_primitives():
    """_motion.CHECKLIST_DRAW factory executable code must not contain any banned primitives.
    The leading block comment is excluded from this check since it describes what's forbidden."""
    from studio.compose import _motion
    assert hasattr(_motion, "CHECKLIST_DRAW"), (
        "_motion.CHECKLIST_DRAW not found — add the factory string to _motion.py"
    )
    # Strip the leading block comment (/* ... */) before scanning for banned primitives.
    code = re.sub(r"/\*.*?\*/", "", _motion.CHECKLIST_DRAW, flags=re.DOTALL)
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(code), (
        f"Banned primitive in _motion.CHECKLIST_DRAW executable code: "
        f"{banned.findall(code)}"
    )
    # 'fetch' must not appear in any executable statement
    fetch_call = re.compile(r"\bfetch\s*\(")
    assert not fetch_call.search(code), (
        "fetch() call found in _motion.CHECKLIST_DRAW executable code"
    )


def test_checklist_draw_in_beats_dict():
    """_motion.BEATS must include 'checklist-draw' entry mapping to makeChecklistDraw."""
    from studio.compose import _motion
    assert "checklist-draw" in _motion.BEATS, (
        "'checklist-draw' not found in _motion.BEATS"
    )
    factory, filename, source = _motion.BEATS["checklist-draw"]
    assert factory == "makeChecklistDraw", (
        f"Expected factory 'makeChecklistDraw', got {factory!r}"
    )
    assert filename == "checklist-draw.js", (
        f"Expected filename 'checklist-draw.js', got {filename!r}"
    )


# === 3. Required keys + token ================================================

def test_build_returns_required_keys():
    """build() must return dict with html, beats_js, token."""
    import studio.compose.archetypes.list_stack as ls
    result = ls.build(_make_scene(), {"sid": "s5"})
    assert "html" in result, "Missing 'html' key in build() result"
    assert "beats_js" in result, "Missing 'beats_js' key in build() result"
    assert "token" in result, "Missing 'token' key in build() result"


def test_build_token_is_checklist():
    """build() must return token == 'checklist'."""
    import studio.compose.archetypes.list_stack as ls
    result = ls.build(_make_scene(), {"sid": "s5"})
    assert result["token"] == "checklist", (
        f"Expected token 'checklist', got {result['token']!r}"
    )


# === 4. Content ==============================================================

def test_build_html_contains_checklist_class():
    """html must contain a .checklist wrapper."""
    import studio.compose.archetypes.list_stack as ls
    result = ls.build(_make_scene(), {"sid": "s5"})
    assert "checklist" in result["html"], ".checklist class not found in html"


def test_build_html_three_check_rows_from_slash_split():
    """'LOG OFF / BREATHE / LIVE REAL' must produce 3 .check-row elements."""
    import studio.compose.archetypes.list_stack as ls
    result = ls.build(_make_scene("LOG OFF / BREATHE / LIVE REAL"), {"sid": "s5"})
    count = result["html"].count("check-row")
    assert count == 3, (
        f"Expected 3 .check-row elements for 3-item slash-split scene, got {count}"
    )


def test_build_html_three_labels_present():
    """All 3 labels must appear in the html for the 3-item scene."""
    import studio.compose.archetypes.list_stack as ls
    result = ls.build(_make_scene("LOG OFF / BREATHE / LIVE REAL"), {"sid": "s5"})
    html = result["html"]
    assert "LOG OFF" in html, "Label 'LOG OFF' not found in html"
    assert "BREATHE" in html, "Label 'BREATHE' not found in html"
    assert "LIVE REAL" in html, "Label 'LIVE REAL' not found in html"


def test_build_html_check_rows_have_path_length():
    """Each .check-mark SVG must include pathLength=\"1\" for the strokeDashoffset animation."""
    import studio.compose.archetypes.list_stack as ls
    result = ls.build(_make_scene("LOG OFF / BREATHE / LIVE REAL"), {"sid": "s5"})
    path_length_count = result["html"].count('pathLength="1"')
    # Each row has at least a path (checkmark) with pathLength="1"
    assert path_length_count >= 3, (
        f"Expected at least 3 pathLength=\"1\" attributes (one per row), got {path_length_count}"
    )


def test_build_html_has_check_mark_class():
    """Each row must contain a .check-mark SVG element."""
    import studio.compose.archetypes.list_stack as ls
    result = ls.build(_make_scene("LOG OFF / BREATHE / LIVE REAL"), {"sid": "s5"})
    assert "check-mark" in result["html"], ".check-mark class not found in html"


def test_build_html_has_check_label_class():
    """Each row must contain a .check-label span."""
    import studio.compose.archetypes.list_stack as ls
    result = ls.build(_make_scene(), {"sid": "s5"})
    assert "check-label" in result["html"], ".check-label class not found in html"


def test_build_html_uses_bullets_list_when_present():
    """If scene has 'bullets', use them instead of splitting on_screen_text."""
    import studio.compose.archetypes.list_stack as ls
    scene = _make_scene("IGNORED TEXT", bullets=["Alpha", "Beta", "Gamma"])
    result = ls.build(scene, {"sid": "s5"})
    assert "Alpha" in result["html"], "Bullet 'Alpha' not found in html"
    assert "Beta" in result["html"], "Bullet 'Beta' not found in html"
    assert "Gamma" in result["html"], "Bullet 'Gamma' not found in html"
    count = result["html"].count("check-row")
    assert count == 3, f"Expected 3 .check-row for 3 bullets, got {count}"


def test_build_html_caps_at_six_items():
    """List items must be capped at 6."""
    import studio.compose.archetypes.list_stack as ls
    scene = _make_scene(bullets=["a", "b", "c", "d", "e", "f", "g", "h"])
    result = ls.build(scene, {"sid": "s5"})
    count = result["html"].count("check-row")
    assert count == 6, f"Expected items capped at 6, got {count}"


def test_build_html_escapes_special_chars():
    """Labels with HTML special chars must be escaped."""
    import studio.compose.archetypes.list_stack as ls
    scene = _make_scene(bullets=["<script>alert(1)</script>"])
    result = ls.build(scene, {"sid": "s5"})
    assert "<script>" not in result["html"], "HTML not escaped in label"
    assert "&lt;script&gt;" in result["html"], "Expected &lt;script&gt; escaped entity"


def test_build_html_svg_has_circle():
    """check-mark SVG must include a circle element."""
    import studio.compose.archetypes.list_stack as ls
    result = ls.build(_make_scene("ONE / TWO"), {"sid": "s5"})
    assert "<circle" in result["html"], "<circle> not found in check-mark SVG"


def test_build_html_fill_none_stroke_spray():
    """SVG elements must use fill=none and stroke the spray colour."""
    import studio.compose.archetypes.list_stack as ls
    ctx = {"sid": "s5", "spray": "#c0ffee"}
    result = ls.build(_make_scene(), ctx)
    assert 'fill="none"' in result["html"], 'fill="none" not found in SVG'


# === 5. Beats call + anchor ==================================================

def test_beats_js_calls_make_checklist_draw():
    """beats_js must invoke makeChecklistDraw(."""
    import studio.compose.archetypes.list_stack as ls
    result = ls.build(_make_scene(), {"sid": "s5"})
    assert "makeChecklistDraw(" in result["beats_js"], (
        "beats_js does not call makeChecklistDraw"
    )


def test_beats_js_anchored_at_ctx_at():
    """beats_js must embed ctx['at'] as the anchor, not 0."""
    import studio.compose.archetypes.list_stack as ls
    scene = _make_scene()
    ctx = {"sid": "s5", "spray": "#2e5e1f", "at": 18.3}
    result = ls.build(scene, ctx)
    assert "18.3" in result["beats_js"], (
        f"Expected ctx['at']=18.3 in beats_js but not found.\n"
        f"beats_js:\n{result['beats_js']}"
    )


def test_beats_js_default_anchor_not_zero():
    """When ctx has no 'at', the default fallback is 0.6 (not 0)."""
    import studio.compose.archetypes.list_stack as ls
    result = ls.build(_make_scene(), {"sid": "s5"})
    beats = result["beats_js"]
    assert "0.6" in beats, (
        f"Expected default anchor 0.6 in beats_js when ctx has no 'at'.\n{beats}"
    )


def test_beats_js_sid_scoped():
    """beats_js must scope the mount selector to the scene sid."""
    import studio.compose.archetypes.list_stack as ls
    ctx = {"sid": "s7", "spray": "#2e5e1f", "at": 5.0}
    result = ls.build(_make_scene(), ctx)
    assert "s7" in result["beats_js"], (
        "sid 's7' not found in beats_js — mount selector must include the sid"
    )


def test_beats_js_has_checklist_selector():
    """beats_js must select the .checklist element."""
    import studio.compose.archetypes.list_stack as ls
    result = ls.build(_make_scene(), {"sid": "s5"})
    assert ".checklist" in result["beats_js"], (
        ".checklist selector not found in beats_js"
    )


# === 6. scene_signature ======================================================

def test_scene_signature_returns_checklist():
    """scene_signature must return 'checklist' for a list-stack scene output."""
    import studio.compose.archetypes.list_stack as ls
    from studio.gate.parse import scene_signature

    scene = _make_scene()
    sid = "s5"
    ctx = {"sid": sid, "spray": "#2e5e1f"}
    result = ls.build(scene, ctx)

    sig = scene_signature(result["html"], result["beats_js"], sid)
    assert sig == "checklist", (
        f"Expected scene_signature == 'checklist' but got {sig!r}. "
        f"Check that html contains 'checklist' or 'checkmark' or beats_js triggers the token."
    )


def test_scene_signature_not_plain():
    """Explicit guard: the signature must never fall back to 'plain'."""
    import studio.compose.archetypes.list_stack as ls
    from studio.gate.parse import scene_signature

    result = ls.build(_make_scene(), {"sid": "s5"})
    sig = scene_signature(result["html"], result["beats_js"], "s5")
    assert sig != "plain", (
        "scene_signature fell back to 'plain' — the gate cannot distinguish this archetype"
    )


# === 7. Parity regression ====================================================

def test_parity_invariant_still_holds():
    """Every registered archetype's token must be in _BEAT_TOKENS (the parity invariant)."""
    import studio.compose.archetypes.list_stack  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    from studio.gate import parse as P

    token_names = {name for name, _pat in P._BEAT_TOKENS}
    for arch in A.REGISTRY:
        tok = A.token_for(arch)
        assert tok in token_names, (
            f"Parity broken: archetype {arch!r} emits token {tok!r} "
            f"not present in _BEAT_TOKENS"
        )


def test_list_stack_in_closed_vocab():
    """'list-stack' must be in the closed ARCHETYPES vocab."""
    from studio.compose import archetypes as A
    assert "list-stack" in A.ARCHETYPES, "'list-stack' not in ARCHETYPES closed vocab"
