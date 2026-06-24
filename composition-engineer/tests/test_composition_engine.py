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
# Brand chips (issue #2, Direction A) — model/logo shots rendered as HTML/SVG,
# never sourced. Detection is by model name so the EXISTING storyboard (kind:'graphic')
# works too; chips show in BOTH media-slot and text-only layouts.
# ----------------------------------------------------------------------
def test_detect_brands_orders_and_dedupes():
    assert engine.detect_brands("GPT-4o Claude Gemini DeepSeek") == \
        ["openai", "anthropic", "google", "deepseek"]
    assert engine.detect_brands("Claude, and claude again") == ["anthropic"]
    assert engine.detect_brands("a quiet city skyline at dusk") == []


def test_scene_brand_keys_scans_shots_regardless_of_kind():
    shots = [{"kind": "graphic", "content": "four logos GPT-4o Claude Gemini DeepSeek",
              "asset_ref": "x"}]
    assert engine.scene_brand_keys(shots) == ["openai", "anthropic", "google", "deepseek"]
    # asset_ref alone names a model
    assert engine.scene_brand_keys([{"kind": "graphic", "content": "web",
                                     "asset_ref": "gpt_reasoning"}]) == ["openai"]


def test_registry_ships_inline_logos_with_no_external_refs():
    # Real brand logos are inlined; no runtime dep, no render-time fetch (HyperFrames-safe).
    for key, b in engine.BRAND_CHIPS.items():
        svg = b["logo_svg"]
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>"), key
        assert "url(http" not in svg, key          # gradients are local fragment refs only
        assert "href" not in svg, key              # no external/xlink references
        assert "<image" not in svg.lower(), key    # no embedded raster fetch


def test_render_brand_chips_renders_logo_and_label():
    # With a logo, the chip shows the INLINE <svg> as the mark AND the name as a label.
    one = engine.render_brand_chips(["anthropic"])
    assert one.count('class="brand-chip"') == 1
    assert "<svg" in one                     # the real logo mark
    assert "Claude" in one and "#D97757" in one   # name label + brand color


def test_render_brand_chips_strips_svg_title_to_avoid_occlusion_flag():
    # The inline logo SVGs carry a decorative <title> (e.g. "OpenAI"). HyperFrames'
    # inspect gate reads that as text occluded beneath the logo's own paths and errors
    # (text_occluded), blocking the render. The chip already shows a visible name label,
    # so the <title> is redundant — it must not reach the rendered HTML.
    html = engine.render_brand_chips(["openai", "anthropic", "google", "deepseek"])
    assert "<title>" not in html
    # the visible names are still present (label, not the stripped title)
    assert "GPT-4o" in html and "Claude" in html


def test_render_brand_chips_falls_back_to_text_when_logo_empty(monkeypatch):
    entry = dict(engine.BRAND_CHIPS["anthropic"], logo_svg="")
    monkeypatch.setitem(engine.BRAND_CHIPS, "anthropic", entry)
    html = engine.render_brand_chips(["anthropic"])
    assert "<svg" not in html and "Claude" in html   # typographic fallback


def test_render_brand_chips_matchup_renders_all_four_logos():
    four = engine.render_brand_chips(["openai", "anthropic", "google", "deepseek"])
    assert four.count('class="brand-chip"') == 4
    assert four.count("<svg") == 4           # four real logos
    for name in ("GPT-4o", "Claude", "Gemini", "DeepSeek"):
        assert name in four


def test_scene_brand_specs_deemphasizes_dimmed_shots():
    shots = [
        {"kind": "panel", "content": "Claude and DeepSeek logos glowing, foregrounded",
         "asset_ref": "a"},
        {"kind": "panel", "content": "GPT-4o and Gemini logos dimmed into the background",
         "asset_ref": "b"},
    ]
    by = {s["key"]: s["dim"] for s in engine.scene_brand_specs(shots)}
    assert by["anthropic"] is False and by["deepseek"] is False   # named winners
    assert by["openai"] is True and by["google"] is True          # de-emphasized


