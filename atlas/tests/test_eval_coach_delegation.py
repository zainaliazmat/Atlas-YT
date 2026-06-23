"""Step-3 tests: loop.propose_fix delegates to the right DOMAIN coach.

Offline: the coach is injected (coach_fn) or registry.build_adapters is faked, so
no sibling engine / LLM is touched. Proves the routing (content->editorial,
craft->production), the authoring priority, and that the soft-tier write boundary
is untouched by the split."""
from __future__ import annotations

from pathlib import Path

import rubric
from eval import loop
from eval.types import Measurement


def _m(stage, prop, value):
    b = rubric.band(stage, prop)
    return Measurement(artifact=b["artifact"], stage=stage, owner=b["owner"], prop=prop,
                       value=value, kind=b["kind"], rolls_up_to=tuple(b["rolls_up_to"]),
                       unit=b.get("unit", ""))


def _target(band_id="script:info_density", stage="script"):
    b = rubric.band_by_id(band_id)
    return {"band_id": band_id, "stage": stage, "owner": b["owner"],
            "comparator": b["comparator"], "measured_value": 9.0,
            "band_min": b.get("min"), "band_max": b.get("max"),
            "band_target": b.get("target")}


# --- routing ---------------------------------------------------------------

def test_coach_for_stage_routes_content_and_craft():
    assert loop.coach_for_stage("script") == "editorial_coach"
    assert loop.coach_for_stage("research") == "editorial_coach"
    assert loop.coach_for_stage("assets") == "editorial_coach"
    assert loop.coach_for_stage("compose") == "production_coach"
    assert loop.coach_for_stage("audiomix") == "production_coach"
    assert loop.coach_for_stage("storyboard") == "production_coach"
    assert loop.coach_for_stage("nonexistent") is None


def test_soft_path_targets_owning_specialist():
    assert loop._soft_path_for(_target("script:info_density", "script")).endswith(
        "scriptwriter/COACH_ADDENDUM.md")
    assert loop._soft_path_for(_target("compose:motion_energy", "compose")).endswith(
        "composition-engineer/COACH_ADDENDUM.md")


# --- authoring priority ----------------------------------------------------

def test_injected_coach_fn_authors_addendum():
    seen = {}
    def coach_fn(payload):
        seen.update(payload)
        return "Cut to one claim per scene; expand each so runtime holds."
    p = loop.propose_fix(_target(), coach_fn=coach_fn)
    assert p["coach_source"] == "coach_fn"
    assert "Cut to one claim per scene" in p["addendum"]
    assert "script:info_density" in p["addendum"]
    # the rubric-decided direction is what the coach is handed (not invented by it)
    assert "direction" in seen and seen["band_id"] == "script:info_density"


def test_use_coaches_delegates_to_owning_coach(monkeypatch):
    calls = {}
    def fake_delegate(target, direction, preserve, *, research=False):
        calls["stage"] = target["stage"]
        return {"addendum": "## Coach note (Quill)\nTighten the claims.\n",
                "coach": "editorial_coach", "source": "llm"}
    monkeypatch.setattr(loop, "delegate_to_coach", fake_delegate)
    p = loop.propose_fix(_target(), use_coaches=True)
    assert p["coach"] == "editorial_coach" and p["coach_source"] == "llm"
    assert "Tighten the claims" in p["addendum"]
    assert calls["stage"] == "script"


def test_delegate_falls_back_to_rule_when_no_coach(monkeypatch):
    monkeypatch.setattr(loop, "delegate_to_coach", lambda *a, **k: None)
    p = loop.propose_fix(_target(), use_coaches=True)
    assert p["coach_source"] == "rule"
    assert "Coach note (eval-driven" in p["addendum"]


# --- delegate_to_coach via a faked registry adapter ------------------------

def test_delegate_to_coach_uses_registry_adapter(monkeypatch):
    class FakeAdapter:
        def run_job(self, job, progress, **params):
            assert job == "propose_addendum"
            assert params["band_id"] == "compose:motion_energy"
            return {"ok": True, "text": "Add a push-in beat.", "source": "llm"}
    import registry
    monkeypatch.setattr(registry, "build_adapters",
                        lambda: {"production_coach": FakeAdapter()})
    res = loop.delegate_to_coach(_target("compose:motion_energy", "compose"),
                                 "RAISE it to about 10", " keep X.")
    assert res["coach"] == "production_coach" and "push-in" in res["addendum"]


# --- run_loop end-to-end with an injected coach (offline) ------------------

def test_run_loop_accepts_through_injected_coach(tmp_path):
    base = [_m("script", "info_density", 9.0), _m("script", "scene_count", 11)]
    target = _target()
    def remeasure(addendum):
        assert "claim" in addendum.lower()        # the coach's text reached the engine
        return [_m("script", "info_density", 2.8), _m("script", "scene_count", 11)]
    soft = tmp_path / "COACH_ADDENDUM.md"
    res = loop.run_loop(
        baseline_measurements=base, target=target, remeasure_fn=remeasure,
        max_iters=1, soft_path=str(soft), objective_margin=0.2,
        coach_fn=lambda payload: "Keep one claim per scene; expand the narration.",
        verify_fn=lambda add: {"generalizes": True},
        spot_check_fn=lambda proposal, verdict: True)
    assert res["accepted"] is True
    assert res["iterations"][0]["coach_source"] == "coach_fn"
    assert soft.read_text()
    assert res["rubric_write_blocked"] is True     # boundary intact through the split
