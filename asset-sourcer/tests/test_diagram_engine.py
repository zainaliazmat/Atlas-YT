"""Offline proof for Magpie's diagram PLANNER — NO network, NO API keys.

The LLM seam is a canned `chat_fn`, so we assert the plumbing + hard invariants:
the closed-vocab validation, the JSON+retry loop, normalization (id dedupe, cap, edge
pruning), the raise-on-failure contract, and cross-engine vocab parity with Mason's
diagram_render.py.
"""
import ast
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import diagram_engine as dg  # noqa: E402

_GOOD = {"layout_hint": "left-to-right", "components": [
    {"id": "a", "type": "speech-bubble", "label": "AI", "of": "brain", "to": ["b"]},
    {"id": "b", "type": "glyph", "label": "Action", "of": "button", "emphasis": "circle"}]}


def _canned(reply):
    return lambda system, user: reply


# ---- validation -------------------------------------------------------
def test_valid_plan_validates():
    assert dg.validate_plan(_GOOD) == []


def test_unknown_tokens_rejected():
    assert dg.validate_plan({"components": [{"id": "x", "type": "hologram"}]})
    assert dg.validate_plan({"components": [{"id": "x", "type": "glyph", "of": "ufo"}]})
    assert dg.validate_plan({"layout_hint": "spiral", "components": [{"id": "x", "type": "node"}]})


# ---- plan_diagram (canned chat_fn) -----------------------------------
def test_plan_diagram_returns_validated_plan():
    plan = dg.plan_diagram({"content": "an AI deciding an action"},
                           chat_fn=_canned(json.dumps(_GOOD)))
    assert plan["layout_hint"] == "left-to-right"
    assert [c["id"] for c in plan["components"]] == ["a", "b"]
    assert dg.validate_plan(plan) == []


def test_plan_diagram_tolerates_fenced_json():
    reply = "```json\n" + json.dumps(_GOOD) + "\n```"
    plan = dg.plan_diagram({"content": "x"}, chat_fn=_canned(reply))
    assert len(plan["components"]) == 2


def test_plan_diagram_retries_then_raises_on_garbage():
    calls = {"n": 0}

    def chat(system, user):
        calls["n"] += 1
        return "sorry, I can't do that"

    with pytest.raises(ValueError):
        dg.plan_diagram({"content": "x"}, chat_fn=chat)
    assert calls["n"] == 2          # one retry


def test_plan_diagram_recovers_on_second_attempt():
    seq = ["not json at all", json.dumps(_GOOD)]
    plan = dg.plan_diagram({"content": "x"}, chat_fn=lambda s, u: seq.pop(0))
    assert dg.validate_plan(plan) == []


def test_normalize_caps_dedupes_and_prunes_edges():
    big = {"components": [{"id": "a", "type": "node", "label": "n", "to": ["zzz"]}]
           + [{"id": "a", "type": "node", "label": "dup"} for _ in range(10)]}
    plan = dg._normalize(big)
    assert len(plan["components"]) <= dg.MAX_COMPONENTS
    assert len({c["id"] for c in plan["components"]}) == len(plan["components"])  # unique
    assert "to" not in plan["components"][0]     # edge to a pruned/unknown id dropped


def test_empty_content_raises():
    with pytest.raises(ValueError):
        dg.plan_diagram({"content": "  "}, chat_fn=_canned(json.dumps(_GOOD)))


# ---- cross-engine vocab parity with Mason's renderer -----------------
def _mason_vocab():
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "composition-engineer" / "diagram_render.py")
    tree = ast.parse(src.read_text())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple) \
                and isinstance(node.targets[0], ast.Name):
            vals = [el.value for el in node.value.elts
                    if isinstance(el, ast.Constant) and isinstance(el.value, str)]
            if vals:
                out[node.targets[0].id] = set(vals)
    return out


def test_closed_vocab_matches_masons_renderer():
    m = _mason_vocab()
    assert set(dg.LAYOUT_HINTS) == m["DIAGRAM_LAYOUTS"]
    assert set(dg.COMPONENTS) == m["DIAGRAM_COMPONENTS"]
    assert set(dg.GLYPHS) == m["DIAGRAM_GLYPHS"]
    assert set(dg.EMPHASIS) == m["DIAGRAM_EMPHASIS"]
    assert set(dg.ANIM) == m["DIAGRAM_ANIM"]
