"""Atlas's new agency tools, and the STRUCTURAL write boundary that fences them.

These prove the privilege asymmetry is physical (not just prompt-level):
- read_repo is jailed to the repo root,
- write_file is tiered: soft/project/incubator allowed, core/secrets REFUSED,
- can_write_core() is permanently False,
- request_from_ceo / ceo_log append to the CEO queue/journal,
- the ceo/STOP kill-switch makes Atlas refuse to act.
"""
import asyncio
import json

import pytest

import boundary
from boundary import WriteBoundaryError, ReadBoundaryError


# ----------------------------------------------------------------------
# classify(): every path lands in exactly one tier
# ----------------------------------------------------------------------
def test_classify_soft_tier_persona_and_soul():
    assert boundary.classify(boundary.ATLAS_DIR / "soul" / "SOUL.md") == boundary.SOFT
    assert boundary.classify(
        boundary.REPO_DIR / "scriptwriter" / "COACH_ADDENDUM.md") == boundary.SOFT


def test_classify_project_and_incubator_are_allowed():
    assert boundary.classify(
        boundary.ATLAS_DIR / "projects" / "espresso-123" / "script.json") == boundary.PROJECT
    assert boundary.classify(
        boundary.REPO_DIR / "agents-incubator" / "newbie" / "engine.py") == boundary.INCUBATOR


def test_classify_core_files_and_dirs_are_refused():
    for name in ("orchestrator.py", "registry.py", "tools.py", "llm.py", "boundary.py"):
        assert boundary.classify(boundary.ATLAS_DIR / name) == boundary.CORE
    assert boundary.classify(boundary.ATLAS_DIR / "rubric" / "rubric.json") == boundary.CORE
    assert boundary.classify(boundary.ATLAS_DIR / "contracts" / "script.json") == boundary.CORE


def test_classify_secrets_by_filename_and_by_content():
    assert boundary.classify(boundary.REPO_DIR / ".env") == boundary.SECRETS
    assert boundary.classify(boundary.REPO_DIR / "deploy.pem") == boundary.SECRETS
    # a secret VALUE in the content trips it no matter how innocent the path is
    soul = boundary.ATLAS_DIR / "soul" / "SOUL.md"
    assert boundary.classify(soul, content="ANTHROPIC_API_KEY=sk-ant-abcdef0123456789xyz") \
        == boundary.SECRETS


def test_classify_unknown_repo_path_defaults_to_core_refuse():
    # not soft/project/incubator -> propose-only by default
    assert boundary.classify(boundary.ATLAS_DIR / "projects.py") == boundary.CORE


def test_classify_outside_repo_is_outside():
    assert boundary.classify("/etc/passwd") == boundary.OUTSIDE


# ----------------------------------------------------------------------
# guarded_write(): allow tiers write; refuse tiers raise WITHOUT writing
# ----------------------------------------------------------------------
def test_guarded_write_allows_soft(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "ATLAS_DIR", tmp_path)
    monkeypatch.setattr(boundary, "REPO_DIR", tmp_path.parent)
    soul = tmp_path / "soul" / "SOUL.md"
    out = boundary.guarded_write(soul, "# hi")
    assert out.read_text() == "# hi"


def test_guarded_write_refuses_core_without_writing():
    core = boundary.ATLAS_DIR / "orchestrator.py"
    before = core.read_text()
    with pytest.raises(WriteBoundaryError):
        boundary.guarded_write(core, "# tampered")
    assert core.read_text() == before  # untouched


def test_guarded_write_refuses_secrets():
    with pytest.raises(WriteBoundaryError):
        boundary.guarded_write(boundary.REPO_DIR / ".env", "KEY=1")


def test_can_write_core_is_false():
    assert boundary.can_write_core() is False


# ----------------------------------------------------------------------
# read_repo(): jailed to the repo root
# ----------------------------------------------------------------------
def test_read_repo_reads_a_repo_file():
    text = boundary.read_repo(boundary.ATLAS_DIR / "soul" / "SOUL.md")
    assert "Atlas" in text


def test_read_repo_refuses_out_of_repo_path():
    with pytest.raises(ReadBoundaryError):
        boundary.read_repo("/etc/passwd")


def test_read_repo_refuses_traversal_escape():
    with pytest.raises(ReadBoundaryError):
        boundary.read_repo(boundary.ATLAS_DIR / ".." / ".." / "etc" / "passwd")


