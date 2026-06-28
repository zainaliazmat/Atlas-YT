"""TDD tests for studio/compose/archetypes/diagram.py — Task C13.

Tests:
  1. Registration + parity: 'diagram' in REGISTRY; token_for == 'diagram-draw';
     'diagram-draw' in _BEAT_TOKENS names.
  2. Determinism: builder html+beats_js and the _motion.DIAGRAM_DRAW factory contain
     none of the banned primitives (strip /* */ comments before scanning; no RNG/Date).
  3. Keys + token: html, beats_js, token in result; token == 'diagram-draw'.
  4. Content: html contains 'diagram', 'diagram-draw-fx', >=2 'diagram-node', >=1
     'diagram-edge' with pathLength="1", the labels;
     beats call makeDiagramDraw( + ctx={"at":18.3}->18.3.
  5. Signature: scene_signature(html, beats_js, sid) == 'diagram-draw' not 'plain'.
  6. on_screen_text 'DATA / MODEL / RESULT' yields 3 diagram-nodes and 2 diagram-edges.
  7. Parity regression over all REGISTRY archetypes.
"""
from __future__ import annotations

import re


# --- helpers -----------------------------------------------------------------

def _make_scene(on_screen_text="DATA / MODEL / RESULT", bullets=None):
    return {
        "scene_no": 7,
        "on_screen_text": on_screen_text,
        "narration": "A conceptual diagram of the system.",
        "duration_est_sec": 9,
        "claims": [],
        **({"bullets": bullets} if bullets is not None else {}),
    }


# === 1. Registration + token parity ==========================================

def test_diagram_is_registered():
    """diagram.py must call register() so 'diagram' appears in REGISTRY."""
    import studio.compose.archetypes.diagram  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    assert "diagram" in A.REGISTRY, "'diagram' not found in archetypes.REGISTRY"


def test_diagram_token_for_returns_diagram_draw():
    """token_for('diagram') must return 'diagram-draw'."""
    from studio.compose import archetypes as A
    assert A.token_for("diagram") == "diagram-draw", (
        f"token_for('diagram') returned {A.token_for('diagram')!r}, "
        f"expected 'diagram-draw'"
    )


def test_diagram_draw_token_in_beat_tokens():
    """The 'diagram-draw' token must be present in gate.parse._BEAT_TOKENS (parity invariant)."""
    from studio.gate import parse as P
    token_names = {name for name, _pat in P._BEAT_TOKENS}
    assert "diagram-draw" in token_names, (
        "'diagram-draw' not found in gate.parse._BEAT_TOKENS — parity broken"
    )


# === 2. Determinism ==========================================================

def test_build_output_has_no_banned_primitives():
    """html + beats_js must not contain Math.random/Date.now/new Date/fetch/XMLHttpRequest."""
    import studio.compose.archetypes.diagram as diag_mod
    scene = _make_scene()
    ctx = {"sid": "s7", "spray": "#2e5e1f", "ink": "#1f1f1e"}
    result = diag_mod.build(scene, ctx)
    combined = result["html"] + result["beats_js"]
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bfetch\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(combined), (
        f"Banned non-deterministic primitive found in build() output: "
        f"{banned.findall(combined)}"
    )


def test_diagram_draw_factory_string_has_no_banned_primitives():
    """_motion.DIAGRAM_DRAW factory executable code must not contain any banned
    primitives. Strip /* */ comments first."""
    from studio.compose import _motion
    assert hasattr(_motion, "DIAGRAM_DRAW"), (
        "_motion.DIAGRAM_DRAW not found — add the factory string to _motion.py"
    )
    # Strip block comments before scanning
    code = re.sub(r"/\*.*?\*/", "", _motion.DIAGRAM_DRAW, flags=re.DOTALL)
    banned = re.compile(
        r"\bMath\.random\b|\bDate\.now\b|\bnew Date\b|\bXMLHttpRequest\b"
    )
    assert not banned.search(code), (
        f"Banned primitive in _motion.DIAGRAM_DRAW executable code: "
        f"{banned.findall(code)}"
    )
    # fetch() call pattern
    fetch_call = re.compile(r"\bfetch\s*\(")
    assert not fetch_call.search(code), (
        "fetch() call found in _motion.DIAGRAM_DRAW executable code"
    )


def test_diagram_draw_factory_no_math_random():
    """The factory must not use Math.random — no RNG anywhere."""
    from studio.compose import _motion
    assert "Math.random" not in _motion.DIAGRAM_DRAW, (
        "Math.random found in DIAGRAM_DRAW — must be deterministic"
    )


# === 3. Required keys + token ================================================

def test_build_returns_required_keys():
    """build() must return dict with html, beats_js, token."""
    import studio.compose.archetypes.diagram as diag_mod
    result = diag_mod.build(_make_scene(), {"sid": "s7"})
    assert "html" in result, "Missing 'html' key in build() result"
    assert "beats_js" in result, "Missing 'beats_js' key in build() result"
    assert "token" in result, "Missing 'token' key in build() result"


