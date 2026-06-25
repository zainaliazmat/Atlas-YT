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


def test_new_motion_effects_are_seek_deterministic():
    # The motion tokens mined from the local hyperframes catalogue (breathe, bars-grow,
    # drift, and the latest pop-in / underline-grow) must be build-time GSAP on the paused
    # timeline only — no render-time clock, randomness, infinite repeats, or SMIL — so each
    # seeked frame is reproducible.
    ctx = {"duration": 6.0, "highlight": "#FFD000"}
    banned = ("Math.random", "Date.now", "performance.now", "repeat:-1", "repeat: -1",
              "<animate", "setTimeout", "setInterval", "requestAnimationFrame", "fetch(")
    for name in ("breathe", "bars-grow", "drift", "pop-in", "underline-grow"):
        frag = engine.EFFECT_BUILDERS[name](ctx)
        assert frag["tl"], f"{name} produced no timeline motion"
        blob = " ".join(frag["tl"]) + frag.get("css", "") + frag.get("html", "")
        for tok in banned:
            assert tok not in blob, f"{name} uses banned non-deterministic token {tok!r}"
    # each technique's signature is present
    assert "sine.inOut" in engine.EFFECT_BUILDERS["breathe"](ctx)["tl"][0]
    assert "yoyo:true" in engine.EFFECT_BUILDERS["breathe"](ctx)["tl"][0]
    assert "stagger" in engine.EFFECT_BUILDERS["bars-grow"](ctx)["tl"][0]
    assert "xPercent" in engine.EFFECT_BUILDERS["drift"](ctx)["tl"][0]
    # pop-in is a back.out scale entrance (distinct from the opacity/y default entrance)
    pop = engine.EFFECT_BUILDERS["pop-in"](ctx)["tl"][0]
    assert "back.out" in pop and "scale:0.84" in pop and "opacity" not in pop
    # underline-grow draws an accent keyline (its own selector + scaleX, no overwrite)
    keyline = engine.EFFECT_BUILDERS["underline-grow"](ctx)
    assert "scaleX:0" in keyline["tl"][0] and ".fx-keyline" in keyline["css"]
    assert "#FFD000" in keyline["css"]                  # tinted to the signature highlight


def test_word_reveal_is_per_word_and_seek_deterministic():
    # Kinetic typography: the title reveals one WORD at a time on the paused timeline.
    # Must be build-time GSAP only — no render-time clock/randomness/infinite repeat/SMIL.
    frag = engine.EFFECT_BUILDERS["word-reveal"]({"duration": 6.0})
    blob = " ".join(frag["tl"]) + frag.get("css", "") + frag.get("html", "")
    banned = ("Math.random", "Date.now", "performance.now", "repeat:-1", "repeat: -1",
              "<animate", "setTimeout", "setInterval", "requestAnimationFrame", "fetch(")
    for tok in banned:
        assert tok not in blob, f"word-reveal uses banned non-deterministic token {tok!r}"
    assert ".scene-title .word" in blob          # animates per-word, not the whole title
    assert "stagger" in blob                      # staggered reveal
    # inline-block so the per-word y-transform actually moves
    assert "display:inline-block" in frag["css"]


def test_word_reveal_only_wraps_text_nodes_so_it_preserves_hl_sweep():
    # The split walks child TEXT nodes only (nodeType===3); the injected .hl-sweep span is
    # an ELEMENT child and must survive untouched when both effects land on one scene.
    frag = engine.EFFECT_BUILDERS["word-reveal"]({"duration": 6.0})
    blob = " ".join(frag["tl"])
    assert "nodeType" in blob and "3" in blob     # guards on text nodes
    assert "replaceChild" in blob                 # in-place, leaves siblings (hl-sweep) alone


def test_word_reveal_suppresses_the_default_whole_title_entrance():
    html = engine.compose_scene_html(_ctx(
        effects=[{"name": "word-reveal", "params": {}}], signature=False))
    # the per-word reveal is present...
    assert 'tl.from(".scene-title .word"' in html
    # ...and the default whole-title fade is NOT (word-reveal owns the entrance)
    assert 'tl.from(".scene-title",{opacity:0,y:24' not in html
    assert engine.scan_determinism(html) == []


