"""Pure-unit tests for Mason's engine — NO network, NO API, NO render.

Everything here runs offline: the determinism self-scan, deterministic HTML assembly
from fixtures, vocabulary completeness + unknown-token rejection, the stutter math, the
global->local caption offset, the motion-budget trim, asset localization rules, the
transition->assembly mapping, and the composition manifest shape (the HyperFrames CLI
is mocked). The real lint/validate/inspect/render behavior is integration (flagged in
the build report), not exercised here.
"""
import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import composition_engine as engine  # noqa: E402


# ----------------------------------------------------------------------
# Vocabulary completeness + an exact match to the Art Director's vocabulary
# ----------------------------------------------------------------------
def test_a_partial_exists_for_every_token_on_all_four_axes():
    assert set(engine.LAYOUT_BUILDERS) == set(engine.LAYOUTS)
    assert set(engine.TEXTURE_BUILDERS) == set(engine.TEXTURES)
    assert set(engine.EFFECT_BUILDERS) == set(engine.EFFECTS)
    assert set(engine.TRANSITION_ASSEMBLY) == set(engine.TRANSITIONS)


def _extract_art_director_vocab():
    """ast-parse art-director/art_engine.py and pull the four vocabulary tuples, so a
    drift between Iris's menu and Mason's partials is caught here (no import side effects)."""
    src = (pathlib.Path(__file__).resolve().parents[2] / "art-director" / "art_engine.py")
    if not src.exists():
        return None
    tree = ast.parse(src.read_text())
    names: dict[str, str] = {}
    tuples: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            names[tgt.id] = node.value.value
        elif isinstance(node.value, ast.Tuple):
            vals = []
            for el in node.value.elts:
                if isinstance(el, ast.Constant) and isinstance(el.value, str):
                    vals.append(el.value)
                elif isinstance(el, ast.Name) and el.id in names:
                    vals.append(names[el.id])
            tuples[tgt.id] = vals
    return tuples


def test_vocabulary_matches_the_art_director_exactly():
    vocab = _extract_art_director_vocab()
    if vocab is None:
        return  # art-director not present; the partials-complete test still guards us
    assert set(vocab["LAYOUTS"]) == set(engine.LAYOUTS)
    assert set(vocab["TRANSITIONS"]) == set(engine.TRANSITIONS)
    assert set(vocab["EFFECTS"]) == set(engine.EFFECTS)
    assert set(vocab["TEXTURES"]) == set(engine.TEXTURES)


# ----------------------------------------------------------------------
# Input validation: unknown tokens REJECTED (never silently dropped); remote URIs blocked
# ----------------------------------------------------------------------
def _good_inputs():
    script = {"scenes": [{"scene_no": 1, "point": "p", "narration": "n",
                          "on_screen_text": "Hello", "duration_est_sec": 4.0}]}
    style = {"palette": {"signature_highlight": "#FFD000"}, "fps": 30,
             "textures": ["paper", "grain"], "motion": {"max_per_scene": 2}}
    board = {"scenes": [{"scene_no": 1, "layout": "centered-statement",
                         "transition": "cut", "effects": [], "signature_beat": True}]}
    assets = {"assets": []}
    return script, style, board, assets


def test_validate_inputs_accepts_clean_inputs():
    ok, errors = engine.validate_inputs(*_good_inputs())
    assert ok and errors == []


def test_validate_inputs_rejects_no_scenes():
    script, style, board, assets = _good_inputs()
    script["scenes"] = []
    ok, errors = engine.validate_inputs(script, style, board, assets)
    assert not ok and any("no scenes" in e for e in errors)


def test_unknown_layout_token_is_rejected():
    script, style, board, assets = _good_inputs()
    board["scenes"][0]["layout"] = "isometric-explosion"
    ok, errors = engine.validate_inputs(script, style, board, assets)
    assert not ok and any("unknown layout" in e for e in errors)


