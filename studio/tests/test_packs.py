"""Tests for studio.packs — load the dark-truth-social pack, assert all partial
files exist and tokens parse. Dependency-free (stdlib + pytest)."""

from __future__ import annotations

import json
import re

import pytest

from studio import packs
from studio.packs import REQUIRED_PARTIALS, Pack, PackError, load_pack, list_packs

PACK_ID = "dark-truth-social"
JS_PARTIALS = ("transitions", "ticker", "retimer")


# --- registry ---------------------------------------------------------------
def test_pack_is_registered():
    ids = [e.id for e in list_packs()]
    assert PACK_ID in ids


def test_discover_finds_pack_on_disk():
    assert PACK_ID in packs.discover_packs()


def test_get_entry_case_insensitive():
    assert packs.get_entry("DARK-TRUTH-SOCIAL") is not None
    assert packs.get_entry("does-not-exist") is None


# --- loading ----------------------------------------------------------------
def test_load_pack_returns_pack():
    pack = load_pack(PACK_ID)
    assert isinstance(pack, Pack)
    assert pack.id == PACK_ID
    assert pack.name
    assert pack.dir.is_dir()


def test_unknown_pack_raises():
    with pytest.raises(PackError):
        load_pack("no-such-pack")


# --- partials exist ---------------------------------------------------------
def test_all_required_partials_exist():
    pack = load_pack(PACK_ID)
    for name in REQUIRED_PARTIALS:
        path = pack.partial(name)
        assert path.exists(), f"missing partial {name}: {path}"
        assert path.read_text(encoding="utf-8").strip(), f"empty partial {name}"


def test_design_md_present():
    pack = load_pack(PACK_ID)
    assert pack.design_md.exists()
    assert "DESIGN.md" in pack.design_md.read_text(encoding="utf-8")


def test_motion_library_dir_present():
    pack = load_pack(PACK_ID)
    assert pack.motion_library_dir.is_dir()


# --- tokens parse + shape ---------------------------------------------------
def test_tokens_parse_and_shape():
    pack = load_pack(PACK_ID)
    tok = pack.tokens
    # required top-level keys
    for key in ("colors", "type", "motion", "textures"):
        assert key in tok, f"tokens missing {key}"
    # palette
    assert tok["colors"]["spray"] == "#2e5e1f"
    assert pack.colors["paper"] == "#f2eed6"
    # type roles map font+weight
    assert tok["type"]["hero"]["font"] == "Rubik Spray Paint"
    assert tok["type"]["slab"]["weight"] == 900
    # motion grammar
    assert tok["motion"]["fps"] == 30
    assert pack.fps == 30
    assert isinstance(tok["motion"]["eases"], list) and tok["motion"]["eases"]
    assert "budget" in tok["motion"]
    # textures name the procedural filters
    assert set(tok["textures"]) >= {"halftone", "spray-rough", "grain"}


def test_tokens_json_is_valid_json_on_disk():
    pack = load_pack(PACK_ID)
    json.loads((pack.dir / "tokens.json").read_text(encoding="utf-8"))


def test_pack_manifest_shape():
    pack = load_pack(PACK_ID)
    m = pack.manifest
    for key in ("id", "name", "aspect_defaults", "fonts", "required_assets", "motion_index"):
        assert key in m, f"pack.json missing {key}"
    assert m["aspect_defaults"] == {"width": 1920, "height": 1080, "fps": 30}
    assert any(f["role"] == "hero" for f in m["fonts"])


