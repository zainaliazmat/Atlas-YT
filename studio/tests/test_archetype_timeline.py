"""TDD tests for studio/compose/archetypes/timeline.py — Task C12.

Tests:
  1. registration + parity: 'timeline' in REGISTRY; token_for == 'timeline-rail';
     'timeline-rail' in _BEAT_TOKENS names.
  2. determinism: builder html+beats_js and the _motion.TIMELINE_RAIL factory contain
     none of the banned primitives (strip /* */ comments before scanning; no RNG/Date).
  3. keys + token: html, beats_js, token in result; token == 'timeline-rail'.
  4. content: html contains 'timeline', 'timeline-rail-fx', 'rail-line', >=2
     'rail-node', the labels; beats call makeTimelineRail( + ctx={"at":18.3}->18.3.
  5. signature: scene_signature(html, beats_js, sid) == 'timeline-rail' (not 'plain').
  6. on_screen_text '2004 / 2010 / 2020' yields 3 rail-nodes with those labels.
  7. parity regression over all REGISTRY archetypes.
"""
from __future__ import annotations

import re


# --- helpers -----------------------------------------------------------------

def _make_scene(on_screen_text="2004 / 2010 / 2020", bullets=None):
    return {
        "scene_no": 5,
        "on_screen_text": on_screen_text,
        "narration": "A timeline of key events.",
        "duration_est_sec": 9,
        "claims": [],
        **({"bullets": bullets} if bullets is not None else {}),
    }


# === 1. Registration + token parity ==========================================

def test_timeline_is_registered():
    """timeline.py must call register() so 'timeline' appears in REGISTRY."""
    import studio.compose.archetypes.timeline  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    assert "timeline" in A.REGISTRY, "'timeline' not found in archetypes.REGISTRY"


def test_timeline_token_for_returns_timeline_rail():
    """token_for('timeline') must return 'timeline-rail'."""
    from studio.compose import archetypes as A
    assert A.token_for("timeline") == "timeline-rail", (
        f"token_for('timeline') returned {A.token_for('timeline')!r}, "
        f"expected 'timeline-rail'"
    )


def test_timeline_rail_token_in_beat_tokens():
    """The 'timeline-rail' token must be present in gate.parse._BEAT_TOKENS (parity invariant)."""
    from studio.gate import parse as P
    token_names = {name for name, _pat in P._BEAT_TOKENS}
    assert "timeline-rail" in token_names, (
        "'timeline-rail' not found in gate.parse._BEAT_TOKENS — parity broken"
    )


# === 2. Determinism ==========================================================

def test_build_output_has_no_banned_primitives():
    """html + beats_js must not contain Math.random/Date.now/new Date/fetch/XMLHttpRequest."""
    import studio.compose.archetypes.timeline as tl_mod
    scene = _make_scene()
    ctx = {"sid": "s5", "spray": "#2e5e1f", "ink": "#1f1f1e"}
    result = tl_mod.build(scene, ctx)
    combined = result["html"] + result["beats_js"]
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bfetch\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(combined), (
        f"Banned non-deterministic primitive found in build() output: "
        f"{banned.findall(combined)}"
    )


def test_timeline_rail_factory_string_has_no_banned_primitives():
    """_motion.TIMELINE_RAIL factory executable code must not contain any banned
    primitives. Strip /* */ comments first."""
    from studio.compose import _motion
    assert hasattr(_motion, "TIMELINE_RAIL"), (
        "_motion.TIMELINE_RAIL not found — add the factory string to _motion.py"
    )
    # Strip block comments before scanning
    code = re.sub(r"/\*.*?\*/", "", _motion.TIMELINE_RAIL, flags=re.DOTALL)
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(code), (
        f"Banned primitive in _motion.TIMELINE_RAIL executable code: "
        f"{banned.findall(code)}"
    )
    # fetch() call pattern
    fetch_call = re.compile(r"\bfetch\s*\(")
    assert not fetch_call.search(code), (
        "fetch() call found in _motion.TIMELINE_RAIL executable code"
    )


def test_timeline_rail_factory_no_math_random():
    """The factory must not use Math.random — no RNG anywhere."""
    from studio.compose import _motion
    assert "Math.random" not in _motion.TIMELINE_RAIL, (
        "Math.random found in TIMELINE_RAIL — must be deterministic"
    )


# === 3. Required keys + token ================================================

def test_build_returns_required_keys():
    """build() must return dict with html, beats_js, token."""
    import studio.compose.archetypes.timeline as tl_mod
    result = tl_mod.build(_make_scene(), {"sid": "s5"})
    assert "html" in result, "Missing 'html' key in build() result"
    assert "beats_js" in result, "Missing 'beats_js' key in build() result"
    assert "token" in result, "Missing 'token' key in build() result"


