"""TDD tests for studio/compose/archetypes/data_chart.py — Task C4.

Tests:
  1. registration + parity: 'data-chart' in REGISTRY; token_for == 'calendar-crumble';
     'calendar-crumble' in _BEAT_TOKENS names.
  2. determinism: builder html+beats_js and the _motion.CALENDAR_CRUMBLE factory contain
     none of the banned primitives (strip /* */ comments before scanning; Math index
     arithmetic and % are allowed; there is no Math.random).
  3. keys + token: html, beats_js, token in result; token == 'calendar-crumble'.
  4. content: html contains 'data-chart', 'calendar-mount', and the label.
  5. beats call + anchor: beats_js contains 'makeCalendarCrumble('; ctx={"at":18.3} -> 18.3
     in beats_js.
  6. signature: scene_signature(html, beats_js, sid) == 'calendar-crumble' (not 'plain',
     and crucially NOT 'shatter' — proves the ordering fix).
  7. ordering regression: scene_signature("<div>shatter shards</div>", "", "s9") == 'shatter'
     (a genuine shatter scene is still detected; the new entry didn't steal it).
  8. parity regression over all REGISTRY archetypes.
"""
from __future__ import annotations

import re


# --- helpers -----------------------------------------------------------------

def _make_scene(on_screen_text="141 DAYS LOST", point="days lost to outages"):
    return {
        "scene_no": 5,
        "on_screen_text": on_screen_text,
        "point": point,
        "narration": "141 days of productivity lost each year.",
        "duration_est_sec": 9,
        "claims": [],
    }


# === 1. Registration + token parity ==========================================

def test_data_chart_is_registered():
    """data_chart.py must call register() so 'data-chart' appears in REGISTRY."""
    import studio.compose.archetypes.data_chart  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    assert "data-chart" in A.REGISTRY, "'data-chart' not found in archetypes.REGISTRY"


def test_data_chart_token_for_returns_calendar_crumble():
    """token_for('data-chart') must return 'calendar-crumble'."""
    from studio.compose import archetypes as A
    assert A.token_for("data-chart") == "calendar-crumble", (
        f"token_for('data-chart') returned {A.token_for('data-chart')!r}, "
        f"expected 'calendar-crumble'"
    )


def test_calendar_crumble_token_in_beat_tokens():
    """The 'calendar-crumble' token must be present in gate.parse._BEAT_TOKENS (parity invariant)."""
    from studio.gate import parse as P
    token_names = {name for name, _pat in P._BEAT_TOKENS}
    assert "calendar-crumble" in token_names, (
        "'calendar-crumble' not found in gate.parse._BEAT_TOKENS — parity broken"
    )


# === 2. Determinism ==========================================================

def test_build_output_has_no_banned_primitives():
    """html + beats_js must not contain Math.random/Date.now/new Date/fetch/XMLHttpRequest."""
    import studio.compose.archetypes.data_chart as dc
    scene = _make_scene()
    ctx = {"sid": "s5", "spray": "#2e5e1f", "ink": "#1f1f1e"}
    result = dc.build(scene, ctx)
    combined = result["html"] + result["beats_js"]
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bfetch\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(combined), (
        f"Banned non-deterministic primitive found in build() output: "
        f"{banned.findall(combined)}"
    )


def test_calendar_crumble_factory_string_has_no_banned_primitives():
    """_motion.CALENDAR_CRUMBLE factory executable code must not contain any banned
    primitives. Math index arithmetic and % are allowed. Strip /* */ comments first."""
    from studio.compose import _motion
    assert hasattr(_motion, "CALENDAR_CRUMBLE"), (
        "_motion.CALENDAR_CRUMBLE not found — add the factory string to _motion.py"
    )
    # Strip block comments before scanning
    code = re.sub(r"/\*.*?\*/", "", _motion.CALENDAR_CRUMBLE, flags=re.DOTALL)
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(code), (
        f"Banned primitive in _motion.CALENDAR_CRUMBLE executable code: "
        f"{banned.findall(code)}"
    )
    # fetch() call pattern
    fetch_call = re.compile(r"\bfetch\s*\(")
    assert not fetch_call.search(code), (
        "fetch() call found in _motion.CALENDAR_CRUMBLE executable code"
    )


def test_calendar_crumble_factory_uses_index_arithmetic():
    """The factory must use deterministic index arithmetic (i*53, i*31, i*47) — no Math.random."""
    from studio.compose import _motion
    assert "53" in _motion.CALENDAR_CRUMBLE, "Expected index multiplier 53 in CALENDAR_CRUMBLE"
    assert "31" in _motion.CALENDAR_CRUMBLE, "Expected index multiplier 31 in CALENDAR_CRUMBLE"
    assert "47" in _motion.CALENDAR_CRUMBLE, "Expected index multiplier 47 in CALENDAR_CRUMBLE"
    assert "Math.random" not in _motion.CALENDAR_CRUMBLE, (
        "Math.random found in CALENDAR_CRUMBLE — must be deterministic"
    )