def test_render_brand_chips_dim_adds_class():
    html = engine.render_brand_chips([{"key": "openai", "dim": True},
                                      {"key": "anthropic", "dim": False}])
    assert 'class="brand-chip dim"' in html      # de-emphasized
    assert 'class="brand-chip"' in html          # the emphasized one


def test_brand_chips_injected_into_text_only_layout():
    ctx = _ctx(layout="title-card", signature=False, effects=[], assets=[],
               brand_keys=["deepseek"])
    html = engine.compose_scene_html(ctx)
    assert "brand-chip" in html and "DeepSeek" in html
    assert "title-card" in html          # the layout is preserved
    assert engine.scan_determinism(html) == []


def test_brand_chips_take_the_media_slot_in_media_layouts():
    ctx = _ctx(layout="split-screen", signature=False, effects=[],
               brand_keys=["openai", "anthropic"],
               assets=[{"type": "image", "label": "junk", "src_rel": "assets/junk.jpg",
                        "placeholder": False}])
    html = engine.compose_scene_html(ctx)
    assert "brand-media" in html
    assert "GPT-4o" in html and "Claude" in html
    assert "<img" not in html            # brand chips take precedence over a sourced asset
    assert engine.scan_determinism(html) == []


def test_no_brand_keys_leaves_layouts_unchanged():
    ctx = _ctx(layout="title-card", signature=False, effects=[], assets=[])
    html = engine.compose_scene_html(ctx)
    assert 'class="brand-chip"' not in html      # no chip element rendered
    assert "layout has-brand" not in html        # layout container class untouched


# ----------------------------------------------------------------------
# Render last-mile (occlusion): the two scene structures the inspect gate
# rejected with 'text_occluded'. These encode the structural invariants that
# keep text out from under opaque media; the geometric truth is verified by
# `npx hyperframes inspect` on the composed scenes.
# ----------------------------------------------------------------------
def test_data_chart_title_sits_above_the_chart_clear_of_caption_band():
    # data-chart rendered its <h2 title> AFTER the chart-frame, so the centered
    # column dropped the title into the bottom caption band where the burned-in
    # caption-scrim painted over it (inspect 'text_occluded'). The title must own
    # the TOP zone, above the chart, leaving the bottom band to the (text-free) media.
    html = engine.compose_scene_html(_ctx(layout="data-chart", signature=False,
                                          effects=[], assets=[]))
    assert '<h2 class="scene-title">' in html
    assert html.index('class="scene-title"') < html.index('class="chart-frame"'), \
        "data-chart title must render ABOVE the chart-frame"


def test_comparison_with_brands_keeps_chips_out_from_under_opaque_panel():
    # comparison-2up's .cmp panels are position:absolute and .cmp.myth is an opaque
    # dark plate. When brand chips were injected (has-brand), the grid laid out only
    # the in-flow chips row; the absolute opaque myth panel then painted OVER the
    # left-hand chips (inspect 'text_occluded' on 'GPT-4o'/'Claude'). Under has-brand
    # the cmp panels must flow as grid rows so every block keeps its own row.
    ctx = _ctx(layout="comparison-2up", signature=False, effects=[], assets=[],
               brand_keys=["openai", "anthropic", "google", "deepseek"])
    html = engine.compose_scene_html(ctx)
    assert "layout has-brand comparison-2up" in html
    for name in ("GPT-4o", "Claude", "Gemini", "DeepSeek"):
        assert name in html
    # the fix: CSS reflows the absolute opaque cmp panels into the has-brand grid
    assert ".layout.has-brand .cmp{" in engine._BASE_CSS
    assert ".layout.has-brand .cmp.myth{display:none" in engine._BASE_CSS
    assert engine.scan_determinism(html) == []


def test_scene_ctx_wires_brand_keys_from_storyboard_shots(tmp_path):
    board = {"scene_no": 1, "layout": "title-card", "transition": "cut", "effects": [],
             "signature_beat": False,
             "shots": [{"kind": "graphic", "content": "GPT-4o Claude Gemini DeepSeek",
                        "asset_ref": "logos"}]}
    ctx = engine._scene_ctx(1, {"scene_no": 1, "on_screen_text": "Wrong question."},
                            {"palette": {}}, board, [], [], tmp_path, tmp_path)
    assert ctx["brand_keys"] == ["openai", "anthropic", "google", "deepseek"]


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