def test_build_token_is_timeline_rail():
    """build() must return token == 'timeline-rail'."""
    import studio.compose.archetypes.timeline as tl_mod
    result = tl_mod.build(_make_scene(), {"sid": "s5"})
    assert result["token"] == "timeline-rail", (
        f"Expected token 'timeline-rail', got {result['token']!r}"
    )


# === 4. Content ==============================================================

def test_build_html_contains_timeline_class():
    """html must contain the 'timeline' class."""
    import studio.compose.archetypes.timeline as tl_mod
    result = tl_mod.build(_make_scene(), {"sid": "s5"})
    assert "timeline" in result["html"], "timeline class not found in html"


def test_build_html_contains_timeline_rail_fx_class():
    """html must contain 'timeline-rail-fx' (carries the 'timeline-rail' literal for static signature match)."""
    import studio.compose.archetypes.timeline as tl_mod
    result = tl_mod.build(_make_scene(), {"sid": "s5"})
    assert "timeline-rail-fx" in result["html"], "timeline-rail-fx class not found in html"


def test_build_html_contains_rail_line():
    """html must contain the 'rail-line' class."""
    import studio.compose.archetypes.timeline as tl_mod
    result = tl_mod.build(_make_scene(), {"sid": "s5"})
    assert "rail-line" in result["html"], "rail-line class not found in html"


def test_build_html_contains_at_least_two_rail_nodes():
    """html must contain at least 2 .rail-node elements."""
    import studio.compose.archetypes.timeline as tl_mod
    # '2004 / 2010 / 2020' splits to 3 nodes
    result = tl_mod.build(_make_scene("2004 / 2010 / 2020"), {"sid": "s5"})
    # Use exact class match to avoid counting the rail-nodes container div
    count = len(re.findall(r'class="rail-node"', result["html"]))
    assert count >= 2, f"Expected >=2 rail-node elements, got {count}"


def test_build_html_contains_labels():
    """html must contain the item labels derived from on_screen_text."""
    import studio.compose.archetypes.timeline as tl_mod
    result = tl_mod.build(_make_scene("2004 / 2010 / 2020"), {"sid": "s5"})
    assert "2004" in result["html"], "Label '2004' not found in html"
    assert "2010" in result["html"], "Label '2010' not found in html"
    assert "2020" in result["html"], "Label '2020' not found in html"


# === 5. Beats call + anchor ==================================================

def test_beats_js_calls_make_timeline_rail():
    """beats_js must invoke makeTimelineRail(."""
    import studio.compose.archetypes.timeline as tl_mod
    result = tl_mod.build(_make_scene(), {"sid": "s5"})
    assert "makeTimelineRail(" in result["beats_js"], (
        "beats_js does not call makeTimelineRail"
    )


def test_beats_js_anchored_at_ctx_at():
    """beats_js must embed ctx['at'] as the anchor."""
    import studio.compose.archetypes.timeline as tl_mod
    scene = _make_scene()
    ctx = {"sid": "s5", "spray": "#2e5e1f", "at": 18.3}
    result = tl_mod.build(scene, ctx)
    assert "18.3" in result["beats_js"], (
        f"Expected ctx['at']=18.3 in beats_js but not found.\n"
        f"beats_js:\n{result['beats_js']}"
    )


def test_beats_js_default_anchor_is_0_6():
    """When ctx has no 'at', the default fallback is 0.6."""
    import studio.compose.archetypes.timeline as tl_mod
    result = tl_mod.build(_make_scene(), {"sid": "s5"})
    assert "0.6" in result["beats_js"], (
        f"Expected default anchor 0.6 in beats_js when ctx has no 'at'.\n{result['beats_js']}"
    )


def test_beats_js_sid_scoped():
    """beats_js must scope the mount selector to the scene sid."""
    import studio.compose.archetypes.timeline as tl_mod
    ctx = {"sid": "s11", "spray": "#2e5e1f", "at": 5.0}
    result = tl_mod.build(_make_scene(), ctx)
    assert "s11" in result["beats_js"], (
        "sid 's11' not found in beats_js — mount selector must include the sid"
    )


# === 6. on_screen_text split yields correct nodes ============================