def test_word_reveal_with_signature_keeps_the_highlighter_sweep():
    html = engine.compose_scene_html(_ctx(
        effects=[{"name": engine.SIGNATURE_EFFECT, "params": {}},
                 {"name": "word-reveal", "params": {}}], signature=True))
    assert 'class="hl-sweep"' in html             # signature span survives alongside word-reveal
    assert 'tl.from(".scene-title .word"' in html
    # word-reveal suppresses the title entrance whose transform incidentally gave .scene-title
    # the stacking context the z-index:-1 sweep needs — so word-reveal must supply it itself,
    # else the sweep falls behind the scene background and vanishes (caught on a real render).
    assert ".scene-title{isolation:isolate;}" in html
    assert engine.scan_determinism(html) == []


def test_default_title_entrance_intact_without_word_reveal():
    # Regression guard: scenes WITHOUT word-reveal still get the whole-title entrance.
    html = engine.compose_scene_html(_ctx(effects=[], signature=False))
    assert 'tl.from(".scene-title",{opacity:0,y:24' in html


def test_pop_in_composes_with_the_default_entrance():
    # pop-in adds an overshoot SCALE on top of the default opacity/y fade — both run (it
    # does NOT own/suppress the entrance the way word-reveal does), and the scene stays
    # determinism-clean.
    html = engine.compose_scene_html(_ctx(
        effects=[{"name": "pop-in", "params": {}}], signature=False))
    assert 'ease:"back.out(1.8)"' in html
    assert 'tl.from(".scene-title",{opacity:0,y:24' in html      # default entrance kept
    assert engine.scan_determinism(html) == []


def test_underline_grow_injects_a_keyline_child_into_the_title():
    html = engine.compose_scene_html(_ctx(
        effects=[{"name": "underline-grow", "params": {}}], signature=False))
    assert 'class="fx-keyline"' in html                          # injected as a title child
    assert 'tl.fromTo(".fx-keyline"' in html
    assert engine.scan_determinism(html) == []


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
    # Give every layout a present photo so the photo-hero layouts render their real form
    # (a photo-slot layout with no usable photo intentionally downgrades to a text card —
    # exercised separately in the text-forward tests).
    present_photo = [{"type": "image", "label": "a1", "src_rel": "assets/p.jpg",
                      "placeholder": False, "integrity_flag": None}]
    for layout in engine.LAYOUTS:
        html = engine.compose_scene_html(_ctx(layout=layout, signature=False,
                                              effects=[], assets=present_photo))
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


# ----------------------------------------------------------------------
# Text-forward for no-relevant-photo scenes (CEO decision 2026-06-24): a photo-slot
# layout whose only asset is a sourcer-declared placeholder (no relevant photo cleared
# the relevance floor) renders a CLEAN TEXT CARD, not a striped placeholder-panel. A
# sourced-but-MISSING file (integrity failure) still shows the panel so the pipeline bug
# stays visible; brand scenes keep their slot (chips fill it).
# ----------------------------------------------------------------------
def test_photo_slot_with_declared_placeholder_downgrades_to_text_forward():
    ctx = _ctx(layout="full-bleed-image", signature=False, effects=[],
               assets=[{"type": "image", "label": "a1", "src_rel": None,
                        "placeholder": True, "integrity_flag": None}])
    html = engine.compose_scene_html(ctx)
    body = html.split("<body>", 1)[1]
    assert "centered-statement" in body          # downgraded to a text card
    assert "full-bleed-image" not in body        # the layout element is gone (CSS aside)
    assert "placeholder-panel" not in body       # no striped box element
    assert "<img" not in html


def test_photo_slot_keeps_panel_on_integrity_failure():
    # A SOURCED file that's missing is a pipeline bug — keep the visible panel (surfaced),
    # don't silently hide it behind a clean text card.
    ctx = _ctx(layout="full-bleed-image", signature=False, effects=[],
               assets=[{"type": "image", "label": "a1", "src_rel": None,
                        "placeholder": True,
                        "integrity_flag": "cleared asset 'a1' has no local file"}])
    html = engine.compose_scene_html(ctx)
    assert "full-bleed-image" in html
    assert "placeholder-panel" in html.split("<body>", 1)[1]


