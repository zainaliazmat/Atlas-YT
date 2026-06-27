"""The generated tools, driven with MOCK adapters (no LLM, no network):
- emit the deterministic progress lines in order,
- route a job to the right adapter with the right params,
- route a persona ask to the adapter,
- CONTAIN errors (a raising/slow job never propagates — the meeting survives),
- bound each job with a timeout.

The canonical ORDER under a REAL orchestrator LLM (scout_find_topics -> decision ->
sage_research) is an integration behavior, proven by the live `run.py "<niche>"`
flow — see README. Here we prove the deterministic pieces those steps rely on.
"""
import asyncio

import registry
import tools
from progress import list_progress


def _text(result):
    return result["content"][0]["text"]


class _MockAdapter:
    def __init__(self, entry, *, behavior="ok"):
        self.entry = entry
        self.progress = None
        self.behavior = behavior
        self.job_calls = []
        self.ask_calls = []

    def run_job(self, job_name, progress, **params):
        self.job_calls.append((job_name, params))
        progress.start(self.entry.emoji, self.entry.display, "scanning",
                       params.get("niche") or params.get("topic") or "")
        if self.behavior == "raise":
            raise RuntimeError("boom")
        if self.behavior == "slow":
            import time
            time.sleep(3)
        progress.done(self.entry.display, "did the thing")
        return {"ok": True, "text": f"{self.entry.display} result for {params}"}

    def ask(self, question, context=""):
        self.ask_calls.append((question, context))
        return f"{self.entry.display} persona reply"


def _entry(name="scout", tool="scout_find_topics", params=None, timeout=300):
    return registry.AgentEntry(
        name=name, display=name.title(), emoji="🔎", blurb="b.",
        project_dir="/x", adapter_cls=_MockAdapter,
        jobs=[registry.JobSpec(name="find_topics", tool=tool,
                               description="d", params=params or {"niche": str},
                               timeout=timeout)],
        persona=True)


def test_job_tool_calls_adapter_and_emits_progress_in_order():
    e = _entry()
    adapter = _MockAdapter(e)
    prog, lines = list_progress()
    adapter.progress = prog
    job_tool = tools._make_job_tool(adapter, e.jobs[0])

    result = asyncio.run(job_tool.handler({"niche": "home espresso"}))

    # the slug param is always injected (empty when not provided)
    assert adapter.job_calls == [("find_topics",
                                  {"niche": "home espresso", "slug": ""})]
    assert "home espresso" in _text(result)
    # deterministic lines, in order: start (🔎 …scanning…) then done (✅ …)
    assert lines[0].startswith("🔎") and "scanning 'home espresso'" in lines[0]
    assert lines[1].startswith("✅")


def test_sequential_two_agents_emit_lines_in_order():
    se = _entry("scout", "scout_find_topics", {"niche": str})
    ge = _entry("sage", "sage_research", {"topic": str})
    scout, sage = _MockAdapter(se), _MockAdapter(ge)
    prog, lines = list_progress()
    scout.progress = sage.progress = prog
    st = tools._make_job_tool(scout, se.jobs[0])
    gt = tools._make_job_tool(sage, ge.jobs[0])

    asyncio.run(st.handler({"niche": "espresso"}))
    asyncio.run(gt.handler({"topic": "espresso machines"}))

    # scout start, scout done, sage start, sage done
    assert "Scout is scanning" in lines[0]
    assert lines[1].startswith("✅")
    assert "Sage is scanning" in lines[2]
    assert lines[3].startswith("✅")


def test_persona_ask_tool_routes_to_adapter():
    e = _entry()
    adapter = _MockAdapter(e)
    adapter.progress, _ = list_progress()
    ask_tool = tools._make_ask_tool(adapter)
    result = asyncio.run(ask_tool.handler({"question": "is faceless dead?",
                                           "context": ""}))
    assert adapter.ask_calls and adapter.ask_calls[0][0] == "is faceless dead?"
    assert "persona reply" in _text(result)