def test_unknown_effect_and_texture_and_transition_tokens_are_rejected():
    script, style, board, assets = _good_inputs()
    style["textures"] = ["paper", "lens-flare"]
    board["scenes"][0]["effects"] = ["sparkles"]
    board["scenes"][0]["transition"] = "star-wipe"
    ok, errors = engine.validate_inputs(script, style, board, assets)
    assert not ok
    joined = " ".join(errors)
    assert "unknown texture token 'lens-flare'" in joined
    assert "unknown effect token 'sparkles'" in joined
    assert "unknown transition token 'star-wipe'" in joined


def test_remote_asset_uri_is_a_hard_block():
    script, style, board, assets = _good_inputs()
    assets["assets"] = [{"asset_id": "a1", "scene_no": 1, "type": "image",
                         "license": "CC0", "uri": "https://example.com/x.png",
                         "status": "sourced"}]
    ok, errors = engine.validate_inputs(script, style, board, assets)
    assert not ok and any("remote URI" in e for e in errors)


# ----------------------------------------------------------------------
# Stutter math — steps(round(12*dur)), 12 constant, decoupled from render fps
# ----------------------------------------------------------------------
def test_stutter_steps_uses_twelve_fps_constant():
    assert engine.stutter_steps(3.0) == 36          # round(12*3)
    assert engine.stutter_steps(2.0) == 24
    assert engine.stutter_steps(0.0) == 1           # never zero
    # decoupled from render fps: a 30fps render must NOT change the stutter count
    assert engine.stutter_steps(2.0) != engine.stutter_steps(2.0, stutter_fps=30)


# ----------------------------------------------------------------------
# Captions — global transcript timings offset to each scene's LOCAL timeline
# ----------------------------------------------------------------------
def test_scene_captions_offset_global_timings_to_local():
    segments = [
        {"scene_no": 1, "start_sec": 0.0, "end_sec": 4.0, "text": "one"},
        {"scene_no": 2, "start_sec": 4.0, "end_sec": 9.0, "text": "two"},   # global
        {"scene_no": 2, "start_sec": 9.0, "end_sec": 12.0, "text": "three"},
    ]
    caps = engine.scene_captions(segments, scene_no=2)
    assert caps[0]["start"] == 0.0 and caps[0]["duration"] == 5.0   # 4..9 -> local 0..5
    assert caps[1]["start"] == 5.0 and caps[1]["duration"] == 3.0   # 9..12 -> local 5..8
    assert [c["text"] for c in caps] == ["two", "three"]


def test_scene_captions_empty_when_no_segment_for_scene():
    assert engine.scene_captions([{"scene_no": 1, "start_sec": 0, "end_sec": 1,
                                   "text": "x"}], scene_no=9) == []


# ----------------------------------------------------------------------
# Motion budget — (non-cut transition + effects) <= max; signature kept
# ----------------------------------------------------------------------
def test_trim_effects_respects_budget_and_downgrades_extras():
    fx = [{"name": "push-in", "params": {}}, {"name": "parallax", "params": {}},
          {"name": "chromatic-aberration", "params": {}}]
    out = engine.trim_effects(fx, transition="push", max_per_scene=2, signature=False)
    assert len(out) == 1                       # budget 2, transition costs 1 -> room 1


def test_trim_effects_never_drops_the_signature_highlighter():
    fx = [{"name": "push-in", "params": {}}, {"name": engine.SIGNATURE_EFFECT, "params": {}}]
    out = engine.trim_effects(fx, transition="dip-to-black", max_per_scene=1,
                              signature=True)
    assert out and out[0]["name"] == engine.SIGNATURE_EFFECT


def test_signature_highlighter_only_on_signature_scene():
    fx = [{"name": engine.SIGNATURE_EFFECT, "params": {}}]
    out = engine.trim_effects(fx, transition="cut", max_per_scene=2, signature=False)
    assert all(e["name"] != engine.SIGNATURE_EFFECT for e in out)