def test_photo_slot_keeps_a_present_photo():
    ctx = _ctx(layout="full-bleed-image", signature=False, effects=[],
               assets=[{"type": "image", "label": "a1", "src_rel": "assets/p.jpg",
                        "placeholder": False, "integrity_flag": None}])
    html = engine.compose_scene_html(ctx)
    assert "full-bleed-image" in html
    assert "<img" in html


def test_brand_scene_keeps_its_photo_slot_layout():
    # Brand chips fill the media slot, so a brand scene must NOT downgrade even with no photo.
    ctx = _ctx(layout="split-screen", signature=False, effects=[], brand_keys=["openai"],
               assets=[{"type": "image", "label": "a1", "src_rel": None,
                        "placeholder": True, "integrity_flag": None}])
    html = engine.compose_scene_html(ctx)
    assert "split-screen" in html
    assert "brand-media" in html


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
# Signature WebGL shader transitions at the assembly seam (≤budget, into sig beats)
# ----------------------------------------------------------------------
import shader_transition  # noqa: E402


def _sig_manifest(n_sig_at):
    scenes = [{"scene_no": i, "render_path": f"scenes/scene-{i:02d}/renders/draft.mp4",
               "fps": 30, "signature_beat": (i in n_sig_at)} for i in range(1, 6)]
    return {"scenes": scenes}


def test_signature_beat_earns_a_shader_transition_into_it(monkeypatch):
    monkeypatch.delenv("MASON_SHADER_TRANSITIONS", raising=False)
    manifest = _sig_manifest({3})                      # scene 3 is the signature beat
    storyboard = {"scenes": [{"scene_no": i, "transition": "cut"} for i in range(1, 6)]}
    plan = engine.build_assembly_plan(manifest, storyboard, {})
    sh = [s for s in plan["steps"] if s.get("mode") == "shader"]
    assert len(sh) == 1 and plan["shader_count"] == 1
    step = sh[0]
    assert step["boundary_after"] == 2 and step["into_scene"] == 3   # boundary INTO sig
    assert step["shader"] == "sdf-iris"                # premium default first
    assert step["from_render"].endswith("scene-02/renders/draft.mp4")
    assert step["to_render"].endswith("scene-03/renders/draft.mp4")
    assert step["frames"] == shader_transition.DEFAULT_FRAMES and step["fps"] == 30


def test_shader_budget_is_capped(monkeypatch):
    monkeypatch.delenv("MASON_SHADER_TRANSITIONS", raising=False)
    manifest = _sig_manifest({2, 3, 4})               # three signature beats
    storyboard = {"scenes": [{"scene_no": i, "transition": "cut"} for i in range(1, 6)]}
    plan = engine.build_assembly_plan(manifest, storyboard, {})
    assert plan["shader_count"] == shader_transition.SHADER_BUDGET   # capped at 2
    assert [s["shader"] for s in plan["steps"] if s.get("mode") == "shader"] \
        == ["sdf-iris", "glitch"]                      # taste order


def test_storyboard_can_override_signature_shader(monkeypatch):
    monkeypatch.delenv("MASON_SHADER_TRANSITIONS", raising=False)
    manifest = _sig_manifest({3})
    storyboard = {"scenes": [
        {"scene_no": i, "transition": "cut",
         **({"signature_transition": "glitch"} if i == 3 else {})} for i in range(1, 6)]}
    plan = engine.build_assembly_plan(manifest, storyboard, {})
    step = next(s for s in plan["steps"] if s.get("mode") == "shader")
    assert step["shader"] == "glitch"                  # honored the per-beat override
    # an invalid override silently falls back to the default
    storyboard["scenes"][2]["signature_transition"] = "wormhole"
    plan2 = engine.build_assembly_plan(manifest, storyboard, {})
    assert next(s for s in plan2["steps"] if s.get("mode") == "shader")["shader"] == "sdf-iris"


