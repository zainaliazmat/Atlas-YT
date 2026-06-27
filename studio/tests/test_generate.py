"""Offline tests for studio.library.generate — the self-growing seam.

Core requirement: obtaining a "check" twice generates once and then serves a
cache HIT (no regeneration); the snippet file is identical and library.json has
exactly one entry. Plus robustness: routing, recolor-on-hit, deterministic
procedural output, and the cache-once halftone processor.
"""

from __future__ import annotations

import shutil

import pytest

from studio import config, library
from studio.library import generate


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """Isolate the library in a throwaway dir for each test."""
    monkeypatch.setattr(config, "ASSET_LIBRARY_DIR", tmp_path / "asset-library")
    return library


# --- the required test: generate once, then cache HIT forever ----------------
def test_obtain_check_twice_is_cache_hit(lib, monkeypatch):
    # spy on library.add as seen by the generator, to prove it runs only once
    calls = {"n": 0}
    real_add = generate.add

    def spy_add(*a, **k):
        calls["n"] += 1
        return real_add(*a, **k)

    monkeypatch.setattr(generate, "add", spy_add)

    first = generate.obtain("snippet", ["check"], {})
    assert first is not None
    assert first.kind == "snippet"
    assert first.provenance == "procedural"
    assert first.factory == "makeCheck"
    assert calls["n"] == 1  # generated once

    snippet_path = config.ASSET_LIBRARY_DIR / first.entry["file"]
    bytes_after_first = snippet_path.read_bytes()

    second = generate.obtain("snippet", ["check"], {})
    assert second is not None
    assert second.id == first.id
    assert calls["n"] == 1  # NO regeneration on the second call (cache HIT)

    # snippet file identical + exactly one entry in the manifest
    assert snippet_path.read_bytes() == bytes_after_first
    assert len(library.list_assets()) == 1


# --- procedural routing + output --------------------------------------------
def test_procedural_snippet_is_deterministic_and_drop_in(lib):
    ref = generate.obtain("snippet", ["check"], {})
    src = ref.payload
    # deterministic: none of the banned primitives in the executable snippet
    for banned in ("Math.random", "Date.now", "fetch("):
        assert banned not in src
    assert "function makeCheck(" in src
    html = ref.html()
    assert html.startswith("<script>") and "makeCheck" in html


def test_aliases_route_to_canonical_semantic(lib):
    # "loading" -> spinner, "tick" -> check
    spin = generate.obtain("snippet", ["loading"], {})
    assert spin.factory == "makeSpinner"
    tick = generate.obtain("snippet", ["tick"], {})
    assert tick.factory == "makeCheck"


def test_distinct_semantics_make_distinct_entries(lib):
    generate.obtain("snippet", ["check"], {})
    generate.obtain("snippet", ["bell"], {})
    generate.obtain("snippet", ["progress"], {})
    snippets = library.list_assets(kind="snippet")
    assert {e["id"] for e in snippets} == {"snippet-check", "snippet-bell", "snippet-progress"}


# --- hit path recolors a sourced icon (no regeneration) ----------------------
def test_resolve_hit_recolors_icon(lib):
    svg = '<svg viewBox="0 0 24 24" stroke="currentColor" fill="none"><path d="M3 3h18"/></svg>'
    library.add(svg.encode("utf-8"), "icon", ["facebook", "brand"], "CC0-1.0",
                "Simple Icons", "simpleicons.org", "sourced", True, filename="facebook.svg")
    ref = generate.obtain("icon", ["facebook"], {"color": "#2e5e1f"})
    assert ref is not None
    assert "#2e5e1f" in ref.payload
    assert "currentColor" not in ref.payload
    # cached original untouched
    stored = config.ASSET_LIBRARY_DIR / ref.entry["file"]
    assert "currentColor" in stored.read_text(encoding="utf-8")


# --- source route degrades offline ------------------------------------------
def test_source_route_degrades_offline(lib, monkeypatch):
    # a non-procedural icon with no cache + a fetch that returns nothing -> None
    monkeypatch.setattr(generate, "_fetch", lambda url: None)
    assert generate.obtain("icon", ["whatsapp"], {}) is None


def test_source_accepts_injected_fetch(lib):
    svg = b'<svg viewBox="0 0 24 24" stroke="currentColor"><path d="M1 1"/></svg>'
    entry = generate._gen_source("icon", ["telegram"], {}, fetch_fn=lambda url: svg)
    assert entry is not None
    assert entry["provenance"] == "sourced"
    assert entry["license"] == "ISC"


# --- halftone processor (cache once) ----------------------------------------
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_halftone_is_cached_once(lib, monkeypatch):
    portrait = config.REPO_ROOT / "reference" / "dark-truth-social" / "assets" / "img" / "portrait.jpg"
    if not portrait.is_file():
        pytest.skip("reference portrait not present")

    calls = {"n": 0}
    real_add = generate.add

    def spy_add(*a, **k):
        calls["n"] += 1
        return real_add(*a, **k)

    monkeypatch.setattr(generate, "add", spy_add)

    a = generate.halftone(portrait, source_id="portrait")
    assert a is not None and a.provenance == "generated"
    assert any(t == "src:portrait" for t in a.entry["tags"])
    assert calls["n"] == 1

    b = generate.halftone(portrait, source_id="portrait")
    assert b.id == a.id
    assert calls["n"] == 1  # processed once, then cache
    assert len(library.list_assets(kind="img")) == 1
