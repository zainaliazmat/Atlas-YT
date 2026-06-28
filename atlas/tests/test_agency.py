"""Atlas improving + creating agents, fenced by the same structural boundary.

- improve_agent: edits a SOFT-tier persona/prompt of an existing agent, then
  re-validates that agent's persona. Non-soft files are refused.
- propose_agent: scaffolds a NEW agent into agents-incubator/ (SOUL/STYLE/engine
  + a PROPOSED AgentEntry patch as text), smoke-tests it in isolation, and files a
  CEO approval request. registry.py is NEVER auto-edited (promotion is a human/CORE
  change).
- run_self_eval: measures a finished video via eval/ and may apply ONE soft
  improvement through eval/loop.py's guarded path — the rubric stays read-only.
"""
import asyncio
import importlib
from pathlib import Path

import pytest

import agency
import boundary
import registry
from boundary import WriteBoundaryError


def _text(result):
    return result["content"][0]["text"]


def _fake_entry(tmp_path, name="testbot"):
    return registry.AgentEntry(
        name=name, display=name.title(), emoji="🤖", blurb="a test agent.",
        project_dir=str(tmp_path / name), adapter_cls=object, role="Tester")


# ----------------------------------------------------------------------
# 1. improve_agent — SOFT persona edit + re-validate; non-soft refused
# ----------------------------------------------------------------------
def test_improve_agent_writes_soul_and_validates(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "REPO_DIR", tmp_path)
    monkeypatch.setattr(boundary, "ATLAS_DIR", tmp_path / "atlas")
    entry = _fake_entry(tmp_path)
    monkeypatch.setattr(registry, "get_entry", lambda n: entry if n == "testbot" else None)

    res = agency.improve_agent("testbot", "soul/SOUL.md",
                               "# Testbot\nYou are decisive and terse.")
    soul = tmp_path / "testbot" / "soul" / "SOUL.md"
    assert soul.read_text().startswith("# Testbot")
    assert res["tier"] == boundary.SOFT
    assert res["validation"]["ok"] is True
    assert res["validation"]["soul_chars"] > 0


def test_improve_agent_refuses_non_soft_file(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "REPO_DIR", tmp_path)
    monkeypatch.setattr(boundary, "ATLAS_DIR", tmp_path / "atlas")
    entry = _fake_entry(tmp_path)
    monkeypatch.setattr(registry, "get_entry", lambda n: entry)
    with pytest.raises(WriteBoundaryError):
        agency.improve_agent("testbot", "engine.py", "print('pwned')")
    assert not (tmp_path / "testbot" / "engine.py").exists()


def test_improve_agent_unknown_agent(monkeypatch):
    monkeypatch.setattr(registry, "get_entry", lambda n: None)
    with pytest.raises(agency.AgentError):
        agency.improve_agent("ghost", "soul/SOUL.md", "x")


# ----------------------------------------------------------------------
# 2. propose_agent — scaffold to incubator, smoke-test, request promotion,
#    NEVER touch registry.py
# ----------------------------------------------------------------------
def test_propose_agent_scaffolds_and_requests_promotion(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "REPO_DIR", tmp_path)
    monkeypatch.setattr(boundary, "ATLAS_DIR", tmp_path / "atlas")
    monkeypatch.setattr(boundary, "CEO_DIR", tmp_path / "ceo")

    res = agency.propose_agent("thumbsmith", "Thumbnail Artist",
                               "Designs high-CTR thumbnails from the script's hook.")
    incub = tmp_path / "agents-incubator" / "thumbsmith"
    # the four scaffolded files exist
    for f in ("soul/SOUL.md", "soul/STYLE.md", "engine.py", "PROMOTION.md"):
        assert (incub / f).exists(), f
    # the proposed AgentEntry is TEXT in PROMOTION.md, not applied to registry
    assert "AgentEntry(" in (incub / "PROMOTION.md").read_text()
    # it loaded in isolation + smoke-tested
    assert res["smoke"]["ok"] is True
    # a CEO approval request was queued
    assert res["promotion_request"]["kind"] == "approval"
    assert (tmp_path / "ceo" / "requests.jsonl").exists()
    assert res["registry_edited"] is False