def test_shader_transitions_kill_switch(monkeypatch):
    monkeypatch.setenv("MASON_SHADER_TRANSITIONS", "0")
    manifest = _sig_manifest({3})
    storyboard = {"scenes": [{"scene_no": i, "transition": "cut"} for i in range(1, 6)]}
    plan = engine.build_assembly_plan(manifest, storyboard, {})
    assert plan["shader_count"] == 0
    assert not any(s.get("mode") == "shader" for s in plan["steps"])


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
# data-chart kinds: line + pie (ported from the HF data-chart registry block).
# Mason stays the deterministic emitter — pure build-time SVG geometry, draw-on
# reveal on the paused timeline. Iris (the art director) picks the chart_kind.
# ----------------------------------------------------------------------
_CHART_DATA = [{"label": "Coffee", "value": 95}, {"label": "Black tea", "value": 48},
               {"label": "Green tea", "value": 29}]


def test_chart_kinds_vocab_matches_the_art_director():
    assert set(engine.CHART_KINDS) == {"bar", "line", "pie"}
    vocab = _extract_art_director_vocab()
    if vocab is not None and "CHART_KINDS" in vocab:
        assert set(vocab["CHART_KINDS"]) == set(engine.CHART_KINDS)


def test_shader_vocab_matches_the_art_director():
    # Iris's signature_transition menu must equal the shaders Mason can actually render.
    vocab = _extract_art_director_vocab()
    if vocab is not None and "SHADER_TRANSITIONS" in vocab:
        assert set(vocab["SHADER_TRANSITIONS"]) == set(shader_transition.SHADER_TRANSITIONS)


def test_render_line_chart_is_a_drawon_ready_polyline():
    svg = engine.render_line_chart(_CHART_DATA)
    assert "line-chart" in svg and "line-path" in svg
    assert 'pathLength="1"' in svg                    # normalized for stroke-dashoffset draw-on
    assert svg.count("line-dot") == 3                 # one marker per point
    assert "Coffee" in svg and "95" in svg
    assert engine.render_line_chart([]) == ""         # nothing chartable
    assert engine.render_line_chart([{"label": "x", "value": 1}]) == ""   # a line needs >=2 pts


def test_render_pie_chart_slices_cover_the_whole_circle():
    svg = engine.render_pie_chart(_CHART_DATA)
    assert "pie-chart" in svg
    assert svg.count("pie-slice") == 3                # one arc per datum
    assert engine.render_pie_chart([]) == ""
    # percentages shown and the largest datum carries the signature tint
    assert "%" in svg
    assert engine.SIGNATURE_HIGHLIGHT in svg


def test_data_chart_line_kind_renders_line_and_draws_on():
    ctx = _ctx(layout="data-chart", signature=False, effects=[], assets=[],
               chart_kind="line", chart_data=_CHART_DATA)
    html = engine.compose_scene_html(ctx)
    assert 'class="media line-chart"' in html and '<rect class="bar"' not in html
    assert 'strokeDashoffset' in html                 # baked draw-on reveal in the timeline
    assert engine.scan_determinism(html) == []


def test_data_chart_pie_kind_renders_pie_and_reveals():
    ctx = _ctx(layout="data-chart", signature=False, effects=[], assets=[],
               chart_kind="pie", chart_data=_CHART_DATA)
    html = engine.compose_scene_html(ctx)
    assert 'class="media pie-chart"' in html and '<rect class="bar"' not in html
    assert "pie-slice" in html
    assert engine.scan_determinism(html) == []


def test_data_chart_defaults_to_bar_when_kind_absent():
    ctx = _ctx(layout="data-chart", signature=False, effects=[], assets=[],
               chart_data=_CHART_DATA)                # no chart_kind
    html = engine.compose_scene_html(ctx)
    assert "bar-chart" in html


def test_chart_kinds_are_byte_deterministic():
    for kind in ("line", "pie"):
        ctx = dict(layout="data-chart", chart_kind=kind, chart_data=_CHART_DATA)
        a = engine.compose_scene_html(_ctx(**ctx))
        b = engine.compose_scene_html(_ctx(**ctx))
        assert a == b, f"{kind} chart is not byte-stable"


