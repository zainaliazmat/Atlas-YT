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


# ---------------------------------------------------------------------------
# Slice 2 — Task 2: persisted supervisor counters
# ---------------------------------------------------------------------------
from supervisor import (ensure_supervisor_block, bump_decision, decisions_count,
                        bump_fix_attempt, fix_attempts, record_decision)


def test_ensure_block_is_idempotent():
    p = {}
    b = ensure_supervisor_block(p)
    assert b == {"decisions": 0, "fix_attempts": {}, "log": [], "fix_history": {}}
    b["decisions"] = 5
    assert ensure_supervisor_block(p)["decisions"] == 5  # does not clobber


def test_bump_decision_counts_up():
    p = {}
    assert bump_decision(p) == 1 and bump_decision(p) == 2
    assert decisions_count(p) == 2


def test_fix_attempts_are_per_gate():
    p = {}
    assert bump_fix_attempt(p, "factcheck") == 1
    assert bump_fix_attempt(p, "factcheck") == 2
    assert bump_fix_attempt(p, "final_render") == 1
    assert fix_attempts(p, "factcheck") == 2 and fix_attempts(p, "final_render") == 1
    assert fix_attempts(p, "never") == 0


def test_record_decision_appends_to_log_and_history():
    p = {"history": []}
    entry = record_decision(p, trigger="blocked", stage="script", kind="FIX_AND_RERUN",
                            reason="fix s5c2", latency_ms=1200, model="claude-opus-4-8")
    assert entry["kind"] == "FIX_AND_RERUN" and entry["latency_ms"] == 1200
    assert p["supervisor"]["log"][-1]["kind"] == "FIX_AND_RERUN"
    assert p["history"][-1]["decision"].startswith("atlas: FIX_AND_RERUN")
    assert p["history"][-1].get("initiator") == "atlas"


# ---------------------------------------------------------------------------
# Slice 4 — Task 1: fix-attempt snapshot history (parallel to cap counter)
# ---------------------------------------------------------------------------
from supervisor import record_fix_snapshot, fix_history


def test_record_fix_snapshot_appends_trajectory():
    p = {}
    e1 = record_fix_snapshot(p, "factcheck", attempt_no=1,
                             flagged=[{"claim_id": "s5c2", "status": "flagged"}],
                             instructions="drop s5c2")
    assert e1["n"] == 1 and e1["instructions"] == "drop s5c2"
    record_fix_snapshot(p, "factcheck", attempt_no=2, flagged=[], instructions="ok")
    hist = fix_history(p, "factcheck")
    assert [h["n"] for h in hist] == [1, 2]
    assert hist[0]["flagged_before"][0]["claim_id"] == "s5c2"
    assert fix_history(p, "never") == []


def test_fix_history_is_separate_from_fix_attempts_counter():
    from supervisor import bump_fix_attempt, fix_attempts
    p = {}
    bump_fix_attempt(p, "factcheck"); bump_fix_attempt(p, "factcheck")
    record_fix_snapshot(p, "factcheck", attempt_no=2, flagged=[], instructions="")
    assert fix_attempts(p, "factcheck") == 2          # the int cap counter is intact
    assert isinstance(p["supervisor"]["fix_attempts"]["factcheck"], int)
    assert len(fix_history(p, "factcheck")) == 1       # parallel list, unrelated to the int


# ---------------------------------------------------------------------------
# Live "Atlas is doing X" line — engine decision kinds must read as plain
# English, never raw enum tokens leaking into the CEO's face.
# ---------------------------------------------------------------------------
def test_humanize_maps_kind_to_plain_phrase():
    from supervisor import humanize_atlas_activity
    txt = humanize_atlas_activity({"kind": "RETRY_STAGE", "stage": "script"})
    assert "RETRY_STAGE" not in txt          # no raw enum token
    assert txt.startswith("Atlas: ")
    assert "script" in txt


def test_humanize_appends_reason_verbatim():
    from supervisor import humanize_atlas_activity
    txt = humanize_atlas_activity({"kind": "ESCALATE", "reason": "needs your call"})
    assert "Escalat" in txt and "needs your call" in txt


def test_humanize_handles_every_decision_kind_without_leaking_enum():
    from supervisor import humanize_atlas_activity, DECISION_KINDS
    for k in DECISION_KINDS:
        txt = humanize_atlas_activity({"kind": k, "stage": "render", "gate": "factcheck"})
        assert k not in txt, f"{k} leaked as raw token: {txt}"
        assert txt.startswith("Atlas: ")


def test_humanize_empty_or_unknown_is_safe():
    from supervisor import humanize_atlas_activity
    assert humanize_atlas_activity({}).startswith("Atlas:")
    assert humanize_atlas_activity({"kind": "WAT"}).startswith("Atlas:")
