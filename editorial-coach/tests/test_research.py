"""Offline tests for Quill's bounded research/self-study seam (Phase-2 step 4).
No network: search_fn and chat_fn are injected. Research only produces HYPOTHESES;
the rubric/held-out set (tested on the atlas side) prunes."""
from __future__ import annotations

import coach_engine as ce


def _fake_search(query, max_results=5):
    return [
        {"url": "https://ex.com/a", "title": "Open with a question",
         "snippet": "Hooks that pose a question lift retention.", "source_type": "web"},
        {"url": "https://ex.com/b", "title": "One idea per scene",
         "snippet": "Cut secondary claims; keep one point per beat.", "source_type": "web"},
    ]


def test_research_hypotheses_from_injected_search():
    r = ce.research_hypotheses(band_id="script:info_density",
                               direction="LOWER it to about 2.75",
                               search_fn=_fake_search,
                               chat_fn=lambda s, u: "Keep one claim per scene\nMerge overlapping claims")
    assert r["hypotheses"] == ["Keep one claim per scene", "Merge overlapping claims"]
    assert "https://ex.com/a" in r["sources"]
    assert r["n_results"] == 2


def test_research_budget_caps_queries():
    calls = {"n": 0}
    def counting_search(q, max_results=5):
        calls["n"] += 1
        return _fake_search(q)
    r = ce.research_hypotheses(band_id="script:hook_strength", direction="RAISE",
                               search_fn=counting_search, chat_fn=None, max_queries=1)
    assert r["budget"]["queries_used"] == 1 and calls["n"] == 1


def test_research_empty_search_yields_no_hypotheses():
    r = ce.research_hypotheses(band_id="script:info_density", direction="LOWER",
                               search_fn=lambda q, max_results=5: [], chat_fn=None)
    assert r["hypotheses"] == [] and r["sources"] == []


def test_research_never_raises_on_search_failure():
    def boom(q, max_results=5):
        raise RuntimeError("network down")
    r = ce.research_hypotheses(band_id="script:info_density", direction="LOWER",
                               search_fn=boom, chat_fn=None)
    assert r["hypotheses"] == []          # degraded, not crashed


def test_propose_addendum_folds_research():
    seen = {}
    def chat_fn(system, user):
        seen["user"] = user
        return "Pose a question in the hook; keep one claim per scene."
    out = ce.propose_addendum(band_id="script:info_density",
                              direction="LOWER it to about 2.75", owner="Marlow",
                              research=True, search_fn=_fake_search, chat_fn=chat_fn)
    assert out["source"] == "llm-research"
    assert out["research"]["n_results"] == 2
    # the researched hypotheses were put in front of the authoring brain
    assert "HYPOTHESES" in seen["user"]


def test_propose_addendum_without_research_is_unchanged():
    out = ce.propose_addendum(band_id="script:info_density", direction="LOWER",
                              chat_fn=lambda s, u: "Trim claims.")
    assert out["source"] == "llm" and out["research"] is None
