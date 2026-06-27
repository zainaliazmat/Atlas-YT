"""Tests for studio.library — add/resolve/dedupe/recolor against an isolated
temp library (so the real asset-library/ is never touched)."""

from __future__ import annotations

import pytest

from studio import config, library


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """Point the library at a throwaway directory for the duration of a test."""
    monkeypatch.setattr(config, "ASSET_LIBRARY_DIR", tmp_path / "asset-library")
    return library


def _svg(color_token="currentColor"):
    return (
        f'<svg viewBox="0 0 24 24" stroke="{color_token}" fill="none">'
        f'<path d="M3 3h18"/></svg>'
    )


# --- add --------------------------------------------------------------------
def test_add_copies_hashes_and_appends(lib, tmp_path):
    src = tmp_path / "bell.svg"
    src.write_text(_svg(), encoding="utf-8")
    entry = lib.add(src, "icon", ["bell", "notification"], "ISC", "Lucide (ISC)",
                    "lucide.dev", "sourced", True)
    assert entry["kind"] == "icon"
    assert entry["tags"] == ["bell", "notification"]
    assert len(entry["sha256"]) == 64
    assert entry["recolorable"] is True
    # file actually copied into the cache
    stored = config.ASSET_LIBRARY_DIR / entry["file"]
    assert stored.is_file()
    assert stored.read_text(encoding="utf-8") == _svg()
    # manifest persisted
    assert any(e["id"] == entry["id"] for e in lib.list_assets())


def test_add_accepts_bytes(lib):
    entry = lib.add(b"\x00\x01binary", "sfx", ["whoosh"], "Pixabay Content License",
                    "x (Pixabay)", "pixabay.com", "sourced", False,
                    filename="whoosh.mp3", extra={"duration": 0.6})
    assert entry["kind"] == "sfx"
    assert entry["duration"] == 0.6
    assert (config.ASSET_LIBRARY_DIR / entry["file"]).read_bytes() == b"\x00\x01binary"


# --- resolve by tag ----------------------------------------------------------
def test_resolve_by_tag(lib, tmp_path):
    a = tmp_path / "bell.svg"
    a.write_text(_svg(), encoding="utf-8")
    b = tmp_path / "infinity.svg"
    b.write_text(_svg() + "<!--inf-->", encoding="utf-8")
    bell = lib.add(a, "icon", ["bell", "notification", "ui"], "ISC", "", "lucide.dev", "sourced", True)
    lib.add(b, "icon", ["infinity", "loop", "ui"], "ISC", "", "lucide.dev", "sourced", True)

    hit = lib.resolve("icon", ["bell"])
    assert hit is not None and hit["id"] == bell["id"]
    # no semantic match -> None
    assert lib.resolve("icon", ["nonexistent-tag"]) is None
    # wrong kind -> None
    assert lib.resolve("music", ["bell"]) is None


def test_resolve_constraint_duration_and_tiebreak(lib):
    # two music beds; duration constraint should pick the closer one
    lib.add(b"dark", "music", ["bed", "dark"], "Pixabay Content License", "", "x", "sourced", False,
            filename="dark.mp3", id="music-dark", extra={"duration": 135.0, "mood": ["dark"]})
    lib.add(b"hope", "music", ["bed", "hopeful"], "Pixabay Content License", "", "x", "sourced", False,
            filename="hope.mp3", id="music-hopeful", extra={"duration": 85.0, "mood": ["hopeful"]})
    near85 = lib.resolve("music", ["bed"], {"duration": 84.0})
    assert near85["id"] == "music-hopeful"
    near135 = lib.resolve("music", ["bed"], {"duration": 130.0})
    assert near135["id"] == "music-dark"
    # mood constraint
    assert lib.resolve("music", [], {"mood": ["dark"]})["id"] == "music-dark"


