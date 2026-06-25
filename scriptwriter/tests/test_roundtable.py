"""Offline proof for the Creative Roundtable — NO network, NO API keys.

The three sub-agents (Critic / Researcher / Craftsman) each talk through the SAME
injected `llm_chat(system, user) -> str` seam, so a fake router that switches on a
marker in the system prompt stands in for all three. The `search_tool` is faked too.

What we assert is the PLUMBING and the guarantees the architecture promises:
  - the 3 steps run in order and produce an enhanced artifact + a complete log
  - FRESH CONTEXT: every sub-agent prompt is system + user only (history_tokens == 0),
    the Critic reads SKILL+SOUL, the Craftsman reads STYLE+SOUL
  - the Critic only diagnoses; the Researcher actually calls the search tool
  - GRACEFUL DEGRADATION: any sub-agent failure returns the draft unchanged, never raises
  - the log is written to project_dir for the eval system to read later

Whether the REAL sub-agents write sharper criticism / find a killer detail / craft a
better draft is a manual/integration check, exactly like the engine's arc call.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import roundtable as rt  # noqa: E402


DRAFT = {
    "working_title": "What Crema Really Tells You",
    "hook": "Crema isn't the sign of quality everyone thinks it is.",
    "cta": "What did you used to believe about espresso?",
    "total_scenes": 2,
    "est_runtime_sec": 12.0,
    "scenes": [
        {"scene_no": 1, "beat": "hook", "point": "crema is misunderstood",
         "narration": "Crema isn't the sign of quality everyone thinks it is.",
         "on_screen_text": "CREMA", "visual_note": "macro shot",
         "duration_est_sec": 5, "claims": []},
        {"scene_no": 2, "beat": "point", "point": "not a quality signal",
         "narration": "It's mostly gas and oil — not a grade.",
         "on_screen_text": "not a grade", "visual_note": "side-by-side",
         "duration_est_sec": 7,
         "claims": [{"claim_id": "s2c1",
                     "text": "Crema is mostly CO2 and emulsified oils.",
                     "source_ref": 0}]},
    ],
}

UPSTREAM = {
    "thematic_anchor": {"core_metaphor": "crema as a lie detector"},
    "narrative_intent": {"video_level": {"core_thesis": "crema is not quality"}},
    "motion_mood_board": {},
}

CRITICISMS = [
    {"rank": 1, "severity": "major",
     "principle_violated": "Open on the single sharpest true thing.",
     "target_text": "Crema isn't the sign of quality everyone thinks it is.",
     "location": "scene 1 (hook)",
     "diagnosis": "The hook is generic and abstract — no concrete image.",
     "impact": "The viewer doesn't lean in."},
]

FINDINGS = {
    "findings": [
        {"target_criticism_rank": 1,
         "found_detail": "Baristas blind-tested crema and guessed quality no better than chance.",
         "detail_type": "case_study",
         "source_description": "A 2019 sensory study",
         "source_url": "https://sci.example/blindtest",
         "suggested_use": "Open the hook on the failed blind test.",
         "why_surprising": "People assume pros can read crema."}
    ]
}

ENHANCED = {
    **DRAFT,
    "hook": "Pros blind-tested crema and guessed quality no better than a coin flip.",
    "scenes": [
        {**DRAFT["scenes"][0],
         "narration": "Pros blind-tested crema and guessed no better than a coin flip."},
        DRAFT["scenes"][1],
    ],
}


def _router(*, criticisms=CRITICISMS, findings=FINDINGS, enhanced=ENHANCED,
            capture=None):
    """A fake llm_chat that returns the right artifact per sub-agent, by marker."""
    def chat(system, user):
        if capture is not None:
            capture.append({"system": system, "user": user})
        if "INTERNAL CRITIC" in system:
            return json.dumps(criticisms)
        if "RESEARCH ASSISTANT" in system:
            return json.dumps(findings)
        # Craftsman: "You are Marlow." + STYLE
        return json.dumps(enhanced)
    return chat


def _fake_search(calls=None):
    def search(query, max_results=5):
        if calls is not None:
            calls.append(query)
        return [{"url": "https://sci.example/blindtest", "title": "Blind crema test",
                 "snippet": "Baristas could not tell quality from crema.",
                 "source_type": "web"}]
    return search


def _config(**overrides):
    base = dict(
        specialist_name="Marlow", specialist_role="Scriptwriter",
        skill_md="SKILL-PRINCIPLE: open on the sharpest true thing.",
        style_md="STYLE-VOICE: short, declarative, shape-first.",
        soul_md="SOUL-IDENTITY: information is cheap, feeling is expensive.",
        llm_chat=_router(), search_tool=_fake_search(),
    )
    base.update(overrides)
    return rt.RoundtableConfig(**base)


# ----------------------------------------------------------------------
# config validation
# ----------------------------------------------------------------------
def test_config_requires_skill_and_style():
    import pytest
    with pytest.raises(ValueError):
        rt.CreativeRoundtable(_config(skill_md=""))
    with pytest.raises(ValueError):
        rt.CreativeRoundtable(_config(style_md=""))


# ----------------------------------------------------------------------
# the happy path — 3 steps, enhanced artifact, complete log
# ----------------------------------------------------------------------
def test_review_and_enhance_runs_three_steps():
    board = rt.CreativeRoundtable(_config())
    enhanced, log = board.review_and_enhance(DRAFT, UPSTREAM)

    assert enhanced["hook"] == ENHANCED["hook"]            # the craftsman's rewrite won
    assert log["specialist"] == "Marlow"
    assert len(log["criticisms"]) == 1
    assert len(log["research_findings"]) == 1
    assert log["enhanced_artifact"]["hook"] == ENHANCED["hook"]
    assert log["draft_artifact"]["hook"] == DRAFT["hook"]
    assert log["models_used"]["critic"]                    # recorded for the eval system
    assert log["diff_summary"]["scenes_modified"] == 1     # only scene 1 narration changed


# ----------------------------------------------------------------------
# FRESH CONTEXT — system + user only, the right files per sub-agent
# ----------------------------------------------------------------------
def test_each_subagent_runs_with_fresh_context():
    cap = []
    board = rt.CreativeRoundtable(_config(llm_chat=_router(capture=cap)))
    _, log = board.review_and_enhance(DRAFT, UPSTREAM)

    # exactly three sub-agent calls (no search-driven extra LLM turns)
    assert len(cap) == 3
    critic_sys, researcher_sys, craftsman_sys = (c["system"] for c in cap)
    assert "SKILL-PRINCIPLE" in critic_sys and "SOUL-IDENTITY" in critic_sys
    assert "STYLE-VOICE" in craftsman_sys and "SOUL-IDENTITY" in craftsman_sys
    # the proof is in the log: zero history tokens for every sub-agent
    for who in ("critic", "researcher", "craftsman"):
        assert log["context_proof"][who]["history_tokens"] == 0
        assert log["context_proof"][who]["system_tokens"] > 0


def test_critic_only_diagnoses_never_prescribes():
    cap = []
    board = rt.CreativeRoundtable(_config(llm_chat=_router(capture=cap)))
    board.review_and_enhance(DRAFT, UPSTREAM)
    critic_sys = cap[0]["system"]
    assert "DO NOT suggest" in critic_sys or "do not suggest" in critic_sys.lower()


# ----------------------------------------------------------------------
# the Researcher actually searches (not just an LLM with opinions)
# ----------------------------------------------------------------------
def test_researcher_calls_the_search_tool():
    calls = []
    board = rt.CreativeRoundtable(_config(search_tool=_fake_search(calls)))
    _, log = board.review_and_enhance(DRAFT, UPSTREAM)
    assert calls, "the Researcher must call the web search tool"
    assert log["research_findings"][0]["found_detail"].startswith("Baristas")


def test_researcher_without_search_tool_still_returns_findings():
    board = rt.CreativeRoundtable(_config(search_tool=None))
    _, log = board.review_and_enhance(DRAFT, UPSTREAM)
    assert len(log["research_findings"]) == 1   # falls back to model knowledge


# ----------------------------------------------------------------------
# graceful degradation — never crash the pipeline
# ----------------------------------------------------------------------
def test_falls_back_to_draft_when_craftsman_returns_junk():
    def chat(system, user):
        if "INTERNAL CRITIC" in system:
            return json.dumps(CRITICISMS)
        if "RESEARCH ASSISTANT" in system:
            return json.dumps(FINDINGS)
        return "I am not going to give you JSON, sorry."   # craftsman fails
    enhanced, log = rt.CreativeRoundtable(_config(llm_chat=chat)).review_and_enhance(
        DRAFT, UPSTREAM)
    assert enhanced == DRAFT                  # draft returned unchanged
    assert log["error"]                       # the failure is recorded for review


def test_falls_back_to_draft_when_critic_finds_nothing():
    enhanced, log = rt.CreativeRoundtable(
        _config(llm_chat=_router(criticisms=[]))).review_and_enhance(DRAFT, UPSTREAM)
    assert enhanced == DRAFT
    assert log["criticisms"] == []


def test_does_not_raise_when_a_subagent_explodes():
    def chat(system, user):
        raise RuntimeError("rate limited")
    enhanced, log = rt.CreativeRoundtable(_config(llm_chat=chat)).review_and_enhance(
        DRAFT, UPSTREAM)
    assert enhanced == DRAFT
    assert log["error"]


# ----------------------------------------------------------------------
# the log is persisted for the eval system (Quill/Flux)
# ----------------------------------------------------------------------
def test_log_saved_to_project_dir(tmp_path):
    board = rt.CreativeRoundtable(_config())
    board.review_and_enhance(DRAFT, UPSTREAM, project_dir=tmp_path)
    log_path = tmp_path / "roundtable_log.json"
    assert log_path.exists()
    saved = json.loads(log_path.read_text())
    assert saved["enhanced_artifact"]["hook"] == ENHANCED["hook"]
