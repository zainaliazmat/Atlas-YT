"""The LLM decider seam: injected chat_fn, JSON → validated Decision, safe fallback."""
import json
import atlas_decider
from supervisor import Decision


def _result_block():
    return {"status": "blocked", "gate": "factcheck", "stage": "factcheck",
            "reason": "unverified claims"}


def _ctx():
    return {"attempts": 0, "max_retries": 1, "fix_attempts": {"factcheck": 0},
            "decisions": 0, "flagged_claims": [{"claim_id": "s5c2", "claim_text": "X",
            "status": "flagged", "note": "no source"}], "history": []}


def test_decider_parses_a_legal_json_decision():
    def chat_fn(system, user):
        return json.dumps({"kind": "FIX_AND_RERUN", "stage": "script",
                           "gate": "factcheck", "instructions": "drop s5c2 or source it",
                           "reason": "claim unsupported"})
    decide = atlas_decider.make_llm_decider(chat_fn=chat_fn)
    d = decide("vid", _result_block(), _ctx())
    assert d.kind == "FIX_AND_RERUN" and d.stage == "script"
    assert "s5c2" in d.instructions


def test_decider_extracts_json_from_chatty_reply():
    def chat_fn(system, user):
        return "Sure! Here is my call:\n```json\n{\"kind\": \"ESCALATE\", \"reason\": \"unfixable\"}\n```\nThanks"
    decide = atlas_decider.make_llm_decider(chat_fn=chat_fn)
    assert decide("vid", _result_block(), _ctx()).kind == "ESCALATE"


def test_decider_coerces_illegal_decision_to_escalate():
    def chat_fn(system, user):
        return json.dumps({"kind": "APPROVE_GATE", "gate": "factcheck"})  # legal schema...
    # schema-legal, but the EXECUTOR (not the decider) bans factcheck-approve; the decider
    # returns it as-is and the dispatcher rejects it. Here we only assert it parses.
    d = atlas_decider.make_llm_decider(chat_fn=chat_fn)("vid", _result_block(), _ctx())
    assert d.kind == "APPROVE_GATE" and d.gate == "factcheck"


def test_decider_coerces_unknown_kind_to_escalate():
    def chat_fn(system, user):
        return json.dumps({"kind": "NUKE"})
    assert atlas_decider.make_llm_decider(chat_fn=chat_fn)("vid", _result_block(), _ctx()).kind == "ESCALATE"


def test_decider_falls_back_to_safe_default_on_chat_error():
    def chat_fn(system, user):
        raise RuntimeError("LLM down")
    decide = atlas_decider.make_llm_decider(chat_fn=chat_fn)
    # safe default on a transient stage failure with budget → RETRY_STAGE (today's policy)
    res = {"status": "failed", "failure_kind": "transient", "stage": "script"}
    assert decide("vid", res, {"attempts": 0, "max_retries": 1}).kind == "RETRY_STAGE"


def test_decider_escalates_on_unparseable_reply():
    def chat_fn(system, user):
        return "I am not going to give you JSON, sorry."
    assert atlas_decider.make_llm_decider(chat_fn=chat_fn)("vid", _result_block(), _ctx()).kind == "ESCALATE"


def test_build_prompt_includes_flagged_claims_and_counters():
    system, user = atlas_decider.build_decision_prompt("vid", _result_block(), _ctx())
    assert "factcheck" in user and "s5c2" in user
    assert "PROCEED" in system and "FIX_AND_RERUN" in system  # the legal vocabulary


def test_build_prompt_surfaces_render_budget():
    result = {"status": "blocked", "gate": "final_render", "stage": "render"}
    ctx = {"attempts": 0, "max_retries": 1, "fix_attempts": {}, "decisions": 0,
           "flagged_claims": [], "history": [],
           "render_plan": {"scenes": 5, "est_runtime_sec": 200}, "render_budget_sec": 450.0}
    system, user = atlas_decider.build_decision_prompt("vid", result, ctx)
    assert "200" in user and "450" in user
    assert "budget" in system.lower()
