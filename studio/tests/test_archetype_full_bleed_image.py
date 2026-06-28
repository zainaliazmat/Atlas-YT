"""TDD tests for studio/compose/archetypes/full_bleed_image.py — Task C7.

Tests:
  1. registration + parity: 'full-bleed-image' in REGISTRY; token_for == 'device-loop';
     'device-loop' in _BEAT_TOKENS names.
  2. determinism: builder html+beats_js and _motion.DEVICE_LOOP factory — no banned
     primitives (strip /* */ comments first; index arithmetic / String(j % 10) are fine).
  3. keys + token 'device-loop'.
  4. content: html contains 'full-bleed-image', 'device-loop-fx', 'device-mount'.
  5. beats call + anchor: beats_js contains 'makeDeviceLoop('; ctx={"at":18.3} -> 18.3.
  6. signature: scene_signature(html, beats_js, sid) == 'device-loop' (not 'plain').
  7. parity regression over all REGISTRY archetypes.
"""
from __future__ import annotations

import re


# --- helpers -----------------------------------------------------------------

def _make_scene():
    return {
        "scene_no": 7,
        "on_screen_text": "THE PRODUCT HAPPENS TO YOU",
        "narration": "You scroll, you flick, you never stop.",
        "duration_est_sec": 9,
        "claims": [],
    }


# === 1. Registration + token parity ==========================================

def test_full_bleed_image_is_registered():
    """full_bleed_image.py must call register() so 'full-bleed-image' appears in REGISTRY."""
    import studio.compose.archetypes.full_bleed_image  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    assert "full-bleed-image" in A.REGISTRY, (
        "'full-bleed-image' not found in archetypes.REGISTRY"
    )


def test_full_bleed_image_token_for_returns_device_loop():
    """token_for('full-bleed-image') must return 'device-loop'."""
    from studio.compose import archetypes as A
    tok = A.token_for("full-bleed-image")
    assert tok == "device-loop", (
        f"token_for('full-bleed-image') returned {tok!r}, expected 'device-loop'"
    )


def test_device_loop_token_in_beat_tokens():
    """The 'device-loop' token must be present in gate.parse._BEAT_TOKENS (parity invariant)."""
    from studio.gate import parse as P
    token_names = {name for name, _pat in P._BEAT_TOKENS}
    assert "device-loop" in token_names, (
        "'device-loop' not found in gate.parse._BEAT_TOKENS — parity broken"
    )


# === 2. Determinism ==========================================================

def test_build_output_has_no_banned_primitives():
    """html + beats_js must not contain Math.random/Date.now/new Date/fetch/XMLHttpRequest."""
    import studio.compose.archetypes.full_bleed_image as fbi
    scene = _make_scene()
    ctx = {"sid": "s7", "spray": "#2e5e1f", "ink": "#1f1f1e"}
    result = fbi.build(scene, ctx)
    combined = result["html"] + result["beats_js"]
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bfetch\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(combined), (
        f"Banned non-deterministic primitive found in build() output: "
        f"{banned.findall(combined)}"
    )


def test_device_loop_factory_string_has_no_banned_primitives():
    """_motion.DEVICE_LOOP factory executable code must not contain any banned
    primitives. Strip /* */ comments first before scanning."""
    from studio.compose import _motion
    assert hasattr(_motion, "DEVICE_LOOP"), (
        "_motion.DEVICE_LOOP not found — add the factory string to _motion.py"
    )
    # Strip block comments before scanning
    code = re.sub(r"/\*.*?\*/", "", _motion.DEVICE_LOOP, flags=re.DOTALL)
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(code), (
        f"Banned primitive in _motion.DEVICE_LOOP executable code: "
        f"{banned.findall(code)}"
    )
    # fetch() call pattern
    fetch_call = re.compile(r"\bfetch\s*\(")
    assert not fetch_call.search(code), (
        "fetch() call found in _motion.DEVICE_LOOP executable code"
    )


def test_device_loop_in_beats_dict():
    """'device-loop' must be registered in _motion.BEATS."""
    from studio.compose import _motion
    assert "device-loop" in _motion.BEATS, (
        "'device-loop' not found in _motion.BEATS — add it alongside the factory"
    )


def test_device_loop_beats_entry_has_correct_factory():
    """BEATS['device-loop'] entry must reference makeDeviceLoop."""
    from studio.compose import _motion
    factory_name, filename, source = _motion.BEATS["device-loop"]
    assert factory_name == "makeDeviceLoop", (
        f"Expected factory name 'makeDeviceLoop', got {factory_name!r}"
    )
    assert filename == "device-loop.js", (
        f"Expected filename 'device-loop.js', got {filename!r}"
    )


# === 3. Required keys + token ================================================

def test_build_returns_required_keys():
    """build() must return dict with html, beats_js, token."""
    import studio.compose.archetypes.full_bleed_image as fbi
    result = fbi.build(_make_scene(), {"sid": "s7"})
    assert "html" in result, "Missing 'html' key in build() result"
    assert "beats_js" in result, "Missing 'beats_js' key in build() result"
    assert "token" in result, "Missing 'token' key in build() result"


