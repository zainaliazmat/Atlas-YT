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

    assert adapter.job_calls == [("find_topics", {"niche": "home espresso"})]
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
# produce_video: the guard accepts an approve-only resume; the arg-logging surfaces.
# pipeline.produce is patched so these stay pure-unit (no real project dirs / network).
# ----------------------------------------------------------------------
def test_produce_guard_allows_approve_only_resume(monkeypatch):
    # TEST 1 (guard half): {approve} alone must NOT be rejected as "Nothing to produce",
    # and must reach pipeline.produce with approve parsed + forwarded.
    import pipeline
    seen = {}

    def fake_produce(brief=None, *, slug=None, approve=None, **kw):
        seen.update(brief=brief, slug=slug, approve=approve)
        return {"status": "blocked", "gate": "final_render", "slug": "demo",
                "reason": "ok", "details": {}}

    monkeypatch.setattr(pipeline, "produce", fake_produce)
    prog, _ = list_progress()
    ptool = tools._make_produce_tool(prog)

    result = asyncio.run(ptool.handler({"approve": "factcheck"}))
    assert "Nothing to produce" not in _text(result)
    assert seen["brief"] is None and seen["slug"] is None
    assert seen["approve"] == ["factcheck"]


def test_produce_guard_still_rejects_a_truly_empty_call(monkeypatch):
    import pipeline
    monkeypatch.setattr(pipeline, "produce",
                        lambda *a, **k: pytest_fail_never_called())
    prog, _ = list_progress()
    ptool = tools._make_produce_tool(prog)
    result = asyncio.run(ptool.handler({}))  # no brief, no slug, no approve
    assert "Nothing to produce" in _text(result)


def pytest_fail_never_called():  # pragma: no cover — guard must short-circuit first
    raise AssertionError("pipeline.produce should not be reached for an empty call")


def test_produce_video_arg_logging_is_captured(tmp_path, monkeypatch):
    # TEST 7: the permanent INFO arg-line now actually lands (in the file handler).
    import logging

    import pipeline
    logger = logging.getLogger("atlas")
    for h in list(logger.handlers):           # ensure THIS test's path is used
        if getattr(h, "_atlas_file", False):
            h.close()
            logger.removeHandler(h)

    logpath = tmp_path / "atlas.log"
    tools.configure_logging(logpath)
    try:
        monkeypatch.setattr(pipeline, "produce", lambda *a, **k: {
            "status": "blocked", "gate": "factcheck", "slug": "demo-slug",
            "reason": "r", "details": {}})
        prog, _ = list_progress()
        ptool = tools._make_produce_tool(prog)
        asyncio.run(ptool.handler({"brief": "kokoro tts deep dive"}))

        contents = logpath.read_text()
        assert "produce_video args:" in contents
        assert "kokoro tts deep dive" in contents
    finally:
        for h in list(logger.handlers):
            if getattr(h, "_atlas_file", False):
                h.close()
                logger.removeHandler(h)