def test_propose_agent_does_not_edit_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "REPO_DIR", tmp_path)
    monkeypatch.setattr(boundary, "ATLAS_DIR", tmp_path / "atlas")
    monkeypatch.setattr(boundary, "CEO_DIR", tmp_path / "ceo")
    real_registry = Path(registry.__file__)
    before = real_registry.read_text()
    n_entries = len(registry.REGISTRY)

    agency.propose_agent("echobot", "Analyst", "Reads comments.")

    assert real_registry.read_text() == before          # registry.py untouched
    assert len(registry.REGISTRY) == n_entries           # no live entry added


def test_propose_agent_rejects_unsafe_name(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "REPO_DIR", tmp_path)
    monkeypatch.setattr(boundary, "CEO_DIR", tmp_path / "ceo")
    with pytest.raises(agency.AgentError):
        agency.propose_agent("../escape", "x", "y")


# ----------------------------------------------------------------------
# 3. run_self_eval — measure a video, apply ONE soft tweak, rubric read-only
# ----------------------------------------------------------------------
def _failing_scorecard():
    """A scorecard with one clean soft-tier failure (script:info_density over band)."""
    return {
        "overall": "FAIL", "quality_score": 0.4,
        "rows": [{
            "band_id": "script:info_density", "stage": "script", "passed": False,
            "hard": False, "rolls_up_to": ["G2"], "comparator": "range",
            "measured_value": 7.0, "band_min": 1.5, "band_max": 4.0,
            "band_target": None, "note": "claims per minute", "placeholder": False,
        }],
    }


def test_run_self_eval_applies_a_soft_tweak(tmp_path, monkeypatch):
    import projects
    from eval import loop
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    projects.start_project("espresso machines explained", slug="espresso")
    # persist the soft tweak to a tmp file, not the real scriptwriter/ dir
    soft = tmp_path / "COACH_ADDENDUM.md"
    monkeypatch.setattr(loop, "_soft_path_for", lambda target: str(soft))

    rubric_before = (Path(__file__).resolve().parents[1] / "rubric" / "rubric.json").read_text()

    res = agency.run_self_eval("espresso", apply=True,
                               inspect_fn=lambda d: _failing_scorecard())

    assert res["target"]["band_id"] == "script:info_density"
    assert res["applied"]["soft_path"] == str(soft)
    assert "LOWER" in soft.read_text() or "info_density" in soft.read_text()
    # the success bar never moved
    assert res["rubric_read_only"] is True
    assert (Path(__file__).resolve().parents[1] / "rubric" / "rubric.json").read_text() \
        == rubric_before


def test_run_self_eval_measure_only_when_clean(tmp_path, monkeypatch):
    import projects
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    projects.start_project("a clean video", slug="clean")
    clean = {"overall": "PASS", "quality_score": 0.9, "rows": []}
    res = agency.run_self_eval("clean", apply=True, inspect_fn=lambda d: clean)
    assert res["target"] is None and res["applied"] is None


def test_run_self_eval_unknown_slug(monkeypatch):
    import projects
    monkeypatch.setattr(projects, "PROJECTS_DIR", Path("/does/not/exist"))
    with pytest.raises(agency.AgentError):
        agency.run_self_eval("nope")


# ----------------------------------------------------------------------
# SDK tool wrappers + registration
# ----------------------------------------------------------------------
def test_improve_agent_tool_refuses_non_soft(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "REPO_DIR", tmp_path)
    monkeypatch.setattr(boundary, "ATLAS_DIR", tmp_path / "atlas")
    entry = _fake_entry(tmp_path)
    monkeypatch.setattr(registry, "get_entry", lambda n: entry)
    import tools
    t = tools._make_improve_agent_tool()
    res = asyncio.run(t.handler({"name": "testbot", "file": "engine.py", "content": "x"}))
    assert "refused" in _text(res).lower()


def test_propose_agent_tool_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "REPO_DIR", tmp_path)
    monkeypatch.setattr(boundary, "ATLAS_DIR", tmp_path / "atlas")
    monkeypatch.setattr(boundary, "CEO_DIR", tmp_path / "ceo")
    import tools
    t = tools._make_propose_agent_tool()
    res = asyncio.run(t.handler({"name": "glint", "role": "Thumbnail Artist",
                                 "spec": "Designs thumbnails."}))
    assert "glint" in _text(res).lower() and "approval" in _text(res).lower()


def test_build_server_registers_agency_tools():
    import tools
    from progress import list_progress
    adapters = registry.build_adapters()
    prog, _ = list_progress()
    _server, allowed = tools.build_server(adapters, prog)
    for name in ("improve_agent", "propose_agent", "run_self_eval"):
        assert f"mcp__atlas__{name}" in allowed