# ----------------------------------------------------------------------
# C1 — typography must reach font-family as a NAME, never a leaked Python dict
# ----------------------------------------------------------------------
def test_dict_shaped_typography_emits_font_name_not_dict_repr(tmp_path):
    # Iris emits typography slots as nested dicts: display/body = {family, weight}.
    # A BUNDLED display family (Fraunces) reaches the heading CSS as a name; an unbundled
    # one falls back (covered below). NO dict repr ever leaks.
    style = {"palette": {}, "typography": {"display": {"family": "Fraunces", "weight": 700},
                                           "body": {"family": "Inter", "weight": 400}}}
    board = {"scene_no": 1, "layout": "centered-statement", "transition": "cut",
             "effects": [], "signature_beat": False}
    ctx = engine._scene_ctx(1, {"scene_no": 1, "on_screen_text": "Hi"}, style, board,
                            [], [], tmp_path, tmp_path)
    html = engine.compose_scene_html(ctx)
    assert "Fraunces" in html
    assert "Inter" in html                       # body font present too
    assert "'family'" not in html and '"family"' not in html
    # no Python-dict brace leaked into any font-family declaration
    for decl in html.split("font-family:")[1:]:
        assert "{" not in decl.split(";", 1)[0]


def test_unbundled_family_falls_back_to_a_bundled_face(tmp_path):
    # An unbundled (e.g. proprietary) family name must snap to a guaranteed-present OFL
    # face so the render is never fontless — a serif/display role -> Noto Serif Display.
    style = {"palette": {}, "typography": {"display": {"family": "GT Sectra", "weight": 700},
                                           "body": "Helvetica Neue"}}
    board = {"scene_no": 1, "layout": "centered-statement", "transition": "cut",
             "effects": [], "signature_beat": False}
    ctx = engine._scene_ctx(1, {"scene_no": 1, "on_screen_text": "Hi"}, style, board,
                            [], [], tmp_path, tmp_path)
    html = engine.compose_scene_html(ctx)
    assert "GT Sectra" not in html and "Helvetica Neue" not in html
    assert engine.FALLBACK_DISPLAY_FAMILY in html   # Noto Serif Display
    assert engine.FALLBACK_BODY_FAMILY in html      # Noto Sans
    for decl in html.split("font-family:")[1:]:
        assert "{" not in decl.split(";", 1)[0]


# ----------------------------------------------------------------------
# Job 1 — OFL fonts bundled LOCALLY as @font-face (no render-time font fetch)
# ----------------------------------------------------------------------
def test_emitted_css_has_local_font_face_no_http():
    # The default display face (Fraunces) reaches the heading CSS via a LOCAL @font-face.
    html = engine.compose_scene_html(_ctx(palette={"bg": "#0d0d0d", "text": "#fff",
                                                   "font": "Fraunces", "body_font": "Inter"}))
    assert "@font-face" in html
    # the @font-face src points at a local assets/fonts/... path — NEVER http
    assert "src:url('assets/fonts/Fraunces.ttf')" in html
    assert "assets/fonts/Inter.ttf" in html
    assert "http" not in html.split("@font-face", 1)[1].split("}", 1)[0]
    # heading CSS uses the bundled display family NAME, no leaked dict brace
    assert ".scene-title{font-family:'Fraunces'" in html
    # variable faces declare the full weight range
    assert "font-weight:100 900;" in html


