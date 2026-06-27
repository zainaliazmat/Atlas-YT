"""Task C-WIRE integration tests: archetype registry wired into real compose.

Tests verify:
1. REGISTRY auto-loads on package import (no explicit builder import needed).
2. A real compose() routes beats_js into the <script> block, not the <section> body.
3. Archetype beats do NOT emit the generic makeOutlineDraw for the quote scene sid.
4. Builder beats_js is anchored at ctx["at"] (scene's authored start), not t=0.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from studio import config


# ---------------------------------------------------------------------------
# Shared fixture: one quote-card scene (attributed quote claim)
# ---------------------------------------------------------------------------

@pytest.fixture
def mini_project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    pdir = tmp_path / "dark-truth-social"
    pdir.mkdir()
    (pdir / "research_brief.json").write_text(json.dumps({"topic": "t"}))
    (pdir / "script.json").write_text(json.dumps({
        "working_title": "Wire Test",
        "scenes": [
            {
                "scene_no": 1,
                "on_screen_text": "THE MACHINE",
                "point": "p",
                "narration": "n",
                "duration_est_sec": 6,
                "claims": [
                    {
                        "claim_id": "c1",
                        "text": '"Pull-to-refresh is addictive." — Loren Brichter',
                        "source_ref": "F1",
                    }
                ],
            }
        ],
    }))
    return pdir


# ---------------------------------------------------------------------------
# Test 1: REGISTRY auto-populates on import (without explicit builder import)
# ---------------------------------------------------------------------------

def test_registry_autoloads_on_import():
    """Importing archetypes package alone must populate REGISTRY (auto-loader fires).

    This is the key regression: before the fix, `from studio.compose import archetypes`
    left REGISTRY == {} unless the test explicitly imported quote_cards.
    """
    # Force a fresh import by reimporting the package
    # (tests may run after other tests have already loaded builders — that's fine:
    # the auto-loader must have run, so quote-card MUST be present.)
    from studio.compose import archetypes as A
    assert "quote-card" in A.REGISTRY, (
        "REGISTRY is empty — auto-loader did not fire on package import. "
        "Add _load_builders() to studio/compose/archetypes/__init__.py"
    )


# ---------------------------------------------------------------------------
# Test 2: beats_js reaches the <script> block, NOT the <section> body
# ---------------------------------------------------------------------------

def test_archetype_beats_js_reaches_the_script_not_the_section(mini_project):
    """After compose(), the quote-card builder's JS (makeHighlighterSwipe CALL with
    mount: _body) must appear inside the trailing <script> block, not in the <section>
    HTML body.

    Discriminator: the factory definition uses destructuring `makeHighlighterSwipe({tl, mount,...})`
    but the BUILDER CALL emits `mount: _body,` (a variable), so we check for
    `mount: _body` which only appears in the builder's invocation, not the factory.
    """
    from studio.compose import compose

    out = compose("dark-truth-social", pack_id="dark-truth-social")
    html_text = Path(out).read_text(encoding="utf-8")

    # Split at the choreography <script> opening tag
    script_split = html_text.split("<script>")
    assert len(script_split) >= 2, "No <script> block found in composed HTML"

    html_body = script_split[0]
    script_body = "<script>".join(script_split[1:])

    # The builder's CALL uses `mount: _body,` (a JS variable), which only the
    # builder emits (the factory definition uses destructuring syntax, not `mount: _body`)
    assert "mount: _body," in script_body, (
        "Builder's makeHighlighterSwipe call (mount: _body) not found in the <script> "
        "block — beats_js is not being routed through _beat_js into the choreography. "
        "Check: (1) _load_builders() auto-loads the builder, (2) _scene_beat returns "
        "beats_js in the beat descriptor, (3) _beat_js early-returns beat['beats_js']."
    )

    # The <section> body must NOT contain the builder's GSAP call
    section_match = re.search(
        r'<section[^>]*id="s1"[^>]*>.*?</section>', html_body, re.DOTALL
    )
    if section_match:
        section_text = section_match.group(0)
        assert "mount: _body," not in section_text, (
            "Builder's GSAP call found inside the s1 <section> body — "
            "beats_js is being concatenated into extra_html instead of returned "
            "as a beat descriptor for the choreography"
        )


# ---------------------------------------------------------------------------
# Test 3: Archetype scene does NOT emit the generic makeOutlineDraw for its sid
# ---------------------------------------------------------------------------

def test_archetype_scene_does_not_emit_generic_underline(mini_project):
    """When a scene is dispatched to an archetype builder, _beat_js must emit the
    builder's beats_js verbatim, NOT the generic makeOutlineDraw underline for that sid.
    """
    from studio.compose import compose

    out = compose("dark-truth-social", pack_id="dark-truth-social")
    html_text = Path(out).read_text(encoding="utf-8")

    script_split = html_text.split("<script>")
    script_body = "<script>".join(script_split[1:])

    # The generic underline for s1 would look like:
    # makeOutlineDraw({ tl: tl, mount: "#s1 .fx", at: ...
    # This must NOT appear for the quote-card scene (s1).
    assert 'makeOutlineDraw({ tl: tl, mount: "#s1 .fx"' not in script_body, (
        "Generic makeOutlineDraw for s1 found in script — the archetype builder's "
        "beats_js is not replacing the generic beat. Check _beat_js early-return."
    )


# ---------------------------------------------------------------------------
# Test 4: beats_js is anchored at ctx["at"] (scene's authored start), not t=0
# ---------------------------------------------------------------------------

def test_beats_anchored_at_scene_start():
    """Builder beats_js must embed the base time from ctx['at'], not hardcode t=0.

    This ensures that a quote-card scene at t=18.3s gets its entry tweens anchored
    at 18.3, not at absolute 0 (which would land in scene 1's window).
    """
    from studio.compose import archetypes as A

    # Ensure builder is loaded (after fix, auto-loader guarantees this)
    assert "quote-card" in A.REGISTRY, "quote-card not in REGISTRY"

    scene = {
        "scene_no": 5,
        "on_screen_text": "QUOTE SCENE",
        "narration": "Here is what they said.",
        "duration_est_sec": 8,
        "claims": [
            {
                "claim_id": "c1",
                "text": '"Pull-to-refresh is addictive." — Loren Brichter',
                "source_ref": "F1",
            }
        ],
    }
    ctx = {
        "sid": "s5",
        "spray": "#2e5e1f",
        "ink": "#1f1f1e",
        "width": 1920,
        "height": 1080,
        "at": 18.3,
    }
    result = A.REGISTRY["quote-card"](scene, ctx)
    beats_js = result["beats_js"]

    # The base time 18.3 must appear in the beats_js
    assert "18.3" in beats_js, (
        f"Expected base time 18.3 in beats_js but it was not found.\n"
        f"beats_js:\n{beats_js}\n\n"
        "The builder must read ctx['at'] and anchor tweens at that offset, "
        "not at absolute 0."
    )

    # Sanity: must still call makeHighlighterSwipe and tl.from
    assert "makeHighlighterSwipe" in beats_js
    assert "tl.from" in beats_js
