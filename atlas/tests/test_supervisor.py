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