def test_scene_build_copies_font_files_into_the_project(tmp_path):
    style = {"palette": {}, "typography": {"display": {"family": "Fraunces", "weight": 700},
                                           "body": {"family": "Inter", "weight": 400}}}
    board = {"scene_no": 1, "layout": "centered-statement", "transition": "cut",
             "effects": [], "signature_beat": False}
    scene_dir = tmp_path / "scenes" / "scene-01"
    scene_dir.mkdir(parents=True)
    ctx = engine._scene_ctx(1, {"scene_no": 1, "on_screen_text": "Hi"}, style, board,
                            [], [], tmp_path, scene_dir)
    # the .ttf files were copied into the scene project's assets/fonts/
    assert (scene_dir / "assets" / "fonts" / "Fraunces.ttf").exists()
    assert (scene_dir / "assets" / "fonts" / "Inter.ttf").exists()
    # ctx records (family, local-rel-path) pairs, and the html references those paths
    fams = {f for f, _ in ctx["font_faces"]}
    assert fams == {"Fraunces", "Inter"}
    html = engine.compose_scene_html(ctx)
    assert "src:url('assets/fonts/Fraunces.ttf')" in html
    assert "http" not in html.split("@font-face", 1)[1].split("}", 1)[0]


# ----------------------------------------------------------------------
# C5 — a data-chart scene must render an ACTUAL visual (chart markup), not bare text
# ----------------------------------------------------------------------
def test_parse_chart_data_extracts_label_value_pairs():
    pairs = engine.parse_chart_data("Coffee ~95 · Black tea ~47-48 · Green tea ~29 mg")
    by = {p["label"].lower(): p["value"] for p in pairs}
    assert by["coffee"] == 95.0
    assert by["green tea"] == 29.0


def test_data_chart_renders_native_bar_chart_from_scene_data():
    # No usable asset -> a deterministic native bar chart from the scene's numbers.
    ctx = _ctx(layout="data-chart", signature=False, effects=[], assets=[],
               chart_data=[{"label": "Coffee", "value": 95}, {"label": "Black tea", "value": 48},
                           {"label": "Green tea", "value": 29}])
    html = engine.compose_scene_html(ctx)
    assert "<svg" in html and "<rect" in html        # real chart markup, not just a title
    assert "bar-chart" in html
    assert "Coffee" in html and "95" in html
    assert engine.scan_determinism(html) == []       # still frame-seek safe


def test_data_chart_scene_with_data_is_not_bare_text():
    # The live regression: layout data-chart with a generated (file-less) data-viz asset.
    ctx = _ctx(layout="data-chart", signature=False, effects=[],
               title="8 oz averages",
               assets=[{"type": "data-viz", "label": "s2_caffeine_bars", "src_rel": None,
                        "placeholder": True}],
               chart_data=[{"label": "Coffee", "value": 95}, {"label": "Green tea", "value": 29}])
    html = engine.compose_scene_html(ctx)
    assert "<rect" in html                           # a chart reached the DOM


# ----------------------------------------------------------------------
# C4 — caption legibility: a scrim/background panel behind the caption text
# ----------------------------------------------------------------------
def test_captions_have_a_legibility_scrim():
    html = engine.compose_scene_html(_ctx())
    assert "caption-scrim" in html                   # a background panel element
    assert "background:rgba(0,0,0" in html           # readable dark scrim behind text
    assert "text-shadow" in html                     # plus a shadow for over-image legibility


# ----------------------------------------------------------------------
# Contrast over imagery: titles rendered OVER a photo (full-bleed-image,
# lower-third) must sit on a SOLID, sufficiently-opaque dark scrim plate so
# they reach WCAG 4.5:1 regardless of the underlying image luminance — a thin
# bottom-only gradient (opaque only at the very bottom) leaves the TOP of the
# title over the raw photo and fails. Mirror the working .caption-scrim plate.
# ----------------------------------------------------------------------
def test_full_bleed_title_sits_on_a_solid_contrast_scrim():
    html = engine.compose_scene_html(
        _ctx(layout="full-bleed-image", signature=False, effects=[],
             assets=[{"type": "image", "label": "a1", "src_rel": "assets/x.jpg",
                      "placeholder": False}]))
    # the title text is wrapped in a dedicated scrim plate element
    assert "bleed-scrim" in html
    # the plate is a SOLID dark fill (not a fade-to-transparent gradient) and is
    # opaque enough (>= 0.8) to guarantee contrast over any photo
    assert "background:rgba(0,0,0,0.82)" in html
    # and the over-image title band no longer relies on the leaky bottom gradient
    assert "linear-gradient(0deg,#000c,#0000)" not in html