# === 3. Required keys + token ================================================

def test_build_returns_required_keys():
    """build() must return dict with html, beats_js, token."""
    import studio.compose.archetypes.data_chart as dc
    result = dc.build(_make_scene(), {"sid": "s5"})
    assert "html" in result, "Missing 'html' key in build() result"
    assert "beats_js" in result, "Missing 'beats_js' key in build() result"
    assert "token" in result, "Missing 'token' key in build() result"


def test_build_token_is_calendar_crumble():
    """build() must return token == 'calendar-crumble'."""
    import studio.compose.archetypes.data_chart as dc
    result = dc.build(_make_scene(), {"sid": "s5"})
    assert result["token"] == "calendar-crumble", (
        f"Expected token 'calendar-crumble', got {result['token']!r}"
    )


# === 4. Content ==============================================================

def test_build_html_contains_data_chart_class():
    """html must contain the 'data-chart' class."""
    import studio.compose.archetypes.data_chart as dc
    result = dc.build(_make_scene(), {"sid": "s5"})
    assert "data-chart" in result["html"], "data-chart class not found in html"


def test_build_html_contains_calendar_mount():
    """html must contain the 'calendar-mount' class for the grid mount point."""
    import studio.compose.archetypes.data_chart as dc
    result = dc.build(_make_scene(), {"sid": "s5"})
    assert "calendar-mount" in result["html"], "calendar-mount class not found in html"


def test_build_html_contains_label():
    """html must contain the label derived from scene['point'] (upper-cased, ≤28 chars)."""
    import studio.compose.archetypes.data_chart as dc
    result = dc.build(_make_scene(point="days lost to outages"), {"sid": "s5"})
    # label should be upper-cased from point
    assert "DAYS LOST" in result["html"].upper(), (
        f"Label from scene['point'] not found in html. Got:\n{result['html']}"
    )


def test_build_html_default_label_fallback():
    """html must default to 'DAYS LOST' when scene has no point or on_screen_text."""
    import studio.compose.archetypes.data_chart as dc
    scene = {"scene_no": 5, "duration_est_sec": 9, "claims": []}
    result = dc.build(scene, {"sid": "s5"})
    assert "DAYS LOST" in result["html"], (
        f"Default label 'DAYS LOST' not found in html. Got:\n{result['html']}"
    )


def test_build_html_label_capped_at_28_chars():
    """html label must be capped at 28 characters."""
    import studio.compose.archetypes.data_chart as dc
    long_point = "this is a very long label that exceeds the 28 char limit"
    result = dc.build(_make_scene(point=long_point), {"sid": "s5"})
    # Find the label text (it's in the <small> tag)
    label_m = re.search(r'<small[^>]*>([^<]+)</small>', result["html"])
    assert label_m, "Could not find <small> label in html"
    label_text = label_m.group(1)
    assert len(label_text) <= 28, (
        f"Label '{label_text}' exceeds 28 chars (len={len(label_text)})"
    )


# === 5. Beats call + anchor ==================================================

def test_beats_js_calls_make_calendar_crumble():
    """beats_js must invoke makeCalendarCrumble(."""
    import studio.compose.archetypes.data_chart as dc
    result = dc.build(_make_scene(), {"sid": "s5"})
    assert "makeCalendarCrumble(" in result["beats_js"], (
        "beats_js does not call makeCalendarCrumble"
    )


def test_beats_js_anchored_at_ctx_at():
    """beats_js must embed ctx['at'] as the anchor."""
    import studio.compose.archetypes.data_chart as dc
    scene = _make_scene()
    ctx = {"sid": "s5", "spray": "#2e5e1f", "at": 18.3}
    result = dc.build(scene, ctx)
    assert "18.3" in result["beats_js"], (
        f"Expected ctx['at']=18.3 in beats_js but not found.\n"
        f"beats_js:\n{result['beats_js']}"
    )


def test_beats_js_default_anchor_not_zero():
    """When ctx has no 'at', the default fallback is 0.6 (not 0)."""
    import studio.compose.archetypes.data_chart as dc
    result = dc.build(_make_scene(), {"sid": "s5"})
    assert "0.6" in result["beats_js"], (
        f"Expected default anchor 0.6 in beats_js when ctx has no 'at'.\n{result['beats_js']}"
    )


def test_beats_js_sid_scoped():
    """beats_js must scope the mount selector to the scene sid."""
    import studio.compose.archetypes.data_chart as dc
    ctx = {"sid": "s7", "spray": "#2e5e1f", "at": 5.0}
    result = dc.build(_make_scene(), ctx)
    assert "s7" in result["beats_js"], (
        "sid 's7' not found in beats_js — mount selector must include the sid"
    )


