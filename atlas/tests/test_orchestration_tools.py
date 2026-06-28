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


# ----------------------------------------------------------------------
# Production now flows through the studio spine via the `produce` / `approve_gate`
# tools (the legacy start_project + hand-called chain is retired). These tools call
# studio through tools.studio_bridge, which we MOCK so the tests stay pure-unit (no
# real render / network). The CEO checkpoint is captured, not written to disk.
# ----------------------------------------------------------------------
def _final_gate_state(slug="att-econ"):
    return {"slug": slug, "status": "awaiting_final_gate",
            "brief": {"topic": "the attention economy"},
            "stages": {}, "gates": {"final": {"status": "awaiting_approval",
                                              "approvable": True, "reason": "awaiting approval",
                                              "details": {"motion_ok": True, "review_ok": True,
                                                          "under_budget": True}}},
            "artifacts": {}}


def test_produce_tool_starts_and_surfaces_the_final_gate(monkeypatch):
    asks = []
    monkeypatch.setattr(tools.boundary, "kill_switch_active", lambda: False)
    monkeypatch.setattr(tools.boundary, "request_from_ceo",
                        lambda *a, **k: asks.append((a, k)) or {"message": "ok"})
    monkeypatch.setattr(tools.studio_bridge, "start",
                        lambda topic, **kw: ("att-econ", _final_gate_state()))
    ptool = tools._make_produce_tool()
    txt = _text(asyncio.run(ptool.handler({"topic": "the attention economy"})))
    assert "FINAL GATE" in txt and "att-econ" in txt
    assert asks, "the final gate should file a CEO approval checkpoint"


def test_produce_tool_needs_a_topic(monkeypatch):
    monkeypatch.setattr(tools.boundary, "kill_switch_active", lambda: False)
    ptool = tools._make_produce_tool()
    assert "topic" in _text(asyncio.run(ptool.handler({}))).lower()


def test_produce_tool_respects_kill_switch(monkeypatch):
    monkeypatch.setattr(tools.boundary, "kill_switch_active", lambda: True)
    called = []
    monkeypatch.setattr(tools.studio_bridge, "start",
                        lambda *a, **k: called.append(1) or ("x", {}))
    ptool = tools._make_produce_tool()
    txt = _text(asyncio.run(ptool.handler({"topic": "x"})))
    assert "STOP kill-switch" in txt and not called


def test_approve_gate_tool_resumes_and_reports_complete(monkeypatch):
    monkeypatch.setattr(tools.boundary, "kill_switch_active", lambda: False)
    done = {"slug": "att-econ", "status": "complete", "brief": {"topic": "t"},
            "stages": {}, "gates": {}, "artifacts": {"video": "/x/video.mp4"}}
    seen = {}
    def fake_resume(slug, *, approve):
        seen["slug"], seen["approve"] = slug, approve
        return done
    monkeypatch.setattr(tools.studio_bridge, "resume", fake_resume)
    gtool = tools._make_approve_gate_tool()
    txt = _text(asyncio.run(gtool.handler({"slug": "att-econ"})))
    assert "complete" in txt and "/x/video.mp4" in txt
    assert seen == {"slug": "att-econ", "approve": {"final"}}  # defaults to final


def test_approve_gate_tool_needs_a_slug_and_valid_gate(monkeypatch):
    monkeypatch.setattr(tools.boundary, "kill_switch_active", lambda: False)
    gtool = tools._make_approve_gate_tool()
    assert "'slug'" in _text(asyncio.run(gtool.handler({"slug": ""})))
    assert "final" in _text(asyncio.run(gtool.handler({"slug": "s", "gate": "bogus"})))


def test_project_status_tool_lists_and_reads_studio(monkeypatch):
    monkeypatch.setattr(tools.studio_bridge, "list_projects",
                        lambda: [{"slug": "home-espresso", "topic": "home espresso",
                                  "status": "complete", "updated": "z"}])
    monkeypatch.setattr(tools.studio_bridge, "read_state",
                        lambda slug: {"slug": slug, "status": "complete",
                                      "brief": {"topic": "home espresso"}, "stages": {},
                                      "gates": {}, "artifacts": {}} if slug == "home-espresso"
                                      else None)
    stool = tools._make_project_status_tool()
    listing = _text(asyncio.run(stool.handler({})))
    assert "home-espresso" in listing and "[complete]" in listing
    detail = _text(asyncio.run(stool.handler({"slug": "home-espresso"})))
    assert "Production 'home-espresso'" in detail
    assert "No production" in _text(asyncio.run(stool.handler({"slug": "ghost"})))