# ----------------------------------------------------------------------
# The determinism SELF-SCAN — owns the three rules lint misses
# ----------------------------------------------------------------------
PLANTED = """
<script>
  const tl = gsap.timeline({paused:true});
  fetch("https://example.com/d.json").then(r => r.json()).then(d => {
    gsap.set("#t", {opacity: d ? 1 : 0});
  });
</script>
<svg><filter id="w"><feTurbulence><animate attributeName="baseFrequency"
  values="0.02;0.05" repeatCount="indefinite"/></feTurbulence></filter></svg>
"""


def test_self_scan_catches_all_three_planted_violations():
    rules = {v["rule"] for v in engine.scan_determinism(PLANTED)}
    assert "render_time_fetch" in rules
    assert "animated_svg_filter" in rules
    assert "late_async_gsap_set" in rules


def test_self_scan_is_clean_on_a_real_composed_scene():
    html = engine.compose_scene_html(_ctx())
    assert engine.scan_determinism(html) == []


# ----------------------------------------------------------------------
# Deterministic HTML assembly + the lint-enforced generator invariants
# ----------------------------------------------------------------------
def _ctx(**over):
    base = {
        "scene_no": 1, "comp_id": "scene-01", "duration": 4.0, "fps": 30,
        "title": "Design simplified.", "layout": "centered-statement",
        "transition": "cut", "effects": [{"name": engine.SIGNATURE_EFFECT, "params": {}}],
        "textures": [{"name": "paper", "params": {}}, {"name": "grain", "params": {}}],
        "signature": True, "palette": {"bg": "#0d0d0d", "text": "#fff", "font": "Inter",
                                       "signature_highlight": "#FFD000"},
        "highlight": "#FFD000",
        "captions": [{"start": 0.0, "duration": 3.0, "text": "design simplified"}],
        "assets": [],
    }
    base.update(over)
    return base


def test_assembly_is_byte_deterministic():
    assert engine.compose_scene_html(_ctx()) == engine.compose_scene_html(_ctx())


def test_root_is_first_body_element_with_required_attrs():
    html = engine.compose_scene_html(_ctx())
    body_inner = html.split("<body>", 1)[1].lstrip()
    assert body_inner.startswith('<div id="scene-root"')
    for attr in ('data-composition-id="scene-01"', 'data-width="1920"',
                 'data-height="1080"', 'data-start="0"', 'data-duration='):
        assert attr in html


def test_paused_timeline_registered_and_no_forbidden_idioms():
    html = engine.compose_scene_html(_ctx())
    assert "gsap.timeline({ paused: true })" in html
    assert 'window.__timelines["scene-01"]' in html
    assert "gsap.set(" not in html          # initial state via CSS, never gsap.set
    assert "Math.random" not in html
    assert "Date.now" not in html
    assert "repeat:-1" not in html and "repeat: -1" not in html


def test_captions_are_clip_elements_with_local_timing():
    html = engine.compose_scene_html(_ctx())
    assert 'class="caption clip" data-start="0.000" data-duration=' in html


def test_signature_scene_has_the_highlighter_sweep():
    html = engine.compose_scene_html(_ctx(signature=True))
    assert 'class="hl-sweep"' in html


def test_every_layout_renders_clean_and_self_scans_clean():
    for layout in engine.LAYOUTS:
        html = engine.compose_scene_html(_ctx(layout=layout, signature=False,
                                              effects=[]))
        assert layout in html
        assert engine.scan_determinism(html) == [], f"{layout} tripped the self-scan"


def test_every_effect_renders_clean_and_self_scans_clean():
    for name in engine.EFFECTS:
        sig = name == engine.SIGNATURE_EFFECT
        html = engine.compose_scene_html(_ctx(effects=[{"name": name, "params": {}}],
                                              signature=sig))
        assert engine.scan_determinism(html) == [], f"{name} tripped the self-scan"