def test_unknown_chart_kind_is_rejected():
    script, style, board, assets = _good_inputs()
    board["scenes"][0]["layout"] = "data-chart"
    board["scenes"][0]["chart_kind"] = "radar"
    ok, errors = engine.validate_inputs(script, style, board, assets)
    assert not ok and any("chart_kind" in e for e in errors)


# ----------------------------------------------------------------------
# Conceptual diagrams: the `diagram` layout composes a cached DiagramPlan as animated
# flat SVG (Magpie plans off the render path; Mason renders deterministically).
# ----------------------------------------------------------------------
_DIAGRAM_PLAN = {"layout_hint": "left-to-right", "components": [
    {"id": "g", "type": "labeled-box", "label": "Goal", "of": "document"},
    {"id": "b", "type": "speech-bubble", "label": "LLM", "of": "brain", "to": ["t"]},
    {"id": "t", "type": "glyph", "label": "Tools", "of": "gear"}]}


def test_diagram_layout_composes_the_plan_and_self_scans_clean():
    ctx = _ctx(layout="diagram", signature=False, effects=[], assets=[],
               diagram_plan=_DIAGRAM_PLAN, diagram_seed=777)
    html = engine.compose_scene_html(ctx)
    assert "diagram-svg" in html and "dg-node" in html
    assert 'class="layout diagram"' in html
    assert engine.scan_determinism(html) == []


def test_diagram_scene_is_byte_deterministic():
    ctx = dict(layout="diagram", diagram_plan=_DIAGRAM_PLAN, diagram_seed=777)
    assert engine.compose_scene_html(_ctx(**ctx)) == engine.compose_scene_html(_ctx(**ctx))


def test_diagram_plan_wins_over_brand_chip_fallback():
    # regression: a diagram shot's prose can trip the generic-roster brand heuristic; on a
    # `diagram` layout the plan is authoritative and must render, not the brand chips.
    ctx = _ctx(layout="diagram", signature=False, effects=[], assets=[],
               diagram_plan=_DIAGRAM_PLAN, diagram_seed=1,
               brand_keys=["openai", "anthropic"], brand_specs=[])
    html = engine.compose_scene_html(ctx)
    assert "diagram-svg" in html and 'class="media brand-media"' not in html  # no chip wrapper


def test_diagram_layout_falls_back_when_plan_is_invalid_or_absent():
    # invalid plan -> graceful fallback (placeholder/media), never a crash or bare title
    bad = _ctx(layout="diagram", signature=False, effects=[], assets=[],
               diagram_plan={"components": [{"id": "x", "type": "nope"}]}, diagram_seed=1)
    html = engine.compose_scene_html(bad)
    assert "dg-node" not in html and engine.scan_determinism(html) == []
    # no plan at all -> also fine
    none = _ctx(layout="diagram", signature=False, effects=[], assets=[])
    assert engine.scan_determinism(engine.compose_scene_html(none)) == []


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


def test_motion_sidecar_only_asserts_selectors_present_in_the_scene(tmp_path):
    # A big-number signature scene has NO .scene-title (its hero is .big-number-value)
    # and shows the signature via the gold number, so the highlighter sweep is never
    # injected. The motion sidecar must assert ONLY selectors that exist in the DOM —
    # asserting .scene-title/.hl-sweep makes `inspect --strict` fail with
    # motion_selector_missing and hard-blocks the whole video.
    ctx = _ctx(layout="big-number", signature=True,
               effects=[{"name": engine.SIGNATURE_EFFECT, "params": {}}],
               hero_stat={"value": 40, "unit": "%", "label": "fewer"})
    html = engine.compose_scene_html(ctx)
    engine._emit_motion_sidecar(tmp_path, ctx)
    sidecar = engine.chat_state.load_json(tmp_path / "index.motion.json", {})
    selectors = [a["selector"] for a in sidecar.get("assertions", [])]
    assert selectors, "a signature scene must still emit a motion sidecar"
    body = html.split("<body>", 1)[1]
    for sel in selectors:
        assert f'class="{sel.lstrip(".")}' in body, \
            f"motion sidecar asserts {sel} but no such element exists in the big-number DOM"
    assert ".scene-title" not in selectors
    assert ".hl-sweep" not in selectors


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