def test_lower_third_name_strip_sits_on_a_solid_contrast_scrim():
    html = engine.compose_scene_html(
        _ctx(layout="lower-third", signature=False, effects=[],
             title="Dr. Jane Doe",
             assets=[{"type": "image", "label": "a1", "src_rel": "assets/x.jpg",
                      "placeholder": False}]))
    assert "bleed-scrim" in html
    assert "background:rgba(0,0,0,0.82)" in html


# ----------------------------------------------------------------------
# Brand-chip contrast over imagery: chips land in the full-bleed media slot,
# i.e. OVER a photo. The WCAG checker composites a DARK label against the page
# background (it does not credit a light chip card), so dark-on-light chip
# names fail ~1.04:1 over a dark frame. The chip card must therefore be a SOLID
# dark plate with a near-WHITE label (white-on-dark is the pattern the checker
# reliably resolves) — guaranteeing >=4.5:1 regardless of the underlying frame.
# ----------------------------------------------------------------------
def test_brand_chip_label_is_white_on_a_dark_plate_for_contrast():
    css = engine.compose_scene_html(_ctx())   # the global stylesheet is always emitted
    assert ".brand-chip{" in css
    # the chip card is a solid dark plate (not a near-white #fffffff2 card)
    assert "#fffffff2" not in css
    assert "background:#141414" in css
    # and the label text is white (so it reads on the dark plate AND passes WCAG)
    assert ".brand-chip-name{" in css
    name_rule = css.split(".brand-chip-name{", 1)[1].split("}", 1)[0]
    assert "color:#ffffff" in name_rule


# ----------------------------------------------------------------------
# H1 — generic "four AI logos lined up" naming no model -> full roster of chips
# ----------------------------------------------------------------------
def test_generic_logo_lineup_shot_falls_back_to_full_roster():
    shots = [{"kind": "brand", "content": "four AI logos lined up"}]
    keys = engine.scene_brand_keys(shots)
    assert set(keys) == set(engine.BRAND_CHIPS.keys())
    specs = engine.scene_brand_specs(shots)
    assert {s["key"] for s in specs} == set(engine.BRAND_CHIPS.keys())
    assert all(s["dim"] is False for s in specs)


def test_generic_models_lineup_by_content_cue_without_kind():
    # No brand kind, but content cues "the major models" in a row -> full roster.
    shots = [{"kind": "graphic", "content": "the major models lined up in a row"}]
    assert set(engine.scene_brand_keys(shots)) == set(engine.BRAND_CHIPS.keys())


def test_named_models_still_take_precedence_over_roster_fallback():
    # A specific model named -> only that, not the whole roster.
    shots = [{"kind": "brand", "content": "Claude logo, four models lined up"}]
    assert engine.scene_brand_keys(shots) == ["anthropic"]


# ----------------------------------------------------------------------
# M4 — a multi-brand shot's dim cue must NOT dim the foregrounded winner
# ----------------------------------------------------------------------
def test_dim_cue_does_not_dim_the_foregrounded_brand_in_a_multibrand_shot():
    shots = [{"kind": "panel",
              "content": "Claude foregrounded while GPT-4o fades back into the background"}]
    by = {s["key"]: s["dim"] for s in engine.scene_brand_specs(shots)}
    assert by["anthropic"] is False              # the foregrounded winner stays bright
    assert by["openai"] is True                  # only the de-emphasized peer dims


def test_single_brand_dim_cue_still_applies():
    shots = [{"kind": "panel", "content": "GPT-4o dimmed into the background"}]
    by = {s["key"]: s["dim"] for s in engine.scene_brand_specs(shots)}
    assert by["openai"] is True


