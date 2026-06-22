"""Offline proof for Iris's art-direction engine — NO network, NO API keys.

Run (from the project folder):  python tests/test_art_engine.py
Or:                             pytest tests/test_art_engine.py

The brain (the one taste-making LLM call per job) is MOCKED throughout via an injected
chat_fn, so we assert the engine's PLUMBING and the HARD INVARIANTS only:
  - validate_script: rejects a non-dict / no-scenes / malformed-scene script
  - clamp_fps / clamp_max_per_scene: bounds enforced
  - enforce_palette: the #FFD000 signature is always present; accents bounded (no
    rainbow) and never duplicate the signature
  - normalize_textures / normalize_effects: vocabulary membership + dedupe; both the
    bare-string and {name,params} input forms
  - choose_signature_scene: deterministic (first-flagged -> first-highlighter -> middle)
  - apply_motion_budget: respects max_per_scene and NEVER drops a mandatory effect
  - ensure_shots: every shot gets a non-null asset_ref (generated s{n}-{i} or kept)
  - design_style / build_storyboard end to end with a mocked brain:
      * exactly ONE signature_beat
      * the highlighter-FFD000 EFFECT is on exactly that beat and no other
      * scene count == script scene count
      * vocabulary membership for layout / transition / effects
      * the motion budget is respected per scene
      * fps set
  - both emitted dicts validate against atlas's BUMPED (1.1) frozen schemas
  - the REPL [y/N] gate behavior (standalone gates; pipeline/adapter is gate-free)

HONEST NOTE: whether the REAL brain makes tasteful, topic-appropriate choices (a good
palette, the right layout per scene, the right scene as the signature beat) is a
MANUAL/integration check (a real `run.py style/board`, a live pipeline run, or the
weak-model persona harness). Only the plumbing + the invariants are unit-tested.
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import art_engine as engine  # noqa: E402

# Make atlas's frozen contracts importable so we can assert real schema validity.
# APPEND (not insert-at-0): atlas/ also ships chat.py / llm.py / chat_state.py, and we
# must NOT let those shadow art-director's own modules when chat.py imports them.
_ATLAS = pathlib.Path(__file__).resolve().parent.parent.parent / "atlas"
sys.path.append(str(_ATLAS))
import contracts  # noqa: E402


SCRIPT = {
    "schema_version": "1.0",
    "working_title": "What Crema Really Tells You",
    "scenes": [
        {"scene_no": 1, "beat": "hook", "point": "crema is misunderstood",
         "on_screen_text": "CREMA", "visual_note": "macro shot of crema", "claims": []},
        {"scene_no": 2, "beat": "point", "point": "not a quality signal",
         "on_screen_text": "not a grade", "visual_note": "a rising line chart 1990-2020",
         "claims": [{"claim_id": "s2c1", "text": "x", "source_ref": 0}]},
        {"scene_no": 3, "beat": "detour", "point": "a quote",
         "on_screen_text": "\"mostly gas\"", "visual_note": "pulled quote",
         "claims": [{"claim_id": "s3c1", "text": "y", "source_ref": 1}]},
        {"scene_no": 4, "beat": "cta", "point": "turn outward",
         "on_screen_text": "", "visual_note": "host to camera", "claims": []},
    ],
}


def _chat_returning(obj):
    def chat_fn(system, user):
        return json.dumps(obj)
    return chat_fn


def _style_out(**overrides):
    base = {
        "palette": {"primary": "#111111", "bg": "#FAFAF7", "text": "#111111",
                    "accents": ["#1E5BFF", "#FF3366", "#00CC88", "#AA00FF", "#FFD000"]},
        "typography": {"display": {"family": "GT Sectra", "weight": 700}},
        "motion": {"max_per_scene": 2, "easing": "stepped"},
        "textures": [{"name": "paper"}, {"name": "halftone", "params": {"dot": 3}},
                     "not-a-real-texture"],
        "fps": 99,
        "dos": ["one loud accent"], "donts": ["drop shadows on flat type"],
    }
    base.update(overrides)
    return base


def _board_out(**overrides):
    base = {"scenes": [
        {"scene_no": 1, "layout": "title-card", "transition": "cut",
         "effects": ["stepped-ease"], "signature_beat": False,
         "shots": [{"kind": "title", "content": "cold open"}]},
        {"scene_no": 2, "layout": "data-chart", "transition": "dip-to-black",
         "effects": ["highlighter-FFD000", "map-draw", "push-in"], "signature_beat": True,
         "shots": [{"kind": "chart", "content": "rising line"}]},
        {"scene_no": 3, "layout": "bogus-layout", "transition": "teleport",
         "effects": ["highlighter-FFD000", "not-a-real-effect"], "signature_beat": True,
         "shots": []},
        {"scene_no": 4, "layout": "centered-statement", "transition": "cut",
         "effects": [], "signature_beat": False,
         "shots": [{"kind": "host", "content": "to camera", "asset_ref": "kept-ref"}]},
    ]}
    base.update(overrides)
    return base


# ----------------------------------------------------------------------
# validate_script
# ----------------------------------------------------------------------
def test_validate_script_accepts_a_usable_script():
    assert engine.validate_script(SCRIPT)[0] is True


def test_validate_script_rejects_bad_input():
    assert engine.validate_script("not a dict")[0] is False
    assert engine.validate_script({})[0] is False
    assert engine.validate_script({"scenes": []})[0] is False
    assert engine.validate_script({"scenes": ["not an object"]})[0] is False


# ----------------------------------------------------------------------
# clamps
# ----------------------------------------------------------------------
def test_clamp_fps():
    assert engine.clamp_fps(30) == 30
    assert engine.clamp_fps(99) == 60
    assert engine.clamp_fps(1) == 12
    assert engine.clamp_fps("nope") == engine.DEFAULT_FPS
    assert engine.clamp_fps(24.6) == 25


def test_clamp_max_per_scene():
    assert engine.clamp_max_per_scene(2) == 2
    assert engine.clamp_max_per_scene(0) == 1
    assert engine.clamp_max_per_scene(99) == engine.MAX_PER_SCENE_CEIL
    assert engine.clamp_max_per_scene(None) == engine.DEFAULT_MAX_PER_SCENE


# ----------------------------------------------------------------------
# enforce_palette
# ----------------------------------------------------------------------
def test_enforce_palette_always_has_the_signature():
    p = engine.enforce_palette({})
    assert p["signature_highlight"] == "#FFD000"
    assert engine._is_hex(p["primary"]) and engine._is_hex(p["bg"]) and engine._is_hex(p["text"])


def test_enforce_palette_bounds_accents_and_excludes_the_signature():
    p = engine.enforce_palette({"accents": ["#1E5BFF", "#FF3366", "#00CC88", "#AA00FF",
                                            "#FFD000", "#1E5BFF", "notahex"]})
    assert len(p["accents"]) <= engine.MAX_ACCENTS          # bounded (no rainbow)
    assert "#FFD000" not in p["accents"]                    # signature is reserved
    assert "notahex" not in p["accents"]                    # hex only
    assert len(p["accents"]) == len(set(p["accents"]))      # de-duped


# ----------------------------------------------------------------------
# vocabulary normalization
# ----------------------------------------------------------------------
def test_normalize_textures_filters_to_vocab_and_dedupes():
    out = engine.normalize_textures(["paper", {"name": "halftone", "params": {"dot": 3}},
                                     "paper", "rainbow", {"name": "vignette"}])
    names = [t["name"] for t in out]
    assert names == ["paper", "halftone", "vignette"]       # vocab only, deduped, ordered
    assert out[1]["params"] == {"dot": 3}
    assert all("params" in t for t in out)                  # always {name, params}


def test_normalize_effects_filters_to_vocab():
    out = engine.normalize_effects(["push-in", "not-real", {"name": "map-draw"},
                                    {"name": "also-fake"}])
    assert [e["name"] for e in out] == ["push-in", "map-draw"]


# ----------------------------------------------------------------------
# choose_signature_scene (deterministic)
# ----------------------------------------------------------------------
def test_choose_signature_scene_prefers_first_flagged():
    scenes = [{"signature_beat": False}, {"signature_beat": True}, {"signature_beat": True}]
    assert engine.choose_signature_scene(scenes) == 1


def test_choose_signature_scene_then_first_highlighter_then_middle():
    by_fx = [{"signature_beat": False, "effects": []},
             {"signature_beat": False, "effects": [{"name": "highlighter-FFD000"}]},
             {"signature_beat": False, "effects": []}]
    assert engine.choose_signature_scene(by_fx) == 1
    none_flagged = [{"effects": []}, {"effects": []}, {"effects": []}]
    assert engine.choose_signature_scene(none_flagged) == 1   # middle of 3


# ----------------------------------------------------------------------
# apply_motion_budget
# ----------------------------------------------------------------------
def test_budget_trims_to_max_and_downgrades_transition():
    fx = [{"name": "push-in", "params": {}}, {"name": "parallax", "params": {}},
          {"name": "map-draw", "params": {}}]
    transition, kept = engine.apply_motion_budget("push", fx, 2, set())
    assert (transition != "cut") + len(kept) <= 2


def test_budget_never_drops_a_mandatory_effect_even_at_budget_one():
    fx = [{"name": "highlighter-FFD000", "params": {}}, {"name": "push-in", "params": {}}]
    # budget 1, mandatory highlighter -> transition forced to cut, only highlighter kept
    transition, kept = engine.apply_motion_budget("push", fx, 1, {"highlighter-FFD000"})
    assert transition == "cut"
    assert [e["name"] for e in kept] == ["highlighter-FFD000"]


def test_budget_keeps_mandatory_plus_transition_when_it_fits():
    fx = [{"name": "highlighter-FFD000", "params": {}}, {"name": "push-in", "params": {}}]
    transition, kept = engine.apply_motion_budget("push", fx, 2, {"highlighter-FFD000"})
    assert transition == "push"
    assert "highlighter-FFD000" in [e["name"] for e in kept]
    assert (transition != "cut") + len(kept) <= 2


# ----------------------------------------------------------------------
# ensure_shots
# ----------------------------------------------------------------------
def test_ensure_shots_guarantees_asset_ref():
    shots = engine.ensure_shots([{"kind": "image", "content": "a"}, {"asset_ref": "  "}],
                                7, "on screen")
    assert shots[0]["asset_ref"] == "s7-1"                  # generated
    assert shots[1]["asset_ref"] == "s7-2"                  # blank -> generated
    kept = engine.ensure_shots([{"asset_ref": "real-ref"}], 3, "")
    assert kept[0]["asset_ref"] == "real-ref"               # preserved


def test_ensure_shots_synthesizes_a_default_when_empty():
    shots = engine.ensure_shots([], 2, "the line")
    assert len(shots) == 1
    assert shots[0]["asset_ref"] == "s2-1"
    assert shots[0]["content"] == "the line"


# ----------------------------------------------------------------------
# Brand auto-tagging — a shot naming a registry model becomes kind:'brand' so
# Magpie skips it and Mason renders an HTML/SVG brand-chip (issue #2, Direction A).
# ----------------------------------------------------------------------
def test_ensure_shots_autotags_brand_when_content_names_a_model():
    shots = engine.ensure_shots(
        [{"kind": "graphic", "content": "the Claude logo glowing", "asset_ref": "r1"},
         {"kind": "photo", "content": "a city skyline at dusk", "asset_ref": "r2"},
         {"kind": "statement", "content": "fast and cheap, DeepSeek beneath", "asset_ref": "r3"}],
        5, "")
    assert shots[0]["kind"] == "brand"      # names Claude -> brand
    assert shots[1]["kind"] == "photo"      # no model -> unchanged
    assert shots[2]["kind"] == "brand"      # 'statement' is not pure-typography -> brand


def test_ensure_shots_autotags_brand_from_asset_ref():
    shots = engine.ensure_shots(
        [{"kind": "graphic", "content": "four logos lined up", "asset_ref": "gpt_reasoning_web"}],
        9, "")
    assert shots[0]["kind"] == "brand"      # asset_ref names gpt -> brand


def test_ensure_shots_does_not_override_pure_typography():
    shots = engine.ensure_shots(
        [{"kind": "title", "content": "GPT-4o wins", "asset_ref": "r1"}], 1, "")
    assert shots[0]["kind"] == "title"      # a title that mentions a model stays text


def test_brand_aliases_are_a_subset_of_the_composition_engineer_registry():
    """Drift guard: every alias Iris auto-tags on must be one Mason can render, else a
    brand shot would skip sourcing yet render no chip. Mirrors the cross-engine vocab test."""
    import ast
    src = (pathlib.Path(__file__).resolve().parents[2] / "composition-engineer"
           / "composition_engine.py")
    if not src.exists():
        return
    tree = ast.parse(src.read_text())
    mason_strings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == "BRAND_CHIPS":
            for s in ast.walk(node.value):
                if isinstance(s, ast.Constant) and isinstance(s.value, str):
                    mason_strings.add(s.value.lower())
    assert mason_strings, "could not find BRAND_CHIPS in composition_engine.py"
    assert {a.lower() for a in engine.BRAND_ALIASES} <= mason_strings


# ----------------------------------------------------------------------
# design_style — end to end (mocked brain) + contract validity
# ----------------------------------------------------------------------
def test_design_style_enforces_invariants_and_validates():
    style = engine.design_style(SCRIPT, chat_fn=_chat_returning(_style_out()))
    assert style["palette"]["signature_highlight"] == "#FFD000"
    assert len(style["palette"]["accents"]) <= engine.MAX_ACCENTS
    assert "#FFD000" not in style["palette"]["accents"]
    assert style["fps"] == 60                                   # 99 clamped
    assert [t["name"] for t in style["textures"]] == ["paper", "halftone"]  # vocab only
    stamped = {"schema_version": contracts.version_for("style_guide"), **style}
    ok, errors = contracts.validate("style_guide", stamped)
    assert ok, errors


# ----------------------------------------------------------------------
# build_storyboard — end to end (mocked brain) + every invariant + contract validity
# ----------------------------------------------------------------------
def test_build_storyboard_scene_count_matches_script():
    board = engine.build_storyboard(SCRIPT, None, chat_fn=_chat_returning(_board_out()))
    assert board["total_scenes"] == len(SCRIPT["scenes"]) == 4


def test_build_storyboard_exactly_one_signature_beat():
    board = engine.build_storyboard(SCRIPT, None, chat_fn=_chat_returning(_board_out()))
    sig = [s["scene_no"] for s in board["scenes"] if s["signature_beat"]]
    assert len(sig) == 1


def test_highlighter_effect_is_on_exactly_the_signature_beat():
    board = engine.build_storyboard(SCRIPT, None, chat_fn=_chat_returning(_board_out()))
    sig_no = next(s["scene_no"] for s in board["scenes"] if s["signature_beat"])
    for s in board["scenes"]:
        has_hl = any(e["name"] == engine.SIGNATURE_EFFECT for e in s["effects"])
        assert has_hl == (s["scene_no"] == sig_no), (s["scene_no"], has_hl)


def test_build_storyboard_respects_the_budget_and_vocab():
    style = {"motion": {"max_per_scene": 2}}
    board = engine.build_storyboard(SCRIPT, style, chat_fn=_chat_returning(_board_out()))
    for s in board["scenes"]:
        assert s["layout"] in engine.LAYOUTS
        assert s["transition"] in engine.TRANSITIONS
        assert all(e["name"] in engine.EFFECTS for e in s["effects"])
        budget_used = (s["transition"] != "cut") + len(s["effects"])
        assert budget_used <= 2, (s["scene_no"], budget_used)


def test_default_typography_is_the_bundled_ofl_pairing():
    # Iris's default display face is the bundled OFL Fraunces (replacing GT Sectra);
    # body/caption stay Inter. The brain may still override.
    style = engine.assemble_style(SCRIPT, {"palette": {}, "typography": {}})
    assert style["typography"]["display"]["family"] == "Fraunces"
    assert style["typography"]["body"]["family"] == "Inter"
    assert style["typography"]["caption"]["family"] == "Inter"


def test_layout_hint_picks_big_number_for_a_single_dominant_stat():
    scene = {"on_screen_text": "40% fewer", "point": "Costs dropped sharply",
             "visual_note": "one giant number"}
    assert engine._layout_hint_for_scene(scene) == "big-number"


def test_layout_hint_picks_timeline_for_a_chronological_scene():
    scene = {"on_screen_text": "From 1969 to 2007", "point": "A short history",
             "visual_note": "milestones over the years"}
    assert engine._layout_hint_for_scene(scene) == "timeline"


def test_layout_hint_defaults_to_centered_statement():
    scene = {"on_screen_text": "Wrong question.", "point": "Reframe the premise",
             "visual_note": "just a line"}
    assert engine._layout_hint_for_scene(scene) == "centered-statement"


def test_storyboard_fallback_uses_the_layout_hint_when_brain_botches_layout():
    # The brain returns an unknown layout; the script scene cues a chronology -> timeline.
    script = {"working_title": "T", "scenes": [
        {"scene_no": 1, "on_screen_text": "1969 then 2007",
         "point": "history", "visual_note": "timeline of milestones"}]}
    botched = {"scenes": [{"scene_no": 1, "layout": "bogus", "transition": "cut",
                           "effects": [], "shots": [{"kind": "graphic", "content": "x"}]}]}
    board = engine.build_storyboard(script, None, chat_fn=_chat_returning(botched))
    assert board["scenes"][0]["layout"] == "timeline"


def test_new_layout_and_effect_tokens_are_in_the_vocabulary():
    assert "big-number" in engine.LAYOUTS and "timeline" in engine.LAYOUTS
    assert "count-up" in engine.EFFECTS


def test_build_storyboard_every_shot_has_an_asset_ref():
    board = engine.build_storyboard(SCRIPT, None, chat_fn=_chat_returning(_board_out()))
    for s in board["scenes"]:
        assert s["shots"], s["scene_no"]
        for sh in s["shots"]:
            assert isinstance(sh["asset_ref"], str) and sh["asset_ref"].strip()


def test_build_storyboard_validates_against_the_bumped_schema():
    board = engine.build_storyboard(SCRIPT, None, chat_fn=_chat_returning(_board_out()))
    stamped = {"schema_version": contracts.version_for("storyboard"), **board}
    ok, errors = contracts.validate("storyboard", stamped)
    assert ok, errors


def test_bad_script_raises_before_any_brain_call():
    def exploding_chat(system, user):
        raise AssertionError("the brain must not be called on an invalid script")
    for fn in (engine.design_style, engine.build_storyboard):
        try:
            fn({"scenes": []}, chat_fn=exploding_chat)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{fn.__name__} must raise ValueError on a bad script")


# ----------------------------------------------------------------------
# The REPL [y/N] gate behavior: standalone gates; the adapter/pipeline does NOT.
# (We mock the engine so no network is touched, and write into a tmp project dir.)
# ----------------------------------------------------------------------
def test_repl_gate_blocks_write_when_declined(monkeypatch):
    import chat
    style = engine.design_style(SCRIPT, chat_fn=_chat_returning(_style_out()))
    monkeypatch.setattr(chat, "compute_style", lambda path: style)
    monkeypatch.setattr(chat, "ask_yes_no", lambda prompt: False)   # user says No
    with tempfile.TemporaryDirectory() as d:
        out = chat.run_style_job(d, gate=True)
        assert out is None                                          # declined
        assert not (pathlib.Path(d) / "style_guide.json").exists()  # nothing written


def test_repl_gate_writes_when_approved(monkeypatch):
    import chat
    style = engine.design_style(SCRIPT, chat_fn=_chat_returning(_style_out()))
    monkeypatch.setattr(chat, "compute_style", lambda path: style)
    monkeypatch.setattr(chat, "ask_yes_no", lambda prompt: True)    # user says Yes
    monkeypatch.setattr(chat, "_log_run", lambda *a, **k: None)     # don't touch memory.json
    monkeypatch.setattr(chat.engine, "load_script", lambda path: SCRIPT)
    with tempfile.TemporaryDirectory() as d:
        out = chat.run_style_job(d, gate=True)
        assert out is not None
        written = pathlib.Path(d) / "style_guide.json"
        assert written.exists()                                     # gate passed -> wrote
        data = json.loads(written.read_text())
        assert data["schema_version"] == "1.1"
        assert data["palette"]["signature_highlight"] == "#FFD000"


def test_explicit_command_path_does_not_gate(monkeypatch):
    # /style and /board pass gate=False (typing the command IS the approval); the job
    # still runs and writes. Same code path, gate disabled.
    import chat
    style = engine.design_style(SCRIPT, chat_fn=_chat_returning(_style_out()))
    monkeypatch.setattr(chat, "compute_style", lambda path: style)
    monkeypatch.setattr(chat, "_log_run", lambda *a, **k: None)     # don't touch memory.json

    def _boom(prompt):
        raise AssertionError("gate=False must NOT prompt")
    monkeypatch.setattr(chat, "ask_yes_no", _boom)
    with tempfile.TemporaryDirectory() as d:
        out = chat.run_style_job(d, gate=False)
        assert out is not None
        assert (pathlib.Path(d) / "style_guide.json").exists()


# ----------------------------------------------------------------------
# standalone runner
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import traceback
    import types

    class _MP:
        def __init__(self): self._undo = []
        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, val in reversed(self._undo):
                setattr(obj, name, val)
            self._undo.clear()

    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and isinstance(v, types.FunctionType)]
    failed = 0
    for fn in fns:
        mp = _MP()
        try:
            if "monkeypatch" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                fn(mp)
            else:
                fn()
            print(f"  ok  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
        finally:
            mp.undo()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