def test_resolve_deterministic_tiebreak_by_id(lib):
    # identical tag score -> lowest id wins, stably
    lib.add(b"a", "sfx", ["click"], "L", "", "s", "sourced", False, filename="z.mp3", id="zzz")
    lib.add(b"b", "sfx", ["click"], "L", "", "s", "sourced", False, filename="a.mp3", id="aaa")
    assert lib.resolve("sfx", ["click"])["id"] == "aaa"
    assert lib.resolve("sfx", ["click"])["id"] == "aaa"  # repeatable


# --- dedupe -----------------------------------------------------------------
def test_dedupe_on_readd(lib, tmp_path):
    src = tmp_path / "same.svg"
    src.write_text(_svg(), encoding="utf-8")
    first = lib.add(src, "icon", ["x"], "ISC", "", "lucide.dev", "sourced", True)
    # re-add identical bytes (even via a different filename) -> same entry, no growth
    src2 = tmp_path / "copy.svg"
    src2.write_text(_svg(), encoding="utf-8")
    second = lib.add(src2, "icon", ["x", "other"], "ISC", "", "lucide.dev", "sourced", True)
    assert second["id"] == first["id"]
    assert second["sha256"] == first["sha256"]
    assert len(lib.list_assets()) == 1


# --- recolor never mutates original -----------------------------------------
def test_recolor_does_not_mutate_original(lib, tmp_path):
    src = tmp_path / "ui.svg"
    original = _svg("currentColor")
    src.write_text(original, encoding="utf-8")
    entry = lib.add(src, "icon", ["ui"], "ISC", "", "lucide.dev", "sourced", True)
    stored = config.ASSET_LIBRARY_DIR / entry["file"]

    out = lib.recolor(entry, "#2e5e1f")
    assert "#2e5e1f" in out
    assert "currentColor" not in out
    # the cached original is untouched
    assert stored.read_text(encoding="utf-8") == original
    assert "currentColor" in stored.read_text(encoding="utf-8")


def test_recolor_rejects_non_recolorable(lib):
    entry = lib.add(b"\x00data", "img", ["photo"], "Pexels License", "", "pexels.com", "sourced", False,
                    filename="p.jpg")
    with pytest.raises(library.LibraryError):
        lib.recolor(entry, "#000000")


# --- promote + gc -----------------------------------------------------------
def test_promote_brings_project_asset_in(lib, tmp_path):
    proj = tmp_path / "proj" / "scene.svg"
    proj.parent.mkdir()
    proj.write_text(_svg(), encoding="utf-8")
    entry = lib.promote(proj, kind="icon", tags=["scene"], license="ISC", attribution="",
                        source="proj-slug", used_in=["proj-slug"])
    assert entry["provenance"] == "generated"
    assert (config.ASSET_LIBRARY_DIR / entry["file"]).is_file()
    assert proj.exists()  # default: source not removed


def test_gc_drops_missing_and_orphans(lib, tmp_path):
    src = tmp_path / "g.svg"
    src.write_text(_svg(), encoding="utf-8")
    entry = lib.add(src, "icon", ["g"], "ISC", "", "lucide.dev", "sourced", True)
    # delete the backing file -> gc should drop the entry
    (config.ASSET_LIBRARY_DIR / entry["file"]).unlink()
    # drop an orphan file with no manifest entry
    orphan = config.ASSET_LIBRARY_DIR / "icons" / "orphan.svg"
    orphan.write_text(_svg(), encoding="utf-8")
    summary = lib.gc()
    assert entry["id"] in summary["removed_entries"]
    assert any("orphan.svg" in f for f in summary["removed_files"])
    assert lib.list_assets() == []


# --- seeded library sanity (reads the real, committed library) --------------
def test_seeded_library_has_expected_assets():
    """The committed asset-library/ was seeded from the reference win."""
    fonts = {e["id"] for e in library.list_assets(kind="font")}
    assert {"font-inter", "font-rubik-spray-paint", "font-space-mono"} <= fonts
    assert library.has_font("Inter")
    # semantic icon lookup works on the real cache
    bell = library.resolve("icon", ["bell"])
    assert bell is not None and bell["kind"] == "icon"
    # a music bed resolves by mood
    hopeful = library.resolve("music", [], {"mood": ["hopeful"]})
    assert hopeful is not None
