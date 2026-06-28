"""Offline tests for studio.compose — the Composer authors one deterministic,
seekable HyperFrames index.html from a pack + script. The asset library and
projects dir are isolated to tmp (no network, no growth of the real library)."""

from __future__ import annotations

import json
import re
import shutil

import pytest

from studio import config, pipeline
from studio.compose import compose

SCRIPT = {
    "schema_version": "studio-1",
    "working_title": "Test Reel",
    "scenes": [
        {"scene_no": 1, "beat": "hook", "point": "automatic",
         "narration": "You won't decide to.", "on_screen_text": "YOU JUST WILL",
         "duration_est_sec": 6, "claims": []},
        {"scene_no": 2, "beat": "scale", "point": "users",
         "narration": "Billions of us.", "on_screen_text": "5.66B USERS",
         "duration_est_sec": 8, "claims": []},
        {"scene_no": 3, "beat": "outro", "point": "live real",
         "narration": "Log off.", "on_screen_text": "LIVE REAL",
         "duration_est_sec": 5, "claims": []},
    ],
}
BRIEF = {"schema_version": "studio-1", "topic": "t", "verified_facts": [], "sources": []}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(config, "ASSET_LIBRARY_DIR", tmp_path / "lib")
    slug = "t1"
    pdir = pipeline.scaffold_project(slug)
    (pdir / "script.json").write_text(json.dumps(SCRIPT), encoding="utf-8")
    (pdir / "research_brief.json").write_text(json.dumps(BRIEF), encoding="utf-8")
    return slug, pdir


def _strip_js_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"//.*", "", src)
    return src


def test_composes_index_html(project):
    slug, pdir = project
    out = compose(slug, pack_id="dark-truth-social")
    assert out == pdir / "index.html"
    assert out.exists()


def test_one_section_per_scene_with_timing(project):
    slug, pdir = project
    html = compose(slug, pack_id="dark-truth-social").read_text(encoding="utf-8")
    sections = re.findall(r'<section id="s\d+" class="scene clip"', html)
    assert len(sections) == len(SCRIPT["scenes"])
    # every timed section carries the three required data-* attributes
    for m in re.finditer(r'<section id="s\d+"[^>]*>', html):
        tag = m.group(0)
        assert "data-start=" in tag and "data-duration=" in tag and "data-track-index=" in tag


def test_root_and_registration(project):
    slug, pdir = project
    html = compose(slug, pack_id="dark-truth-social").read_text(encoding="utf-8")
    assert 'data-composition-id="t1"' in html
    assert 'data-width="1920"' in html and 'data-height="1080"' in html
    assert 'window.__timelines["t1"] = tlReal;' in html
    assert "gsap.timeline" in html or "makeRetimer" in html


def test_pack_integration(project):
    slug, pdir = project
    html = compose(slug, pack_id="dark-truth-social").read_text(encoding="utf-8")
    # filters injected
    assert 'id="spray-rough"' in html and 'id="halftone"' in html
    # palette from tokens
    assert "--paper: #f2eed6;" in html or "--paper:#f2eed6;" in html.replace(" ", "")
    # mechanism partials inlined
    assert "function makeRetimer(" in html
    assert "function makeTransitions(" in html
    assert "function makeTicker(" in html


def test_determinism_no_banned_primitives(project):
    slug, pdir = project
    html = compose(slug, pack_id="dark-truth-social").read_text(encoding="utf-8")
    code = _strip_js_comments(html)
    for banned in ("Math.random", "Date.now", "fetch("):
        assert banned not in code, f"emitted composition uses {banned}"


def test_byte_for_byte_deterministic(project):
    slug, pdir = project
    a = compose(slug, pack_id="dark-truth-social").read_bytes()
    b = compose(slug, pack_id="dark-truth-social").read_bytes()
    assert a == b  # same inputs -> identical composition


def test_motion_library_write_back(project):
    """Authoring registers the reusable beats in the pack (compounding policy)."""
    slug, pdir = project
    compose(slug, pack_id="dark-truth-social")
    manifest = json.loads((config.DESIGN_PACKS_DIR / "dark-truth-social" / "pack.json").read_text())
    ids = {m["id"] for m in manifest["motion_index"]}
    assert {"outline-self-draw", "highlighter-swipe", "orbit-cluster"} <= ids
    ml = config.DESIGN_PACKS_DIR / "dark-truth-social" / "motion-library"
    assert (ml / "orbit-cluster.js").exists()