def test_every_texture_overlay_renders():
    for name in engine.TEXTURES:
        html = engine.compose_scene_html(_ctx(textures=[{"name": name, "params": {}}]))
        assert f"tex-{name}" in html


def test_stutter_effect_uses_the_computed_step_count():
    html = engine.compose_scene_html(_ctx(duration=3.0,
                                          effects=[{"name": "stutter-12fps", "params": {}}],
                                          signature=False))
    assert "steps(36)" in html      # round(12 * 3.0)


def test_missing_local_asset_becomes_a_placeholder_panel():
    ctx = _ctx(layout="full-bleed-image", signature=False, effects=[],
               assets=[{"type": "image", "label": "a1", "src_rel": None,
                        "placeholder": True}])
    html = engine.compose_scene_html(ctx)
    assert "placeholder-panel" in html
    assert "<img" not in html


# ----------------------------------------------------------------------
# Asset resolution — placeholder vs integrity flag
# ----------------------------------------------------------------------
def test_resolve_assets_flags_missing_sourced_file_as_integrity_not_placeholder(tmp_path):
    manifest = {"assets": [
        {"asset_id": "a1", "scene_no": 1, "type": "image", "license": "CC0",
         "uri": "assets/missing.png", "status": "sourced"},
        {"asset_id": "a2", "scene_no": 1, "type": "image", "license": "CC0",
         "uri": "assets/ph.png", "status": "placeholder"},
    ]}
    out = engine.resolve_scene_assets(manifest, 1, tmp_path)
    sourced = next(a for a in out if a["asset_id"] == "a1")
    declared = next(a for a in out if a["asset_id"] == "a2")
    assert sourced["placeholder"] and sourced["integrity_flag"]      # missing -> integrity flag
    assert declared["placeholder"] and declared["integrity_flag"] is None  # expected


def test_resolve_assets_marks_present_local_file(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "real.png").write_bytes(b"\x89PNG")
    manifest = {"assets": [{"asset_id": "a1", "scene_no": 1, "type": "image",
                            "license": "CC0", "uri": "assets/real.png", "status": "cleared"}]}
    out = engine.resolve_scene_assets(manifest, 1, tmp_path)
    assert out[0]["present"] and not out[0]["placeholder"]


# ----------------------------------------------------------------------
# Transition -> assembly mapping (render_video), unknown token flagged
# ----------------------------------------------------------------------
def test_build_assembly_plan_maps_transitions_and_extracts_narration():
    manifest = {"scenes": [
        {"scene_no": 1, "render_path": "scenes/scene-01/renders/draft.mp4"},
        {"scene_no": 2, "render_path": "scenes/scene-02/renders/draft.mp4"},
    ]}
    storyboard = {"scenes": [{"scene_no": 1, "transition": "dip-to-black"},
                             {"scene_no": 2, "transition": "cut"}]}
    audio = {"tracks": [{"role": "narration", "uri": "audio/narration.wav"}]}
    plan = engine.build_assembly_plan(manifest, storyboard, audio)
    assert plan["narration"] == "audio/narration.wav"
    assert plan["missing_renders"] == []
    boundary = next(s for s in plan["steps"] if s.get("boundary_after") == 1)
    assert boundary["mode"] == "xfade" and boundary["xfade"] == "fadeblack"


def test_build_assembly_plan_flags_unknown_transition_and_missing_renders():
    manifest = {"scenes": [{"scene_no": 1, "render_path": None},
                           {"scene_no": 2, "render_path": "x.mp4"}]}
    storyboard = {"scenes": [{"scene_no": 1, "transition": "star-wipe"},
                             {"scene_no": 2, "transition": "cut"}]}
    plan = engine.build_assembly_plan(manifest, storyboard, {})
    assert 1 in plan["missing_renders"]
    assert any("unknown transition" in f for f in plan["flags"])