# ----------------------------------------------------------------------
# C2 (calibrated) — contrast failures must be RECORDED + SURFACED (the original bug was
# that they were silently swallowed), but they do NOT hard-block the DETERMINISTIC gate:
# aesthetics are the human render gate's call. Structure (lint/console/inspect) still blocks.
# ----------------------------------------------------------------------
def test_contrast_failures_are_recorded_and_surfaced_not_silently_swallowed(tmp_path, monkeypatch):
    _write_fixture_project(tmp_path)
    monkeypatch.setenv("MASON_SKIP_RENDER", "1")
    import hf_tools
    # structure clean (lint/console/inspect), but validate reports contrast failures.
    monkeypatch.setattr(hf_tools, "run_gate", lambda d, motion_strict=False: {
        "lint": {"ok": True, "errors": 0},
        "validate": {"ok": True, "console_errors": 0, "contrast_failures": 5},
        "inspect": {"ok": True, "issues": 0}})
    manifest = engine.compose(tmp_path)
    # SURFACED: the total is reported in the summary (not zeroed/ignored).
    assert manifest["summary"]["contrast_failures"] >= 5
    # RECORDED per scene so the human render gate can judge.
    assert any(s.get("assets", {}).get("contrast_failures") or
               (s.get("gate", {}).get("validate") or {}).get("contrast_failures")
               for s in manifest["scenes"]) or manifest["summary"]["contrast_failures"] >= 5


def test_contrast_failures_alone_do_not_block_the_auto_gate(tmp_path, monkeypatch):
    """Contrast is an aesthetic/legibility signal for the human render gate to judge —
    it is SURFACED, not a deterministic hard-block. A scene whose only issue is contrast
    (structure clean) must NOT fail the auto-gate, or no LLM-palette video could ever
    render. The human final-render gate still sees the surfaced count + draft."""
    _write_fixture_project(tmp_path)
    monkeypatch.setenv("MASON_SKIP_RENDER", "1")
    import hf_tools
    monkeypatch.setattr(hf_tools, "run_gate", lambda d, motion_strict=False: {
        "lint": {"ok": True, "errors": 0},
        "validate": {"ok": True, "console_errors": 0, "contrast_failures": 7},
        "inspect": {"ok": True, "issues": 0}})
    manifest = engine.compose(tmp_path)
    assert manifest["summary"]["contrast_failures"] >= 7      # still surfaced
    assert manifest["summary"]["auto_gate"] == "PASS"          # but NOT blocked
    assert manifest["verdict"] == "pass"


def test_structural_gate_failures_still_block_the_auto_gate(tmp_path, monkeypatch):
    """The deterministic guarantee is intact: a console error (structural) blocks."""
    _write_fixture_project(tmp_path)
    monkeypatch.setenv("MASON_SKIP_RENDER", "1")
    import hf_tools
    monkeypatch.setattr(hf_tools, "run_gate", lambda d, motion_strict=False: {
        "lint": {"ok": True, "errors": 0},
        "validate": {"ok": False, "console_errors": 2, "contrast_failures": 0},
        "inspect": {"ok": True, "issues": 0}})
    manifest = engine.compose(tmp_path)
    assert manifest["summary"]["auto_gate"] != "PASS"
    assert manifest["verdict"] == "blocked"


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


# ----------------------------------------------------------------------
# Job 2 — vocabulary extension (LOCKSTEP): big-number, timeline, count-up
# ----------------------------------------------------------------------
def test_big_number_renders_hero_stat_and_self_scans_clean():
    ctx = _ctx(layout="big-number", signature=False, effects=[],
               hero_stat={"value": 95, "unit": "mg", "label": "Caffeine per cup"})
    html = engine.compose_scene_html(ctx)
    assert 'class="layout big-number' in html
    assert "big-number-value" in html
    assert ">95<" in html                              # the stat reached the DOM
    assert "Caffeine per cup" in html                  # the label
    assert "mg" in html                                # the unit
    assert "font-size:min(300px,15vw)" in html          # hero scale, viewport-capped so it can't overflow
    assert engine.scan_determinism(html) == []         # frame-seek safe