# --- determinism guard ------------------------------------------------------
def _strip_js_comments(src: str) -> str:
    """Remove /* block */ and // line comments so we scan executable code only."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"//.*", "", src)
    return src


def test_js_partials_are_deterministic():
    """No Math.random / Date.now / fetch in the lifted JS partials (code, not comments)."""
    pack = load_pack(PACK_ID)
    banned = ("Math.random", "Date.now", "fetch(")
    for name in JS_PARTIALS:
        code = _strip_js_comments(pack.read_partial(name))
        for token in banned:
            assert token not in code, f"{name} contains non-deterministic {token!r}"


def test_retimer_exports_factory():
    pack = load_pack(PACK_ID)
    assert "function makeRetimer(" in pack.read_partial("retimer")


def test_transitions_export_all_verbs():
    pack = load_pack(PACK_ID)
    src = pack.read_partial("transitions")
    for verb in ("txPush", "txTile", "txWhip", "txCut", "greenSwipe"):
        assert verb in src, f"transitions missing {verb}"


def test_filters_partial_has_both_filters():
    pack = load_pack(PACK_ID)
    src = pack.read_partial("filters")
    assert 'id="spray-rough"' in src
    assert 'id="halftone"' in src


# --- validation (studio.packs.validate) -------------------------------------
def test_validate_dark_truth_ok():
    from studio.packs.validate import validate_pack

    res = validate_pack(PACK_ID)
    assert res.ok, res.errors
    assert all(res.checks.values())


def test_validate_clean_explainer_ok():
    from studio.packs.validate import validate_pack

    res = validate_pack("clean-explainer")
    assert res.ok, res.errors


def test_validate_catches_missing_transition(tmp_path, monkeypatch):
    """A pack whose transitions.js drops a verb must fail validation."""
    from studio import config
    from studio.packs.validate import validate_pack

    # Build a throwaway pack dir that mirrors a real one but breaks transitions.
    src = config.DESIGN_PACKS_DIR / PACK_ID
    dst = tmp_path / "design-packs" / "broken"
    (dst / "partials").mkdir(parents=True)
    (dst / "DESIGN.md").write_text("# broken\n", encoding="utf-8")
    for fname in ("tokens.json",):
        (dst / fname).write_text((src / fname).read_text(encoding="utf-8"), encoding="utf-8")
    manifest = json.loads((src / "pack.json").read_text(encoding="utf-8"))
    manifest["id"] = "broken"
    (dst / "pack.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name in ("filters.html", "base.css", "ticker.js", "retimer.js"):
        (dst / "partials" / name).write_text(
            (src / "partials" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    # transitions.js missing txWhip
    bad = (src / "partials" / "transitions.js").read_text(encoding="utf-8").replace("txWhip", "txGone")
    (dst / "partials" / "transitions.js").write_text(bad, encoding="utf-8")

    monkeypatch.setattr(config, "DESIGN_PACKS_DIR", tmp_path / "design-packs")
    res = validate_pack("broken")
    assert not res.ok
    assert any("txWhip" in e for e in res.errors)


# --- shared mechanism is pack-agnostic --------------------------------------
def test_clean_explainer_reuses_shared_mechanism():
    pack = load_pack("clean-explainer")
    for name in JS_PARTIALS:
        resolved = pack.partial(name).as_posix()
        assert "/_shared/" in resolved, f"{name} should resolve to the shared mechanism, got {resolved}"


def test_clean_explainer_has_own_surface():
    """Surface partials (filters/base.css) are pack-local, NOT shared."""
    pack = load_pack("clean-explainer")
    for name in ("filters", "base_css"):
        resolved = pack.partial(name).as_posix()
        assert "/_shared/" not in resolved
        assert "clean-explainer" in resolved
    # and the clean look is genuinely different: it doesn't USE the grunge filters
    base = pack.read_partial("base_css")
    assert "url(#halftone)" not in base
    assert "url(#spray-rough)" not in base
    assert ".grain" not in base


def test_dark_truth_mechanism_byte_for_byte_vs_shared():
    """dark-truth's vendored mechanism == the shared canonical mechanism, and
    clean-explainer uses that same shared mechanism -> identical bytes drive both."""
    from studio import config

    dt = load_pack(PACK_ID)
    shared_root = config.DESIGN_PACKS_DIR / "_shared"
    for name in JS_PARTIALS:
        dt_bytes = dt.partial(name).read_bytes()
        shared_bytes = (shared_root / f"{name}.js").read_bytes()
        assert dt_bytes == shared_bytes, f"{name}: dark-truth vendored copy drifted from _shared"


def test_dark_truth_partials_match_reference_source():
    """The lifted mechanism still matches the reference index.html it came from."""
    from studio import config

    ref = config.REPO_ROOT / "reference" / "dark-truth-social" / "index.html"
    if not ref.exists():
        pytest.skip("reference composition not present")
    ref_src = ref.read_text(encoding="utf-8")
    trans = load_pack(PACK_ID).read_partial("transitions")
    # signature tween lines that were lifted verbatim from the reference
    for needle in (
        '{ xPercent: -106, duration: 0.34, ease: "power3.in" }',
        '{ xPercent: 44 }, { xPercent: 0, duration: 0.26, ease: "power3.out" }',
    ):
        assert needle in trans, f"transitions.js lost lifted line: {needle}"
        assert needle in ref_src, f"reference no longer contains: {needle}"
