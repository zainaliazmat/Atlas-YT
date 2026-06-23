"""Step-4 tests (atlas side): the `research` flag threads to the owning coach, and
a RESEARCHED hypothesis is adopted ONLY when it beats the held-out gate — research
widens what's tried; the rubric/held-out set prunes. Offline (no web/LLM)."""
from __future__ import annotations

import rubric
from eval import loop
from eval.types import Measurement


def _m(stage, prop, value):
    b = rubric.band(stage, prop)
    return Measurement(artifact=b["artifact"], stage=stage, owner=b["owner"], prop=prop,
                       value=value, kind=b["kind"], rolls_up_to=tuple(b["rolls_up_to"]),
                       unit=b.get("unit", ""))


def _target():
    b = rubric.band_by_id("script:info_density")
    return {"band_id": "script:info_density", "stage": "script", "owner": "Marlow",
            "comparator": b["comparator"], "measured_value": 9.0,
            "band_min": b.get("min"), "band_max": b.get("max"), "band_target": b.get("target")}


def test_research_flag_threads_to_delegate(monkeypatch):
    captured = {}
    def fake_delegate(target, direction, preserve, *, research=False):
        captured["research"] = research
        return {"addendum": "## note\nTry a researched technique.\n",
                "coach": "editorial_coach", "source": "llm-research"}
    monkeypatch.setattr(loop, "delegate_to_coach", fake_delegate)
    p = loop.propose_fix(_target(), use_coaches=True, research=True)
    assert captured["research"] is True
    assert p["coach_source"] == "llm-research"


def test_researched_addendum_accepted_only_if_beats_heldout():
    base = [_m("script", "info_density", 9.0), _m("script", "scene_count", 11)]
    target = _target()
    # a coach_fn standing in for a RESEARCH-informed addendum
    researched = lambda payload: "Researched: keep one claim per scene; expand narration."
    def remeasure(addendum):
        return [_m("script", "info_density", 2.8), _m("script", "scene_count", 11)]

    # held-out PASSES -> the researched change generalizes -> accepted
    ok = loop.run_loop(baseline_measurements=base, target=target, remeasure_fn=remeasure,
                       max_iters=1, write_soft=False, objective_margin=0.2,
                       coach_fn=researched,
                       verify_fn=lambda add: {"generalizes": True},
                       spot_check_fn=lambda pr, vd: True)
    assert ok["accepted"] is True

    # SAME researched change, but it does NOT generalize -> the eval prunes it
    no = loop.run_loop(baseline_measurements=base, target=target, remeasure_fn=remeasure,
                       max_iters=1, write_soft=False, objective_margin=0.2,
                       coach_fn=researched,
                       verify_fn=lambda add: {"generalizes": False, "regressions": ["x:y"]},
                       spot_check_fn=lambda pr, vd: True)
    assert no["accepted"] is False