# ----------------------------------------------------------------------
# request_from_ceo() / ceo_log(): append to the CEO queue + journal
# ----------------------------------------------------------------------
def test_request_from_ceo_writes_the_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "CEO_DIR", tmp_path / "ceo")
    res = boundary.request_from_ceo(
        "api_key", "a YouTube Data API key", "to read public RPM trends",
        "drop it in .env as YT_API_KEY")
    queue = tmp_path / "ceo" / "requests.jsonl"
    rows = [json.loads(line) for line in queue.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["kind"] == "api_key"
    assert rows[0]["what"] == "a YouTube Data API key"
    # the caller-facing message names the ask AND promises a legal fallback
    assert "api_key" in res["message"] and "YouTube Data API key" in res["message"]
    assert "alternative" in res["message"].lower()


def test_request_from_ceo_rejects_unknown_kind():
    with pytest.raises(ValueError):
        boundary.request_from_ceo("bribe", "x", "y", "z")


def test_ceo_log_appends_to_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "CEO_DIR", tmp_path / "ceo")
    boundary.ceo_log("started espresso video")
    boundary.ceo_log("fact-check passed clean")
    journal = tmp_path / "ceo" / "journal.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    assert [r["entry"] for r in rows] == ["started espresso video",
                                          "fact-check passed clean"]


# ----------------------------------------------------------------------
# kill-switch: ceo/STOP present -> Atlas refuses to act
# ----------------------------------------------------------------------
def test_kill_switch_active_when_stop_present(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "CEO_DIR", tmp_path / "ceo")
    assert boundary.kill_switch_active() is False
    (tmp_path / "ceo").mkdir()
    (tmp_path / "ceo" / "STOP").write_text("halt")
    assert boundary.kill_switch_active() is True


# ----------------------------------------------------------------------
# The SDK tool wrappers (tools._make_*): readable text, boundary enforced
# ----------------------------------------------------------------------
def _text(result):
    return result["content"][0]["text"]


def test_read_repo_tool_returns_file_contents():
    t = __import__("tools")._make_read_repo_tool()
    res = asyncio.run(t.handler({"path": str(boundary.ATLAS_DIR / "soul" / "SOUL.md")}))
    assert "Atlas" in _text(res)


def test_read_repo_tool_refuses_out_of_repo():
    t = __import__("tools")._make_read_repo_tool()
    res = asyncio.run(t.handler({"path": "/etc/passwd"}))
    assert "outside the repo" in _text(res).lower() or "refused" in _text(res).lower()


def test_write_file_tool_refuses_core_and_allows_soft(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "ATLAS_DIR", tmp_path)
    monkeypatch.setattr(boundary, "REPO_DIR", tmp_path.parent)
    import tools
    t = tools._make_write_file_tool()
    # CORE -> refused, readable
    core = tmp_path / "orchestrator.py"
    res = asyncio.run(t.handler({"path": str(core), "content": "x"}))
    assert "refused" in _text(res).lower() and not core.exists()
    # SOFT -> allowed
    soul = tmp_path / "soul" / "STYLE.md"
    res = asyncio.run(t.handler({"path": str(soul), "content": "# voice"}))
    assert soul.read_text() == "# voice"


def test_request_from_ceo_tool_writes_queue_and_returns_message(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "CEO_DIR", tmp_path / "ceo")
    import tools
    t = tools._make_request_from_ceo_tool()
    res = asyncio.run(t.handler({"kind": "budget", "what": "$50 for stock footage",
                                 "why": "the niche needs B-roll", "how_to_provide": "approve here"}))
    assert "budget" in _text(res)
    assert (tmp_path / "ceo" / "requests.jsonl").exists()


# ----------------------------------------------------------------------
# Wiring: the new tools are registered, and the web builtins are enabled
# ----------------------------------------------------------------------
def test_build_server_registers_the_new_tools():
    import registry
    import tools
    from progress import list_progress
    adapters = registry.build_adapters()
    prog, _ = list_progress()
    _server, allowed = tools.build_server(adapters, prog)
    for name in ("read_repo", "write_file", "request_from_ceo", "ceo_log"):
        assert f"mcp__atlas__{name}" in allowed


def test_orchestrator_enables_web_builtins():
    import orchestrator
    orch = orchestrator.Orchestrator()
    opts = orch._options()
    assert "WebSearch" in opts.tools and "WebFetch" in opts.tools
    assert "WebSearch" in opts.allowed_tools and "WebFetch" in opts.allowed_tools


def test_orchestrator_refuses_when_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "CEO_DIR", tmp_path / "ceo")
    (tmp_path / "ceo").mkdir()
    (tmp_path / "ceo" / "STOP").write_text("halt")
    import orchestrator
    orch = orchestrator.Orchestrator()
    out = asyncio.run(orch.run_turn_async("make a video"))
    assert "stop" in out.lower() and "refus" in out.lower()
