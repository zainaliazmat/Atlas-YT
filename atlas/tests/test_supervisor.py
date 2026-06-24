"""Unit tests for the supervisor decision seam (Slice 1).

`safe_default_decider` must reproduce the dispatcher's historical failure policy as
Decision values — proven here in isolation (no dispatcher, no LLM)."""
from supervisor import DECISION_KINDS, Decision, safe_default_decider


def _ctx(attempts=0, max_retries=1):
    return {"attempts": attempts, "max_retries": max_retries}


def test_transient_failure_with_budget_retries():
    d = safe_default_decider("s", {"status": "failed", "failure_kind": "transient",
                                   "stage": "script"}, _ctx(attempts=0, max_retries=1))
    assert d.kind == "RETRY_STAGE" and d.stage == "script"


def test_transient_failure_without_budget_escalates():
    d = safe_default_decider("s", {"status": "failed", "failure_kind": "transient",
                                   "stage": "script", "errors": ["boom"]},
                             _ctx(attempts=1, max_retries=1))
    assert d.kind == "ESCALATE" and d.stage == "script"
    assert d.payload.get("failure_kind") == "transient"
    assert "boom" in d.reason


def test_deterministic_failure_never_retries():
    d = safe_default_decider("s", {"status": "failed", "failure_kind": "deterministic",
                                   "stage": "compose"}, _ctx(attempts=0, max_retries=5))
    assert d.kind == "ESCALATE"
    assert d.payload.get("failure_kind") == "deterministic"


def test_blocked_gate_escalates_as_a_gate():
    d = safe_default_decider("s", {"status": "blocked", "gate": "factcheck",
                                   "reason": "awaiting"}, _ctx())
    assert d.kind == "ESCALATE" and d.gate == "factcheck"
    assert d.payload.get("blocked") is True


def test_non_exception_status_proceeds():
    assert safe_default_decider("s", {"status": "done"}, _ctx()).kind == "PROCEED"


def test_decision_kind_is_always_legal():
    for result in ({"status": "failed", "failure_kind": "transient"},
                   {"status": "failed", "failure_kind": "deterministic"},
                   {"status": "blocked"}, {"status": "done"}):
        assert safe_default_decider("s", result, _ctx()).kind in DECISION_KINDS


# ---------------------------------------------------------------------------
# Slice 2 — Task 1: decision_from_dict + validate_decision
# ---------------------------------------------------------------------------
from supervisor import decision_from_dict, validate_decision, LEGAL_GATES


def test_decision_from_dict_round_trips_fields():
    d = decision_from_dict({"kind": "FIX_AND_RERUN", "stage": "script",
                            "gate": "factcheck", "instructions": "fix s5c2",
                            "reason": "claim unsupported", "payload": {"x": 1}})
    assert d.kind == "FIX_AND_RERUN" and d.stage == "script" and d.gate == "factcheck"
    assert d.instructions == "fix s5c2" and d.payload == {"x": 1}


def test_decision_from_dict_rejects_non_dict_and_missing_kind():
    assert decision_from_dict("nope") is None
    assert decision_from_dict({"stage": "script"}) is None


def test_validate_passes_a_legal_decision():
    d = Decision("FIX_AND_RERUN", stage="script", gate="factcheck")
    assert validate_decision(d) is d


def test_validate_coerces_unknown_kind_to_escalate():
    d = validate_decision(Decision("DELETE_EVERYTHING"))
    assert d.kind == "ESCALATE" and "illegal" in d.reason.lower()
    assert d.payload.get("illegal_kind") == "DELETE_EVERYTHING"


def test_validate_requires_stage_for_rerun_kinds():
    for kind in ("RETRY_STAGE", "RERUN_FROM", "FIX_AND_RERUN"):
        assert validate_decision(Decision(kind)).kind == "ESCALATE"
        assert validate_decision(Decision(kind, stage="script")).kind == kind


def test_validate_requires_real_gate_for_approve():
    assert validate_decision(Decision("APPROVE_GATE")).kind == "ESCALATE"
    assert validate_decision(Decision("APPROVE_GATE", gate="bogus")).kind == "ESCALATE"
    assert validate_decision(Decision("APPROVE_GATE", gate="factcheck")).kind == "APPROVE_GATE"
    assert tuple(LEGAL_GATES) == ("factcheck", "final_render")
