"""Offline tests for Flux's bounded research/self-study seam (Phase-2 step 4).
No network: search_fn and chat_fn are injected. Research only produces HYPOTHESES;
the rubric/held-out set (tested on the atlas side) prunes."""
from __future__ import annotations

import coach_engine as ce


def _fake_search(query, max_results=5):
    return [
        {"url": "https://ex.com/a", "title": "Add a push-in",
         "snippet": "Subtle camera push-ins raise motion modulation.", "source_type": "web"},
        {"url": "https://ex.com/b", "title": "Count-up beats",
         "snippet": "Animated count-ups add motion without clutter.", "source_type": "web"},
    ]


def test_research_hypotheses_from_injected_search():
    r = ce.research_hypotheses(band_id="compose:motion_energy", direction="RAISE it to about 10",
                               search_fn=_fake_search,
                               chat_fn=lambda s, u: "Add a slow push-in\nUse a count-up beat")
    assert r["hypotheses"] == ["Add a slow push-in", "Use a count-up beat"]
    assert "https://ex.com/a" in r["sources"] and r["n_results"] == 2


def test_research_budget_caps_queries():
    calls = {"n": 0}
    def counting_search(q, max_results=5):
        calls["n"] += 1
        return _fake_search(q)
    r = ce.research_hypotheses(band_id="compose:motion_energy", direction="RAISE",
                               search_fn=counting_search, chat_fn=None, max_queries=1)
    assert r["budget"]["queries_used"] == 1 and calls["n"] == 1


def test_research_never_raises_on_search_failure():
    def boom(q, max_results=5):
        raise RuntimeError("network down")
    r = ce.research_hypotheses(band_id="compose:motion_energy", direction="RAISE",
                               search_fn=boom, chat_fn=None)
    assert r["hypotheses"] == []


def test_propose_addendum_folds_research():
    seen = {}
    def chat_fn(system, user):
        seen["user"] = user
        return "Add a slow push-in and a count-up beat to lift motion."
    out = ce.propose_addendum(band_id="compose:motion_energy", direction="RAISE it to about 10",
                              owner="Mason", research=True, search_fn=_fake_search, chat_fn=chat_fn)
    assert out["source"] == "llm-research"
    assert out["research"]["n_results"] == 2
    assert "HYPOTHESES" in seen["user"]