# ----------------------------------------------------------------------
# compose() end-to-end manifest shape — CLI gate MOCKED, renders skipped (offline)
# ----------------------------------------------------------------------
def _write_fixture_project(tmp_path):
    script, style, board, assets = _good_inputs()
    chat_state = engine.chat_state
    chat_state.atomic_write_json(tmp_path / "script.json", {"schema_version": "1.0", **script})
    chat_state.atomic_write_json(tmp_path / "style_guide.json", {"schema_version": "1.1", **style})
    chat_state.atomic_write_json(tmp_path / "storyboard.json", {"schema_version": "1.1", **board})
    chat_state.atomic_write_json(tmp_path / "asset_manifest.json", {"schema_version": "1.0", **assets})
    (tmp_path / "audio").mkdir(parents=True, exist_ok=True)
    chat_state.atomic_write_json(tmp_path / "audio" / "narration.transcript.json",
                                 {"schema_version": "1.0", "total_duration_sec": 4.0,
                                  "segments": [{"scene_no": 1, "start_sec": 0.0,
                                                "end_sec": 4.0, "text": "hello"}]})


def test_compose_builds_manifest_with_passing_gate(tmp_path, monkeypatch):
    _write_fixture_project(tmp_path)
    monkeypatch.setenv("MASON_SKIP_RENDER", "1")     # no real render
    import hf_tools
    monkeypatch.setattr(hf_tools, "run_gate", lambda d, motion_strict=False: {
        "lint": {"ok": True, "errors": 0}, "validate": {"ok": True, "contrast_failures": 0},
        "inspect": {"ok": True, "issues": 0}})

    manifest = engine.compose(tmp_path)
    assert manifest["verdict"] == "pass"
    assert manifest["summary"]["auto_gate"] == "PASS"
    assert manifest["summary"]["total"] == 1
    scene = manifest["scenes"][0]
    for key in ("scene_no", "html_path", "render_path", "self_scan", "gate",
                "render_status", "effects", "signature_beat"):
        assert key in scene
    assert (tmp_path / scene["html_path"]).exists()
    assert scene["render_status"] == "skipped"      # MASON_SKIP_RENDER honored
    # the signature scene emitted a motion.json sidecar for inspect --strict
    assert (tmp_path / "scenes" / "scene-01" / "index.motion.json").exists()


def test_compose_blocks_when_self_scan_fails(tmp_path, monkeypatch):
    _write_fixture_project(tmp_path)
    monkeypatch.setenv("MASON_SKIP_RENDER", "1")
    # Force a determinism violation into every scene's HTML, then compose.
    real = engine.compose_scene_html
    monkeypatch.setattr(engine, "compose_scene_html",
                        lambda ctx: real(ctx) + "<script>fetch('x');</script>")
    import hf_tools
    monkeypatch.setattr(hf_tools, "run_gate", lambda d, motion_strict=False: {
        "lint": {"ok": True}, "validate": {"ok": True}, "inspect": {"ok": True}})
    manifest = engine.compose(tmp_path)
    assert manifest["verdict"] == "blocked"
    assert manifest["scenes"][0]["self_scan"]["ok"] is False


def test_compose_raises_on_invalid_inputs(tmp_path, monkeypatch):
    _write_fixture_project(tmp_path)
    monkeypatch.setenv("MASON_SKIP_RENDER", "1")
    # inject an unknown effect token -> validation must reject before any build
    engine.chat_state.atomic_write_json(tmp_path / "storyboard.json", {
        "schema_version": "1.1",
        "scenes": [{"scene_no": 1, "layout": "centered-statement", "transition": "cut",
                    "effects": ["sparkles"], "signature_beat": True}]})
    try:
        engine.compose(tmp_path)
        assert False, "expected ValueError on unknown token"
    except ValueError as exc:
        assert "unknown effect" in str(exc)
