"""Atlas as CEO: the business state, the work cycle, and the scheduler.

The cycle is a DETERMINISTIC spine — review state, choose the single
highest-leverage action, execute it through the existing tools, update state +
journal, surface a digest + an ask. It never stalls and it halts on the
kill-switch. (Heavy creative execution still delegates to the orchestrator LLM;
that seam is injectable so the spine is tested offline.)
"""
import json

import pytest

import boundary
from ceo import cycle as ceo_cycle
from ceo import state as ceo_state


@pytest.fixture
def ceo_tmp(tmp_path, monkeypatch):
    """Redirect the CEO dir AND the projects dir into a tmp sandbox."""
    import projects
    monkeypatch.setattr(boundary, "CEO_DIR", tmp_path / "ceo")
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path / "projects")
    return tmp_path


# ----------------------------------------------------------------------
# 1. ceo/state.py — projects-style read/update
# ----------------------------------------------------------------------
def test_state_load_creates_default_with_goal(ceo_tmp):
    st = ceo_state.load()
    assert st["goal_usd_per_month"] == 10000
    for key in ("channels", "niches", "backlog", "videos", "strategy", "milestones"):
        assert key in st
    # persisted to ceo/state.json
    assert (ceo_tmp / "ceo" / "state.json").exists()


def test_state_save_and_helpers_roundtrip(ceo_tmp):
    st = ceo_state.load()
    ceo_state.add_video(st, slug="espresso-1", channel="main", topic="espresso",
                        status="in_production")
    ceo_state.update_video(st, "espresso-1", status="produced",
                           metrics={"quality_score": 0.8})
    st2 = ceo_state.load()
    v = next(v for v in st2["videos"] if v["slug"] == "espresso-1")
    assert v["status"] == "produced" and v["metrics"]["quality_score"] == 0.8


# ----------------------------------------------------------------------
# 2. choose_action — the priority ladder (pure, deterministic)
# ----------------------------------------------------------------------
def test_choose_action_no_niche_sets_direction():
    st = {"niches": [], "backlog": [], "videos": []}
    assert ceo_cycle.choose_action(st)["kind"] == "set_direction"


def test_choose_action_ready_backlog_produces():
    st = {"niches": ["espresso"], "videos": [],
          "backlog": [{"topic": "espresso basics", "status": "proposed"}]}
    a = ceo_cycle.choose_action(st)
    assert a["kind"] == "produce_video"
    assert a["target"]["topic"] == "espresso basics"


def test_choose_action_empty_backlog_researches():
    st = {"niches": ["espresso"], "backlog": [], "videos": []}
    assert ceo_cycle.choose_action(st)["kind"] == "research_niche"


def test_choose_action_produced_unevaluated_analyzes():
    st = {"niches": ["espresso"], "backlog": [],
          "videos": [{"slug": "v1", "status": "produced"}]}
    a = ceo_cycle.choose_action(st)
    assert a["kind"] == "analyze_performance" and a["target"]["slug"] == "v1"


# ----------------------------------------------------------------------
# 3. advance_business — ONE cycle end-to-end (THE VERIFY)
# ----------------------------------------------------------------------
def test_advance_business_runs_one_cycle_end_to_end(ceo_tmp):
    # default state ships with a niche + a seeded backlog, so the top action is
    # produce_video — which really starts a project workspace via projects.start_project.
    res = ceo_cycle.advance_business()

    # a digest line AND a concrete ask were surfaced
    assert res["digest"] and isinstance(res["digest"], str)
    assert res["ask"] is not None and res["ask"]["kind"] in boundary.REQUEST_KINDS

    # state was updated: a video is now in production
    st = ceo_state.load()
    assert any(v["status"] == "in_production" for v in st["videos"])

    # journal + request queue were both appended
    journal = (ceo_tmp / "ceo" / "journal.jsonl").read_text().splitlines()
    requests = (ceo_tmp / "ceo" / "requests.jsonl").read_text().splitlines()
    assert len(journal) >= 1 and len(requests) >= 1
    # the queued ask is real JSON with the cycle's kind
    assert json.loads(requests[-1])["kind"] == res["ask"]["kind"]


def test_advance_business_halts_on_kill_switch(ceo_tmp):
    (ceo_tmp / "ceo").mkdir(parents=True)
    (ceo_tmp / "ceo" / "STOP").write_text("halt")
    before = ceo_state.load()
    res = ceo_cycle.advance_business()
    assert res["halted"] is True
    # nothing produced, no video added
    assert ceo_state.load()["videos"] == before["videos"]


# ----------------------------------------------------------------------
# 4. run_cycles scheduler — caps + kill-switch
# ----------------------------------------------------------------------
def test_run_cycles_respects_max_cycles(ceo_tmp):
    out = ceo_cycle.run_cycles(max_cycles=2)
    assert len(out["cycles"]) == 2


def test_run_cycles_respects_budget(ceo_tmp):
    # a budget smaller than one produce_video cycle -> zero cycles run
    out = ceo_cycle.run_cycles(max_cycles=5, budget_usd=0.01)
    assert out["cycles"] == [] and out["stop_reason"] == "budget"


def test_run_cycles_halts_on_kill_switch(ceo_tmp):
    (ceo_tmp / "ceo").mkdir(parents=True)
    (ceo_tmp / "ceo" / "STOP").write_text("halt")
    out = ceo_cycle.run_cycles(max_cycles=3)
    assert out["cycles"] == [] and out["stop_reason"] == "kill_switch"


# ----------------------------------------------------------------------
# 5. chat trigger + orchestrator CEO-mode wiring
# ----------------------------------------------------------------------
def test_is_advance_command():
    assert ceo_cycle.is_advance_command("advance the business")
    assert ceo_cycle.is_advance_command("  Advance The Business  ")
    assert not ceo_cycle.is_advance_command("make me a video about espresso")


def test_chat_routes_advance_phrase_to_cycle(monkeypatch):
    import chat

    class _FakeSession:
        def __init__(self):
            self.advanced = False

        def advance_business(self):
            self.advanced = True
            return {"digest": "📊 did a cycle", "ask": {"kind": "approval"}}

    sess = _FakeSession()
    # the trigger phrase must route to the CEO cycle, NOT a normal chat turn
    chat.handle_message(sess, "advance the business")
    assert sess.advanced is True


def test_orchestrator_advance_business_delegates(monkeypatch):
    import orchestrator
    sentinel = {"digest": "ok", "ask": None}
    captured = {}

    def fake_advance(*, orch=None):
        captured["orch"] = orch
        return sentinel

    monkeypatch.setattr(ceo_cycle, "advance_business", fake_advance)
    orch = orchestrator.Orchestrator()
    out = orch.advance_business()
    assert out is sentinel and captured["orch"] is orch