def test_build_token_is_device_loop():
    """build() must return token == 'device-loop'."""
    import studio.compose.archetypes.full_bleed_image as fbi
    result = fbi.build(_make_scene(), {"sid": "s7"})
    assert result["token"] == "device-loop", (
        f"Expected token 'device-loop', got {result['token']!r}"
    )


# === 4. Content ==============================================================

def test_build_html_contains_full_bleed_image_class():
    """html must contain the 'full-bleed-image' class."""
    import studio.compose.archetypes.full_bleed_image as fbi
    result = fbi.build(_make_scene(), {"sid": "s7"})
    assert "full-bleed-image" in result["html"], (
        "'full-bleed-image' class not found in html"
    )


def test_build_html_contains_device_loop_fx():
    """html must contain 'device-loop-fx' (the static signature class)."""
    import studio.compose.archetypes.full_bleed_image as fbi
    result = fbi.build(_make_scene(), {"sid": "s7"})
    assert "device-loop-fx" in result["html"], (
        "'device-loop-fx' not found in html — needed for static signature match"
    )


def test_build_html_contains_device_mount():
    """html must contain 'device-mount' (the mount point for the JS factory)."""
    import studio.compose.archetypes.full_bleed_image as fbi
    result = fbi.build(_make_scene(), {"sid": "s7"})
    assert "device-mount" in result["html"], (
        "'device-mount' not found in html — it is the mount point for makeDeviceLoop"
    )


# === 5. Beats call + anchor ==================================================

def test_beats_js_calls_make_device_loop():
    """beats_js must invoke makeDeviceLoop(."""
    import studio.compose.archetypes.full_bleed_image as fbi
    result = fbi.build(_make_scene(), {"sid": "s7"})
    assert "makeDeviceLoop(" in result["beats_js"], (
        "beats_js does not call makeDeviceLoop"
    )


def test_beats_js_anchored_at_ctx_at():
    """beats_js must embed ctx['at'] as the anchor."""
    import studio.compose.archetypes.full_bleed_image as fbi
    scene = _make_scene()
    ctx = {"sid": "s7", "spray": "#2e5e1f", "at": 18.3}
    result = fbi.build(scene, ctx)
    assert "18.3" in result["beats_js"], (
        f"Expected ctx['at']=18.3 in beats_js but not found.\n"
        f"beats_js:\n{result['beats_js']}"
    )


def test_beats_js_default_anchor_is_0_6():
    """When ctx has no 'at', the default fallback is 0.6."""
    import studio.compose.archetypes.full_bleed_image as fbi
    result = fbi.build(_make_scene(), {"sid": "s7"})
    assert "0.6" in result["beats_js"], (
        f"Expected default anchor 0.6 in beats_js when ctx has no 'at'.\n{result['beats_js']}"
    )


def test_beats_js_sid_scoped():
    """beats_js must scope the mount selector to the scene sid."""
    import studio.compose.archetypes.full_bleed_image as fbi
    ctx = {"sid": "s11", "spray": "#2e5e1f", "at": 5.0}
    result = fbi.build(_make_scene(), ctx)
    assert "s11" in result["beats_js"], (
        "sid 's11' not found in beats_js — mount selector must include the sid"
    )


# === 6. scene_signature ======================================================

def test_scene_signature_returns_device_loop():
    """scene_signature must return 'device-loop' for a full-bleed-image scene output."""
    import studio.compose.archetypes.full_bleed_image as fbi
    from studio.gate.parse import scene_signature

    scene = _make_scene()
    sid = "s7"
    ctx = {"sid": sid, "spray": "#2e5e1f"}
    result = fbi.build(scene, ctx)

    sig = scene_signature(result["html"], result["beats_js"], sid)
    assert sig == "device-loop", (
        f"Expected scene_signature == 'device-loop' but got {sig!r}. "
        f"Check that html contains 'device-loop-fx'/'device-feed'/'slot-reel' "
        f"or beats_js calls 'makeDeviceLoop'."
    )


def test_scene_signature_not_plain():
    """Explicit guard: the signature must never fall back to 'plain'."""
    import studio.compose.archetypes.full_bleed_image as fbi
    from studio.gate.parse import scene_signature

    result = fbi.build(_make_scene(), {"sid": "s7"})
    sig = scene_signature(result["html"], result["beats_js"], "s7")
    assert sig != "plain", (
        "scene_signature fell back to 'plain' — the gate cannot distinguish this archetype"
    )


# === 7. Parity regression ====================================================

def test_parity_invariant_still_holds():
    """Every registered archetype's token must be in _BEAT_TOKENS (the parity invariant)."""
    import studio.compose.archetypes.full_bleed_image  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    from studio.gate import parse as P

    token_names = {name for name, _pat in P._BEAT_TOKENS}
    for arch in A.REGISTRY:
        tok = A.token_for(arch)
        assert tok in token_names, (
            f"Parity broken: archetype {arch!r} emits token {tok!r} "
            f"not present in _BEAT_TOKENS"
        )


def test_full_bleed_image_in_closed_vocab():
    """'full-bleed-image' must be in the closed ARCHETYPES vocab."""
    from studio.compose import archetypes as A
    assert "full-bleed-image" in A.ARCHETYPES, (
        "'full-bleed-image' not in ARCHETYPES closed vocab"
    )