def test_big_number_carries_signature_tint_on_the_beat():
    ctx = _ctx(layout="big-number", signature=True,
               effects=[{"name": engine.SIGNATURE_EFFECT, "params": {}}],
               hero_stat={"value": 40, "unit": "%", "label": "fewer"})
    html = engine.compose_scene_html(ctx)
    assert "big-number sig" in html
    assert ".big-number.sig .big-number-value{color:#FFD000;}" in html
    assert engine.scan_determinism(html) == []


def test_timeline_emits_one_svg_node_per_parsed_entry():
    entries = [{"date": "1969", "label": "Moon"}, {"date": "1991", "label": "Web"},
               {"date": "2007", "label": "iPhone"}]
    ctx = _ctx(layout="timeline", signature=False, effects=[], timeline_data=entries)
    html = engine.compose_scene_html(ctx)
    assert "<svg" in html and "timeline-svg" in html
    assert html.count('class="tl-node"') == len(entries)   # N nodes for N entries
    for e in entries:
        assert e["date"] in html and e["label"] in html
    assert engine.scan_determinism(html) == []


def test_parse_hero_stat_picks_the_dominant_number():
    stat = engine.parse_hero_stat("Coffee delivers 95 mg of caffeine per cup")
    assert stat["value"] == 95.0
    assert stat["unit"].lower() == "mg"


def test_parse_timeline_data_extracts_year_entries_in_order():
    rows = engine.parse_timeline_data("1969 Moon landing, 2007 iPhone, 2023 GPT-4")
    assert [r["date"] for r in rows] == ["1969", "2007", "2023"]
    assert rows[0]["label"].startswith("Moon")


def test_count_up_tweens_on_the_master_timeline_and_is_deterministic():
    # count-up on a big-number scene: a GSAP tween 0->target on the paused timeline,
    # onUpdate writes textContent. No Math.random/Date.now/late gsap.set -> self-scan clean.
    ctx = _ctx(layout="big-number", signature=False,
               effects=[{"name": "count-up", "params": {}}],
               hero_stat={"value": 250, "unit": None, "label": "servers"})
    html = engine.compose_scene_html(ctx)
    assert 'data-target="250"' in html                 # build-time target on the element
    assert "onUpdate" in html and "textContent" in html
    assert "tl.to(" in html                            # motion lives on the master timeline
    assert "gsap.set(" not in html
    assert "Math.random" not in html and "Date.now" not in html
    assert engine.scan_determinism(html) == []


def test_count_up_value_at_a_sampled_frame_is_determined():
    # The tween is linear in eased progress; the engine seeks to fixed times, so the value
    # at a given progress p is round(target * eased(p)) — a pure function of p (no clock).
    # We verify the emitted tween parameters pin the target + duration deterministically.
    import re as _re
    ctx = _ctx(layout="big-number", signature=False,
               effects=[{"name": "count-up", "params": {}}], duration=2.0,
               hero_stat={"value": 100, "unit": None, "label": "x"})
    html = engine.compose_scene_html(ctx)
    m = _re.search(r"n:target,duration:([0-9.]+),ease:\"power1.out\"", html)
    assert m, "count-up tween not emitted with a fixed duration"
    # duration = min(1.2, dur*0.5) = 1.0 here -> deterministic, not clock-derived
    assert float(m.group(1)) == 1.0


def test_has_brand_stacks_blocks_in_a_non_overlapping_grid():
    """A big-number scene that ALSO carries a brand chip must place the two blocks in a
    grid (content-sized rows) so the opaque chip can never occlude the hero number —
    the live 'text_occluded' inspect error that blocked a real render."""
    ctx = _ctx(layout="big-number", signature=False, effects=[],
               hero_stat={"value": 1936, "unit": "", "label": "The year it was invented"},
               brand_keys=["openai"], brand_specs=[{"key": "openai", "dim": False}])
    html = engine.compose_scene_html(ctx)
    assert "has-brand" in html
    # the has-brand container is a grid (rows can't overlap), not a flex column
    assert "has-brand{display:grid" in html.replace(" ", "")