def test_error_is_contained_not_raised():
    e = _entry()
    adapter = _MockAdapter(e, behavior="raise")
    prog, lines = list_progress()
    adapter.progress = prog
    job_tool = tools._make_job_tool(adapter, e.jobs[0])

    # Must NOT raise — returns a readable failure the orchestrator can narrate.
    result = asyncio.run(job_tool.handler({"niche": "x"}))
    assert "failed" in _text(result).lower()
    assert any(line.startswith("⚠️") for line in lines)  # fail line emitted


def test_slow_job_times_out_and_reports():
    e = _entry(timeout=1)  # 1s budget vs a 3s job
    adapter = _MockAdapter(e, behavior="slow")
    adapter.progress, _ = list_progress()
    job_tool = tools._make_job_tool(adapter, e.jobs[0])

    result = asyncio.run(job_tool.handler({"niche": "x"}))
    assert "timed out" in _text(result).lower()


# ----------------------------------------------------------------------
# The slug is the spine now: every job tool carries a `slug` param, and the
# orchestration tools (start_project / project_status / validate_artifact) manage the
# per-project workspace Atlas runs the playbook against. projects.PROJECTS_DIR is
# redirected to a tmp dir so these stay pure-unit (no real project dirs / network).
# ----------------------------------------------------------------------
def test_job_tool_carries_a_slug_param_and_forwards_it():
    e = _entry()
    adapter = _MockAdapter(e)
    adapter.progress, _ = list_progress()
    job_tool = tools._make_job_tool(adapter, e.jobs[0])

    asyncio.run(job_tool.handler({"niche": "espresso", "slug": "espresso-123"}))
    # the slug reaches run_job alongside the domain params
    name, params = adapter.job_calls[0]
    assert params.get("slug") == "espresso-123"


def test_start_project_tool_mints_a_project(tmp_path, monkeypatch):
    import projects
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    ptool = tools._make_start_project_tool()
    result = asyncio.run(ptool.handler({"brief": "the attention economy"}))
    txt = _text(result)
    assert "Started project" in txt and "attention-economy" in txt
    # exactly one project dir with a manifest was created
    made = [d for d in tmp_path.iterdir() if (d / "project.json").exists()]
    assert len(made) == 1


def test_start_project_tool_needs_a_brief(tmp_path, monkeypatch):
    import projects
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    ptool = tools._make_start_project_tool()
    result = asyncio.run(ptool.handler({}))
    assert "give me a brief" in _text(result).lower()


def test_project_status_tool_reads_the_checklist(tmp_path, monkeypatch):
    import projects
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    info = projects.start_project("home espresso", slug="home-espresso")
    projects.mark_artifact("home-espresso", "research_brief",
                           info["project_dir"] + "/research_brief.json")
    stool = tools._make_project_status_tool()
    txt = _text(asyncio.run(stool.handler({"slug": "home-espresso"})))
    assert "✓ research_brief" in txt and "· script" in txt
    # no slug -> lists known projects
    listing = _text(asyncio.run(stool.handler({})))
    assert "home-espresso" in listing


def test_start_project_arg_logging_is_captured(tmp_path, monkeypatch):
    import logging

    import projects
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    logger = logging.getLogger("atlas")
    for h in list(logger.handlers):           # ensure THIS test's path is used
        if getattr(h, "_atlas_file", False):
            h.close()
            logger.removeHandler(h)
    logpath = tmp_path / "atlas.log"
    tools.configure_logging(logpath)
    try:
        ptool = tools._make_start_project_tool()
        asyncio.run(ptool.handler({"brief": "kokoro tts deep dive"}))
        contents = logpath.read_text()
        assert "start_project:" in contents
        assert "kokoro tts deep dive" in contents
    finally:
        for h in list(logger.handlers):
            if getattr(h, "_atlas_file", False):
                h.close()
                logger.removeHandler(h)