# === 6. scene_signature ======================================================

def test_scene_signature_returns_calendar_crumble():
    """scene_signature must return 'calendar-crumble' for a data-chart scene output."""
    import studio.compose.archetypes.data_chart as dc
    from studio.gate.parse import scene_signature

    scene = _make_scene()
    sid = "s5"
    ctx = {"sid": sid, "spray": "#2e5e1f"}
    result = dc.build(scene, ctx)

    sig = scene_signature(result["html"], result["beats_js"], sid)
    assert sig == "calendar-crumble", (
        f"Expected scene_signature == 'calendar-crumble' but got {sig!r}. "
        f"Check that beats_js contains 'makeCalendarCrumble' or html contains "
        f"'calendar-crumble'/'calendar-grid'."
    )


def test_scene_signature_not_plain():
    """Explicit guard: the signature must never fall back to 'plain'."""
    import studio.compose.archetypes.data_chart as dc
    from studio.gate.parse import scene_signature

    result = dc.build(_make_scene(), {"sid": "s5"})
    sig = scene_signature(result["html"], result["beats_js"], "s5")
    assert sig != "plain", (
        "scene_signature fell back to 'plain' — the gate cannot distinguish this archetype"
    )


def test_scene_signature_not_shatter():
    """Critical ordering test: data-chart must NOT be mislabelled 'shatter' due to
    'crumble' matching the old shatter regex. The calendar-crumble entry must be
    BEFORE shatter in _BEAT_TOKENS."""
    import studio.compose.archetypes.data_chart as dc
    from studio.gate.parse import scene_signature

    result = dc.build(_make_scene(), {"sid": "s5"})
    sig = scene_signature(result["html"], result["beats_js"], "s5")
    assert sig != "shatter", (
        "data-chart scene was mislabelled 'shatter' — 'calendar-crumble' entry must come "
        "BEFORE 'shatter' in gate.parse._BEAT_TOKENS (first-match wins ordering)."
    )


# === 7. Ordering regression ==================================================

def test_shatter_still_detected_after_calendar_crumble_insertion():
    """A genuine shatter scene (text containing only 'shatter') must still be detected
    as 'shatter' — the calendar-crumble entry must not steal it."""
    from studio.gate.parse import scene_signature
    sig = scene_signature("<div>shatter shards</div>", "", "s9")
    assert sig == "shatter", (
        f"Ordering regression: a plain shatter scene returned {sig!r} instead of 'shatter'. "
        f"The calendar-crumble regex must be SPECIFIC (not match bare 'shatter')."
    )


def test_calendar_crumble_regex_does_not_match_bare_shatter():
    """The calendar-crumble regex must NOT match bare 'shatter' text."""
    from studio.gate import parse as P
    # Find the calendar-crumble entry
    cc_pat = None
    for name, pat in P._BEAT_TOKENS:
        if name == "calendar-crumble":
            cc_pat = pat
            break
    assert cc_pat is not None, "calendar-crumble not found in _BEAT_TOKENS"
    # bare 'shatter' must NOT match calendar-crumble
    assert not re.search(cc_pat, "shatter shards", re.IGNORECASE), (
        f"calendar-crumble regex {cc_pat!r} incorrectly matches bare 'shatter' text. "
        f"The regex must be specific (only match calendar-crumble markers)."
    )


def test_calendar_crumble_before_shatter_in_beat_tokens():
    """calendar-crumble entry must appear BEFORE shatter in _BEAT_TOKENS list."""
    from studio.gate import parse as P
    names = [name for name, _pat in P._BEAT_TOKENS]
    assert "calendar-crumble" in names, "calendar-crumble not in _BEAT_TOKENS"
    assert "shatter" in names, "shatter not in _BEAT_TOKENS"
    cc_idx = names.index("calendar-crumble")
    shatter_idx = names.index("shatter")
    assert cc_idx < shatter_idx, (
        f"calendar-crumble (pos {cc_idx}) must come BEFORE shatter (pos {shatter_idx}) "
        f"in _BEAT_TOKENS. First-match wins."
    )


# === 8. Parity regression ====================================================

def test_parity_invariant_still_holds():
    """Every registered archetype's token must be in _BEAT_TOKENS (the parity invariant)."""
    import studio.compose.archetypes.data_chart  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    from studio.gate import parse as P

    token_names = {name for name, _pat in P._BEAT_TOKENS}
    for arch in A.REGISTRY:
        tok = A.token_for(arch)
        assert tok in token_names, (
            f"Parity broken: archetype {arch!r} emits token {tok!r} "
            f"not present in _BEAT_TOKENS"
        )


def test_data_chart_in_closed_vocab():
    """'data-chart' must be in the closed ARCHETYPES vocab."""
    from studio.compose import archetypes as A
    assert "data-chart" in A.ARCHETYPES, "'data-chart' not in ARCHETYPES closed vocab"