def test_build_token_is_diagram_draw():
    """build() must return token == 'diagram-draw'."""
    import studio.compose.archetypes.diagram as diag_mod
    result = diag_mod.build(_make_scene(), {"sid": "s7"})
    assert result["token"] == "diagram-draw", (
        f"Expected token 'diagram-draw', got {result['token']!r}"
    )


# === 4. Content ==============================================================

def test_build_html_contains_diagram_class():
    """html must contain the 'diagram' class."""
    import studio.compose.archetypes.diagram as diag_mod
    result = diag_mod.build(_make_scene(), {"sid": "s7"})
    assert "diagram" in result["html"], "diagram class not found in html"


def test_build_html_contains_diagram_draw_fx_class():
    """html must contain 'diagram-draw-fx' (carries the 'diagram-draw' literal for static signature match)."""
    import studio.compose.archetypes.diagram as diag_mod
    result = diag_mod.build(_make_scene(), {"sid": "s7"})
    assert "diagram-draw-fx" in result["html"], "diagram-draw-fx class not found in html"


def test_build_html_contains_at_least_two_diagram_nodes():
    """html must contain at least 2 .diagram-node elements."""
    import studio.compose.archetypes.diagram as diag_mod
    result = diag_mod.build(_make_scene("DATA / MODEL / RESULT"), {"sid": "s7"})
    # Use exact class match to avoid container over-counts
    count = len(re.findall(r'class="diagram-node"', result["html"]))
    assert count >= 2, f"Expected >=2 diagram-node elements, got {count}"


def test_build_html_contains_at_least_one_diagram_edge():
    """html must contain at least 1 .diagram-edge element with pathLength='1'."""
    import studio.compose.archetypes.diagram as diag_mod
    result = diag_mod.build(_make_scene("DATA / MODEL / RESULT"), {"sid": "s7"})
    assert "diagram-edge" in result["html"], "diagram-edge class not found in html"
    assert 'pathLength="1"' in result["html"], 'pathLength="1" not found in html'


def test_build_html_contains_labels():
    """html must contain the item labels derived from on_screen_text."""
    import studio.compose.archetypes.diagram as diag_mod
    result = diag_mod.build(_make_scene("DATA / MODEL / RESULT"), {"sid": "s7"})
    assert "DATA" in result["html"], "Label 'DATA' not found in html"
    assert "MODEL" in result["html"], "Label 'MODEL' not found in html"
    assert "RESULT" in result["html"], "Label 'RESULT' not found in html"


# === 5. Beats call + anchor ==================================================

def test_beats_js_calls_make_diagram_draw():
    """beats_js must invoke makeDiagramDraw(."""
    import studio.compose.archetypes.diagram as diag_mod
    result = diag_mod.build(_make_scene(), {"sid": "s7"})
    assert "makeDiagramDraw(" in result["beats_js"], (
        "beats_js does not call makeDiagramDraw"
    )


def test_beats_js_anchored_at_ctx_at():
    """beats_js must embed ctx['at'] as the anchor."""
    import studio.compose.archetypes.diagram as diag_mod
    scene = _make_scene()
    ctx = {"sid": "s7", "spray": "#2e5e1f", "at": 18.3}
    result = diag_mod.build(scene, ctx)
    assert "18.3" in result["beats_js"], (
        f"Expected ctx['at']=18.3 in beats_js but not found.\n"
        f"beats_js:\n{result['beats_js']}"
    )


def test_beats_js_default_anchor_is_0_6():
    """When ctx has no 'at', the default fallback is 0.6."""
    import studio.compose.archetypes.diagram as diag_mod
    result = diag_mod.build(_make_scene(), {"sid": "s7"})
    assert "0.6" in result["beats_js"], (
        f"Expected default anchor 0.6 in beats_js when ctx has no 'at'.\n{result['beats_js']}"
    )


def test_beats_js_sid_scoped():
    """beats_js must scope the mount selector to the scene sid."""
    import studio.compose.archetypes.diagram as diag_mod
    ctx = {"sid": "s11", "spray": "#2e5e1f", "at": 5.0}
    result = diag_mod.build(_make_scene(), ctx)
    assert "s11" in result["beats_js"], (
        "sid 's11' not found in beats_js — mount selector must include the sid"
    )


# === 5b. Signature ===========================================================

def test_scene_signature_returns_diagram_draw():
    """scene_signature must return 'diagram-draw' for a diagram scene output."""
    import studio.compose.archetypes.diagram as diag_mod
    from studio.gate.parse import scene_signature

    scene = _make_scene()
    sid = "s7"
    ctx = {"sid": sid, "spray": "#2e5e1f"}
    result = diag_mod.build(scene, ctx)

    sig = scene_signature(result["html"], result["beats_js"], sid)
    assert sig == "diagram-draw", (
        f"Expected scene_signature == 'diagram-draw' but got {sig!r}. "
        f"Check that beats_js contains 'makeDiagramDraw' or html contains 'diagram-node'."
    )


