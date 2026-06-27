"""Tests for Task B3: Composer reads the archetype tag from storyboard.json and resolves
each scene's archetype = tag-if-present else archetypes.classify(scene).

Unit tests that exercise _archetype_for directly, without a full compose run.
"""
import json
from pathlib import Path
import pytest

from studio.compose import archetypes as A
from studio.compose import Composer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_composer(tmp_path, monkeypatch, storyboard_data=None):
    """Build a Composer whose pdir points at tmp_path/mini, optionally with a storyboard.json."""
    from studio import config
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    pdir = tmp_path / "mini"
    pdir.mkdir(exist_ok=True)
    if storyboard_data is not None:
        (pdir / "storyboard.json").write_text(json.dumps(storyboard_data), encoding="utf-8")
    c = Composer.__new__(Composer)
    c.slug = "mini"
    c.pdir = pdir
    c._storyboard = None  # will be loaded lazily or via _load_storyboard
    return c


# ---------------------------------------------------------------------------
# _archetype_for unit tests
# ---------------------------------------------------------------------------

class TestArchetypeFor:
    """Target: Composer._archetype_for(scene) -> str.

    Resolution rule:
      1. If self._storyboard has a tag for scene['scene_no'], return it.
      2. Otherwise return archetypes.classify(scene).
    """

    def test_storyboard_tag_wins_over_classify(self, tmp_path, monkeypatch):
        """When a storyboard tag is present for the scene, it is used regardless of
        what classify() would return."""
        c = _make_composer(tmp_path, monkeypatch)
        # Wire in the storyboard map directly (as author() would after loading)
        c._storyboard = {"1": "diagram"}
        scene = {"scene_no": 1, "on_screen_text": "141 users", "claims": []}
        # classify() would return "big-number" (digit present), but tag wins
        result = c._archetype_for(scene)
        assert result == "diagram"

    def test_absent_tag_falls_back_to_classify_big_number(self, tmp_path, monkeypatch):
        """When scene_no is not in the storyboard map, classify() decides."""
        c = _make_composer(tmp_path, monkeypatch)
        c._storyboard = {}
        scene = {"scene_no": 2, "on_screen_text": "141 users", "claims": []}
        result = c._archetype_for(scene)
        assert result == "big-number"

    def test_absent_tag_falls_back_to_classify_centered_statement(self, tmp_path, monkeypatch):
        """Without a digit or quote claim, classify() returns centered-statement."""
        c = _make_composer(tmp_path, monkeypatch)
        c._storyboard = {}
        scene = {"scene_no": 3, "on_screen_text": "THE MACHINE", "claims": []}
        result = c._archetype_for(scene)
        assert result == "centered-statement"

    def test_none_storyboard_falls_back_to_classify(self, tmp_path, monkeypatch):
        """self._storyboard = None (e.g. storyboard.json absent) falls back to classify."""
        c = _make_composer(tmp_path, monkeypatch)
        c._storyboard = None
        scene = {"scene_no": 1, "on_screen_text": "141 users", "claims": []}
        result = c._archetype_for(scene)
        assert result == "big-number"

    def test_storyboard_loaded_from_file_on_author(self, tmp_path, monkeypatch):
        """author() must load storyboard.json and populate self._storyboard so that
        _archetype_for can use the tags without a separate call."""
        from studio import config
        monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
        pdir = tmp_path / "proj"
        pdir.mkdir()
        (pdir / "storyboard.json").write_text(json.dumps({
            "scenes": [{"scene_no": 1, "archetype": "timeline"}]
        }), encoding="utf-8")
        (pdir / "research_brief.json").write_text(json.dumps({"topic": "t"}))
        (pdir / "script.json").write_text(json.dumps({
            "working_title": "T",
            "scenes": [{"scene_no": 1, "on_screen_text": "THE MACHINE",
                        "point": "p", "narration": "n", "duration_est_sec": 6, "claims": []}]
        }))
        # author() must succeed and self._storyboard must be populated
        c = Composer("proj", "dark-truth-social")
        c.author()
        assert c._storyboard == {"1": "timeline"}


# ---------------------------------------------------------------------------
# Registry dispatch (currently inert because REGISTRY is empty)
# ---------------------------------------------------------------------------

