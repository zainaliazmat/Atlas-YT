"""The Atlas↔studio seam — the ONE place Atlas crosses into the studio v2 spine.

Pure-unit: studio.pipeline.produce is MOCKED (no real research/render/network). We prove
the bridge translates Atlas's intent into the right produce() calls, reads studio state,
and that delete is fenced by the structural boundary (studio/projects is PROJECT tier).
"""
import json

import pytest

import boundary
import studio_bridge as sb
from eval.loop import WriteBoundaryError


@pytest.fixture
def studio_tmp(tmp_path, monkeypatch):
    """Point both the studio config PROJECTS_DIR and the boundary repo root at a tmp
    repo whose projects/ lives under a 'projects' segment (so guarded_delete sees it as
    PROJECT tier)."""
    repo = tmp_path
    pdir = repo / "projects"
    pdir.mkdir()
    monkeypatch.setattr(sb._sconfig, "PROJECTS_DIR", pdir)
    monkeypatch.setattr(boundary, "REPO_DIR", repo)
    monkeypatch.setattr(boundary, "ATLAS_DIR", repo / "atlas")
    return pdir


def _write_state(pdir, slug, state):
    d = pdir / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(state))
    return d


# ---- naming -------------------------------------------------------------------
def test_slugify_is_unique_and_safe():
    a, b = sb.slugify("How AI Agents Work!"), sb.slugify("How AI Agents Work!")
    assert a.startswith("how-ai-agents-work-")
    assert a != b                    # timestamp+uuid suffix → no collision
    assert " " not in a and "!" not in a


# ---- driving produce ----------------------------------------------------------
def test_start_calls_produce_with_gates_on(monkeypatch):
    seen = {}
    def fake_produce(brief, slug, **kw):
        seen.update(brief=brief, slug=slug, kw=kw)
        return {"slug": slug, "status": "awaiting_final_gate"}
    monkeypatch.setattr(sb._spipeline, "produce", fake_produce)
    slug, state = sb.start("Quantum tunnelling", angle="for kids", channel="explainer")
    assert seen["brief"] == {"topic": "Quantum tunnelling", "angle": "for kids"}
    assert seen["kw"]["gates"] is True and seen["kw"]["unattended"] is False
    assert seen["kw"]["run_config"]["channel"] == "explainer"
    assert state["status"] == "awaiting_final_gate"


def test_resume_reads_brief_and_config_from_state(studio_tmp, monkeypatch):
    _write_state(studio_tmp, "vid1", {
        "slug": "vid1", "status": "awaiting_final_gate",
        "brief": {"topic": "stored topic"}, "gates_enabled": True, "unattended": False,
        "run_config": {"channel": "main", "pack_id": "dark-truth-social"}})
    seen = {}
    def fake_produce(brief, slug, **kw):
        seen.update(brief=brief, slug=slug, kw=kw)
        return {"slug": slug, "status": "complete", "artifacts": {"video": "v.mp4"}}
    monkeypatch.setattr(sb._spipeline, "produce", fake_produce)
    state = sb.resume("vid1", approve={"final"})
    assert seen["brief"] == {"topic": "stored topic"}
    assert seen["kw"]["approve"] == {"final"}
    assert seen["kw"]["run_config"]["pack_id"] == "dark-truth-social"
    assert state["status"] == "complete"


def test_resume_unknown_slug_raises(studio_tmp):
    with pytest.raises(ValueError):
        sb.resume("ghost", approve={"final"})


# ---- reading studio projects (Atlas's unified list/status — 2A) ---------------
def test_list_projects_reads_studio_state(studio_tmp):
    _write_state(studio_tmp, "a", {"slug": "a", "status": "complete",
                                    "brief": {"topic": "Alpha"}, "updated_at": "2026-02"})
    _write_state(studio_tmp, "b", {"slug": "b", "status": "awaiting_final_gate",
                                    "brief": {"topic": "Beta"}, "updated_at": "2026-03"})
    items = sb.list_projects()
    assert [p["slug"] for p in items] == ["b", "a"]   # newest (updated_at) first
    assert items[0]["status"] == "awaiting_final_gate"
    assert items[1]["topic"] == "Alpha"


def test_status_digest_renders_stages_gates_and_video():
    state = {"slug": "s", "status": "complete", "brief": {"topic": "T"},
             "stages": {st: {"status": "done"} for st in sb.STAGES},
             "gates": {"factcheck": {"status": "passed"},
                       "final": {"status": "passed"}},
             "artifacts": {"video": "/out/video.mp4"}}
    txt = sb.status_digest(state)
    assert "Production 's' — T" in txt
    assert "✓ research" in txt and "✓ final" in txt
    assert "/out/video.mp4" in txt


# ---- delete is fenced by the boundary -----------------------------------------
def test_delete_removes_studio_project(studio_tmp):
    _write_state(studio_tmp, "gone", {"slug": "gone", "status": "complete",
                                      "brief": {"topic": "x"}})
    res = sb.delete("gone")
    assert res["deleted"] is True
    assert not (studio_tmp / "gone").exists()


def test_delete_unknown_slug_is_noop(studio_tmp):
    assert sb.delete("nope") == {"slug": "nope", "deleted": False, "path": None}


def test_delete_empty_slug_raises(studio_tmp):
    with pytest.raises(ValueError):
        sb.delete("  ")


def test_delete_refuses_a_path_outside_projects(studio_tmp, monkeypatch):
    # If studio's PROJECTS_DIR somehow pointed at a non-PROJECT tier, the boundary must
    # still refuse (fail-closed). Repoint it at the repo root's atlas/ (CORE tier).
    core = studio_tmp.parent / "atlas"
    core.mkdir(parents=True, exist_ok=True)
    (core / "victim").mkdir()
    monkeypatch.setattr(sb._sconfig, "PROJECTS_DIR", core)
    with pytest.raises(WriteBoundaryError):
        sb.delete("victim")
    assert (core / "victim").exists()   # untouched
