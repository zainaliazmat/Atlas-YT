"""Deleting a project workspace — the gated, irreversible privilege.

Three layers, each proven here:
- boundary.guarded_delete: only PROJECT-tier paths under projects/ can be removed; the
  spine, the projects/ root itself, and anything outside the repo are refused WITHOUT
  touching the filesystem (the door fails closed, like guarded_write).
- projects.delete_project: empty slug raises, unknown slug is a no-op, a real slug is
  removed through the boundary.
- the delete_project TOOL: honours the CEO STOP kill-switch, narrates refusals, and
  never needs a slug it wasn't given.

Plus a regression for the containment gap that surfaced "No module named 'projects'"
raw to Atlas: a builtin orchestration tool whose handler raises now returns readable
text instead of propagating.
"""
import asyncio

import pytest

import boundary
import projects
import tools
from eval.loop import WriteBoundaryError


def _text(result):
    return result["content"][0]["text"]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A throwaway repo root with a projects/ workspace, wired into the boundary and
    the projects module."""
    monkeypatch.setattr(boundary, "REPO_DIR", tmp_path)
    monkeypatch.setattr(boundary, "ATLAS_DIR", tmp_path / "atlas")
    monkeypatch.setattr(boundary, "CEO_DIR", tmp_path / "ceo")
    pdir = tmp_path / "projects"
    pdir.mkdir()
    monkeypatch.setattr(projects, "PROJECTS_DIR", pdir)
    # The delete TOOL routes through studio_bridge → studio.config.PROJECTS_DIR; point
    # it at the same tmp projects dir so the tool tests stay pure-unit.
    import studio_bridge
    monkeypatch.setattr(studio_bridge._sconfig, "PROJECTS_DIR", pdir)
    return tmp_path


def _make_project(repo, slug="my-video"):
    d = repo / "projects" / slug
    d.mkdir()
    (d / "project.json").write_text('{"slug": "%s"}' % slug)
    (d / "script.json").write_text("{}")
    return d


# ---- boundary.guarded_delete --------------------------------------------------
def test_guarded_delete_removes_a_project_tree(repo):
    d = _make_project(repo)
    removed = boundary.guarded_delete(d)
    assert removed == d.resolve()
    assert not d.exists()


def test_guarded_delete_refuses_the_spine(repo):
    spine = repo / "atlas" / "orchestrator.py"
    spine.parent.mkdir(parents=True)
    spine.write_text("# core")
    with pytest.raises(WriteBoundaryError):
        boundary.guarded_delete(spine)
    assert spine.exists()  # untouched


def test_guarded_delete_refuses_the_projects_root_itself(repo):
    with pytest.raises(WriteBoundaryError):
        boundary.guarded_delete(repo / "projects")
    assert (repo / "projects").exists()


def test_guarded_delete_refuses_outside_the_repo(repo, tmp_path):
    outside = tmp_path.parent / "elsewhere.txt"
    with pytest.raises(WriteBoundaryError):
        boundary.guarded_delete(outside)


def test_guarded_delete_missing_path_raises_filenotfound(repo):
    with pytest.raises(FileNotFoundError):
        boundary.guarded_delete(repo / "projects" / "ghost")


# ---- projects.delete_project --------------------------------------------------
def test_delete_project_removes_real_project(repo):
    _make_project(repo, "real-one")
    res = projects.delete_project("real-one")
    assert res["deleted"] is True
    assert not (repo / "projects" / "real-one").exists()


def test_delete_project_empty_slug_raises(repo):
    with pytest.raises(ValueError):
        projects.delete_project("   ")


def test_delete_project_unknown_slug_is_noop(repo):
    res = projects.delete_project("never-existed")
    assert res["deleted"] is False
    assert res["path"] is None


# ---- the delete_project TOOL --------------------------------------------------
def test_delete_tool_deletes_a_project(repo):
    _make_project(repo, "to-go")
    t = tools._make_delete_project_tool()
    result = asyncio.run(t.handler({"slug": "to-go"}))
    assert "Deleted production 'to-go'" in _text(result)
    assert not (repo / "projects" / "to-go").exists()


def test_delete_tool_requires_a_slug(repo):
    t = tools._make_delete_project_tool()
    result = asyncio.run(t.handler({"slug": "  "}))
    assert "exact 'slug'" in _text(result)


def test_delete_tool_refuses_when_kill_switch_set(repo):
    d = _make_project(repo, "protected")
    (repo / "ceo").mkdir()
    (repo / "ceo" / "STOP").write_text("halt")
    t = tools._make_delete_project_tool()
    result = asyncio.run(t.handler({"slug": "protected"}))
    assert "STOP kill-switch" in _text(result)
    assert d.exists()  # never deleted while halted


def test_delete_tool_unknown_slug_reports_nothing_deleted(repo):
    t = tools._make_delete_project_tool()
    result = asyncio.run(t.handler({"slug": "nope"}))
    assert "No production named 'nope'" in _text(result)


# ---- list_dir: the directory-browsing capability ------------------------------
def test_list_dir_lists_projects(repo):
    _make_project(repo, "alpha")
    _make_project(repo, "beta")
    entries, truncated = boundary.list_dir(repo / "projects")
    assert not truncated
    assert any(e.endswith("projects/alpha/") for e in entries)
    assert any(e.endswith("projects/beta/") for e in entries)


def test_list_dir_recursive_walks_a_project_and_skips_cache(repo):
    d = _make_project(repo, "gamma")
    (d / "__pycache__").mkdir()
    (d / "__pycache__" / "x.pyc").write_text("junk")
    entries, _ = boundary.list_dir(repo / "projects" / "gamma", recursive=True)
    assert any(e.endswith("gamma/script.json") for e in entries)
    assert not any("__pycache__" in e for e in entries)  # noise filtered


def test_list_dir_refuses_outside_repo(repo, tmp_path):
    with pytest.raises(boundary.ReadBoundaryError):
        boundary.list_dir(tmp_path.parent)


def test_list_dir_on_a_file_raises(repo):
    d = _make_project(repo, "delta")
    with pytest.raises(NotADirectoryError):
        boundary.list_dir(d / "script.json")


def test_list_dir_tool_lists_projects(repo):
    _make_project(repo, "shown")
    t = tools._make_list_dir_tool()
    result = asyncio.run(t.handler({"path": str(repo / "projects")}))
    assert "shown/" in _text(result)


def test_list_dir_tool_needs_a_path(repo):
    t = tools._make_list_dir_tool()
    result = asyncio.run(t.handler({"path": "  "}))
    assert "'path'" in _text(result)


# ---- containment regression ---------------------------------------------------
def test_contain_narrates_a_raising_handler():
    async def _boom(args):
        raise ModuleNotFoundError("No module named 'projects'")

    wrapped = tools._contain("project_status", _boom)
    result = asyncio.run(wrapped({}))
    txt = _text(result)
    assert "project_status failed" in txt
    assert "No module named 'projects'" in txt  # the cause is surfaced, not swallowed