class TestRegistryDispatch:
    """When REGISTRY gains a builder, _scene_beat must call it for the resolved archetype.
    Test with a synthetic builder registered and then cleaned up."""

    def test_registry_builder_called_when_archetype_matches(self, tmp_path, monkeypatch):
        """If REGISTRY has a builder for the resolved archetype, _scene_beat returns
        html and beats_js from the builder."""
        from studio import config
        monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
        pdir = tmp_path / "proj"
        pdir.mkdir()

        called = []

        def fake_builder(scene, ctx):
            called.append(scene)
            return {"html": "<div class='custom-archetype'></div>",
                    "beats_js": "// custom beat",
                    "token": "custom-token"}

        # Register the fake builder temporarily
        A.REGISTRY["centered-statement"] = fake_builder
        A._TOKEN["centered-statement"] = "underline"  # token already exists
        try:
            c = Composer.__new__(Composer)
            c.slug = "proj"
            c.pdir = pdir
            from ..packs import load_pack
            c.pack = load_pack("dark-truth-social")
            c.tokens = c.pack.tokens
            c.colors = c.tokens.get("colors", {})
            from studio import config as cfg
            ad = c.pack.manifest.get("aspect_defaults", {})
            c.width = int(ad.get("width", cfg.DEFAULT_WIDTH))
            c.height = int(ad.get("height", cfg.DEFAULT_HEIGHT))
            c.fps = int(c.tokens.get("motion", {}).get("fps", cfg.DEFAULT_FPS))
            c.spray = c.colors.get("spray", "#2e5e1f")
            c.ink = c.colors.get("ink", "#1f1f1e")
            c._inlines = []
            # Storyboard: no tag → classify → centered-statement
            c._storyboard = {}
            scene = {"scene_no": 1, "on_screen_text": "THE MACHINE",
                     "point": "p", "narration": "n", "duration_est_sec": 6, "claims": []}
            beat, extra_html = c._scene_beat(0, scene, 0.0)
            assert len(called) == 1, "builder should have been called once"
            assert "<div class='custom-archetype'>" in extra_html or "custom-archetype" in extra_html
        finally:
            del A.REGISTRY["centered-statement"]

    def test_missing_registry_falls_through_to_existing_beat(self, tmp_path, monkeypatch):
        """When REGISTRY has no builder for the archetype, _scene_beat uses the
        existing generic beat logic unchanged (the safe fallthrough).

        Updated (C6): centered-statement now has a builder; we use 'map-focus'
        (a valid vocab archetype with no Phase-C builder yet) via a storyboard
        tag to exercise the same fallthrough code path.
        """
        from studio import config
        monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
        pdir = tmp_path / "proj"
        pdir.mkdir()

        c = Composer.__new__(Composer)
        c.slug = "proj"
        c.pdir = pdir
        c.pack = __import__("studio.packs", fromlist=["load_pack"]).load_pack("dark-truth-social")
        c.tokens = c.pack.tokens
        c.colors = c.tokens.get("colors", {})
        from studio import config as cfg
        ad = c.pack.manifest.get("aspect_defaults", {})
        c.width = int(ad.get("width", cfg.DEFAULT_WIDTH))
        c.height = int(ad.get("height", cfg.DEFAULT_HEIGHT))
        c.fps = int(c.tokens.get("motion", {}).get("fps", cfg.DEFAULT_FPS))
        c.spray = c.colors.get("spray", "#2e5e1f")
        c.ink = c.colors.get("ink", "#1f1f1e")
        c._inlines = []
        # Force a storyboard tag to 'map-focus' — a vocab archetype with no builder yet.
        # _archetype_for() uses str(scene_no) as the key.
        c._storyboard = {"5": "map-focus"}
        assert "map-focus" not in A.REGISTRY, (
            "'map-focus' must not be in REGISTRY for this fallthrough test to be valid"
        )
        scene = {"scene_no": 5, "on_screen_text": "THE MACHINE",
                 "point": "p", "narration": "n", "duration_est_sec": 6, "claims": []}
        beat, extra_html = c._scene_beat(4, scene, 24.0)
        # scene index 4 = not i==0, not numeric, not social → underline
        assert beat["kind"] == "underline"