def test_three_nodes_from_slash_split():
    """on_screen_text '2004 / 2010 / 2020' must yield exactly 3 rail-nodes."""
    import studio.compose.archetypes.timeline as tl_mod
    result = tl_mod.build(_make_scene("2004 / 2010 / 2020"), {"sid": "s5"})
    # Count class="rail-node" occurrences (not rail-nodes which is the container)
    count = len(re.findall(r'class="rail-node"', result["html"]))
    assert count == 3, (
        f"Expected 3 rail-node elements from '2004 / 2010 / 2020', got {count}"
    )


def test_labels_from_slash_split():
    """Labels '2004', '2010', '2020' must all appear in html."""
    import studio.compose.archetypes.timeline as tl_mod
    result = tl_mod.build(_make_scene("2004 / 2010 / 2020"), {"sid": "s5"})
    for label in ("2004", "2010", "2020"):
        assert label in result["html"], f"Label '{label}' not found in html"


def test_bullets_list_takes_priority():
    """scene['bullets'] must be used when present, over on_screen_text."""
    import studio.compose.archetypes.timeline as tl_mod
    scene = _make_scene(
        on_screen_text="IGNORED / ALSO IGNORED",
        bullets=["Alpha", "Beta", "Gamma"],
    )
    result = tl_mod.build(scene, {"sid": "s5"})
    assert "Alpha" in result["html"], "Bullet 'Alpha' not found in html"
    assert "Beta" in result["html"], "Bullet 'Beta' not found in html"
    assert "Gamma" in result["html"], "Bullet 'Gamma' not found in html"


def test_point_fallback():
    """When on_screen_text is empty, scene['point'] is used as a single node."""
    import studio.compose.archetypes.timeline as tl_mod
    scene = {"scene_no": 5, "on_screen_text": "", "point": "Key Event", "claims": []}
    result = tl_mod.build(scene, {"sid": "s5"})
    assert "Key Event" in result["html"], "Fallback point 'Key Event' not found in html"


def test_dash_fallback():
    """When scene has no usable text, a single '—' node is emitted."""
    import studio.compose.archetypes.timeline as tl_mod
    scene = {"scene_no": 5, "claims": []}
    result = tl_mod.build(scene, {"sid": "s5"})
    assert "—" in result["html"], "Fallback '—' not found in html"


def test_nodes_capped_at_5():
    """Items must be capped at 5 regardless of how many are in on_screen_text."""
    import studio.compose.archetypes.timeline as tl_mod
    ost = "A / B / C / D / E / F / G"
    result = tl_mod.build(_make_scene(ost), {"sid": "s5"})
    # Use exact class match to avoid counting the rail-nodes container div
    count = len(re.findall(r'class="rail-node"', result["html"]))
    assert count <= 5, f"Expected <=5 rail-nodes, got {count}"


# === 6b. signature ============================================================

def test_scene_signature_returns_timeline_rail():
    """scene_signature must return 'timeline-rail' for a timeline scene output."""
    import studio.compose.archetypes.timeline as tl_mod
    from studio.gate.parse import scene_signature

    scene = _make_scene()
    sid = "s5"
    ctx = {"sid": sid, "spray": "#2e5e1f"}
    result = tl_mod.build(scene, ctx)

    sig = scene_signature(result["html"], result["beats_js"], sid)
    assert sig == "timeline-rail", (
        f"Expected scene_signature == 'timeline-rail' but got {sig!r}. "
        f"Check that beats_js contains 'makeTimelineRail' or html contains 'rail-node'."
    )


def test_scene_signature_not_plain():
    """Explicit guard: the signature must never fall back to 'plain'."""
    import studio.compose.archetypes.timeline as tl_mod
    from studio.gate.parse import scene_signature

    result = tl_mod.build(_make_scene(), {"sid": "s5"})
    sig = scene_signature(result["html"], result["beats_js"], "s5")
    assert sig != "plain", (
        "scene_signature fell back to 'plain' — the gate cannot distinguish this archetype"
    )


# === 7. Parity regression ====================================================

def test_parity_invariant_still_holds():
    """Every registered archetype's token must be in _BEAT_TOKENS (the parity invariant)."""
    import studio.compose.archetypes.timeline  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    from studio.gate import parse as P

    token_names = {name for name, _pat in P._BEAT_TOKENS}
    for arch in A.REGISTRY:
        tok = A.token_for(arch)
        assert tok in token_names, (
            f"Parity broken: archetype {arch!r} emits token {tok!r} "
            f"not present in _BEAT_TOKENS"
        )


def test_timeline_in_closed_vocab():
    """'timeline' must be in the closed ARCHETYPES vocab."""
    from studio.compose import archetypes as A
    assert "timeline" in A.ARCHETYPES, "'timeline' not in ARCHETYPES closed vocab"
