"""Pure-unit tests for Mason's conceptual-diagram renderer (DiagramPlan -> animated SVG).

Offline, no network/render. Covers the closed-vocab validation seam, deterministic
byte-stable output, the determinism wall (no Math.random/Date.now/SMIL), glyph coverage,
and fixed-slot flow connectivity.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import diagram_render as d  # noqa: E402


def _plan():
    return {"layout_hint": "left-to-right", "components": [
        {"id": "g", "type": "labeled-box", "label": "Goal", "of": "document"},
        {"id": "b", "type": "speech-bubble", "label": "LLM", "of": "brain", "to": ["t"]},
        {"id": "t", "type": "labeled-box", "label": "Tools", "of": "gear", "to": ["a"]},
        {"id": "a", "type": "glyph", "label": "Action", "of": "button", "emphasis": "circle"},
    ]}


# ---- closed-vocab validation -----------------------------------------
def test_valid_plan_passes_validation():
    assert d.validate_plan(_plan()) == []


def test_no_components_is_invalid():
    assert d.validate_plan({"components": []})


def test_unknown_tokens_are_rejected():
    for bad in (
        {"components": [{"id": "x", "type": "wormhole"}]},
        {"components": [{"id": "x", "type": "glyph", "of": "spaceship"}]},
        {"components": [{"id": "x", "type": "node", "emphasis": "sparkle"}]},
        {"components": [{"id": "x", "type": "node", "anim": "explode"}]},
        {"layout_hint": "isometric", "components": [{"id": "x", "type": "node"}]},
    ):
        assert d.validate_plan(bad), f"should reject {bad}"


def test_edge_to_unknown_id_is_rejected():
    bad = {"components": [{"id": "a", "type": "node", "to": ["ghost"]}]}
    assert any("unknown id" in e for e in d.validate_plan(bad))


# ---- rendering + determinism -----------------------------------------
def test_render_emits_svg_and_timeline():
    r = d.render_diagram(_plan(), seed=777)
    assert 'class="media diagram-svg"' in r["svg"]
    assert r["n"] == 4
    assert r["tl"] and any("dg-node" in t for t in r["tl"])
    assert any("strokeDashoffset" in t for t in r["tl"])   # edges draw on


def test_render_is_byte_stable_for_same_plan_and_seed():
    assert d.render_diagram(_plan(), seed=5)["svg"] == d.render_diagram(_plan(), seed=5)["svg"]


def test_render_honors_the_determinism_wall():
    r = d.render_diagram(_plan(), seed=99)
    blob = r["svg"] + " ".join(r["tl"])
    for tok in ("Math.random", "Date.now", "performance.now", "<animate",
                "setTimeout", "requestAnimationFrame", "fetch(", "repeat:-1"):
        assert tok not in blob, f"banned token {tok!r} leaked into the diagram"


def test_invalid_plan_raises():
    import pytest
    with pytest.raises(ValueError):
        d.render_diagram({"components": [{"id": "x", "type": "nope"}]}, seed=1)


def test_every_glyph_renders_a_path():
    for g in d.DIAGRAM_GLYPHS:
        plan = {"components": [{"id": "x", "type": "glyph", "of": g, "label": g}]}
        r = d.render_diagram(plan, seed=1)
        assert "dg-glyph" in r["svg"], f"glyph {g} produced no art"


def test_flow_layout_connects_consecutive_nodes():
    # a left-to-right flow with NO explicit edges still draws n-1 connectors
    plan = {"layout_hint": "left-to-right", "components": [
        {"id": "a", "type": "node", "label": "A"},
        {"id": "b", "type": "node", "label": "B"},
        {"id": "c", "type": "node", "label": "C"}]}
    r = d.render_diagram(plan, seed=1)
    # each arrow is two <path class="dg-edge ..."> (line + head); 2 gaps -> 4 edge paths
    assert r["svg"].count("dg-edge-0") == 2 and r["svg"].count("dg-edge-1") == 2
    assert "dg-edge-2" not in r["svg"]