def test_scene_signature_not_plain():
    """Explicit guard: the signature must never fall back to 'plain'."""
    import studio.compose.archetypes.diagram as diag_mod
    from studio.gate.parse import scene_signature

    result = diag_mod.build(_make_scene(), {"sid": "s7"})
    sig = scene_signature(result["html"], result["beats_js"], "s7")
    assert sig != "plain", (
        "scene_signature fell back to 'plain' — the gate cannot distinguish this archetype"
    )


# === 6. on_screen_text split yields correct nodes/edges ======================

def test_three_nodes_from_slash_split():
    """on_screen_text 'DATA / MODEL / RESULT' must yield exactly 3 diagram-nodes."""
    import studio.compose.archetypes.diagram as diag_mod
    result = diag_mod.build(_make_scene("DATA / MODEL / RESULT"), {"sid": "s7"})
    count = len(re.findall(r'class="diagram-node"', result["html"]))
    assert count == 3, (
        f"Expected 3 diagram-node elements from 'DATA / MODEL / RESULT', got {count}"
    )


def test_two_edges_for_three_nodes():
    """N nodes must yield N-1 edges: 3 nodes -> 2 diagram-edge paths."""
    import studio.compose.archetypes.diagram as diag_mod
    result = diag_mod.build(_make_scene("DATA / MODEL / RESULT"), {"sid": "s7"})
    count = len(re.findall(r'class="diagram-edge"', result["html"]))
    assert count == 2, (
        f"Expected 2 diagram-edge elements for 3 nodes, got {count}"
    )


def test_n_minus_one_edges_invariant():
    """For any N nodes there must be exactly N-1 edges (the chain invariant)."""
    import studio.compose.archetypes.diagram as diag_mod
    # 4 nodes -> 3 edges
    result = diag_mod.build(_make_scene("A / B / C / D"), {"sid": "s7"})
    node_count = len(re.findall(r'class="diagram-node"', result["html"]))
    edge_count = len(re.findall(r'class="diagram-edge"', result["html"]))
    assert node_count == 4, f"Expected 4 nodes for 'A / B / C / D', got {node_count}"
    assert edge_count == node_count - 1, (
        f"Expected {node_count - 1} edges for {node_count} nodes, got {edge_count}"
    )


def test_labels_from_slash_split():
    """Labels 'DATA', 'MODEL', 'RESULT' must all appear in html."""
    import studio.compose.archetypes.diagram as diag_mod
    result = diag_mod.build(_make_scene("DATA / MODEL / RESULT"), {"sid": "s7"})
    for label in ("DATA", "MODEL", "RESULT"):
        assert label in result["html"], f"Label '{label}' not found in html"


def test_default_labels_when_no_text():
    """When scene has no usable text, default labels (INPUT, MODEL, OUTPUT) must be emitted."""
    import studio.compose.archetypes.diagram as diag_mod
    scene = {"scene_no": 7, "claims": []}
    result = diag_mod.build(scene, {"sid": "s7"})
    assert "INPUT" in result["html"], "Default label 'INPUT' not found in html"
    assert "MODEL" in result["html"], "Default label 'MODEL' not found in html"
    assert "OUTPUT" in result["html"], "Default label 'OUTPUT' not found in html"


def test_nodes_capped_at_4():
    """Items must be capped at 4 regardless of how many are in on_screen_text."""
    import studio.compose.archetypes.diagram as diag_mod
    ost = "A / B / C / D / E / F"
    result = diag_mod.build(_make_scene(ost), {"sid": "s7"})
    count = len(re.findall(r'class="diagram-node"', result["html"]))
    assert count <= 4, f"Expected <=4 diagram-nodes, got {count}"


def test_bullets_list_takes_priority():
    """scene['bullets'] must be used when present, over on_screen_text."""
    import studio.compose.archetypes.diagram as diag_mod
    scene = _make_scene(
        on_screen_text="IGNORED / ALSO IGNORED",
        bullets=["Alpha", "Beta", "Gamma"],
    )
    result = diag_mod.build(scene, {"sid": "s7"})
    assert "Alpha" in result["html"], "Bullet 'Alpha' not found in html"
    assert "Beta" in result["html"], "Bullet 'Beta' not found in html"
    assert "Gamma" in result["html"], "Bullet 'Gamma' not found in html"


# === 7. Parity regression ====================================================

def test_parity_invariant_still_holds():
    """Every registered archetype's token must be in _BEAT_TOKENS (the parity invariant)."""
    import studio.compose.archetypes.diagram  # noqa: F401 — triggers register()
    from studio.compose import archetypes as A
    from studio.gate import parse as P

    token_names = {name for name, _pat in P._BEAT_TOKENS}
    for arch in A.REGISTRY:
        tok = A.token_for(arch)
        assert tok in token_names, (
            f"Parity broken: archetype {arch!r} emits token {tok!r} "
            f"not present in _BEAT_TOKENS"
        )


def test_diagram_in_closed_vocab():
    """'diagram' must be in the closed ARCHETYPES vocab."""
    from studio.compose import archetypes as A
    assert "diagram" in A.ARCHETYPES, "'diagram' not in ARCHETYPES closed vocab"
