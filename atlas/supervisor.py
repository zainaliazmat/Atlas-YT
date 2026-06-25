"""Atlas's decision seam — the supervisor brain's interface to the belt.

Slice 1 introduces the seam with ZERO behavior change: `safe_default_decider` reproduces
the dispatcher's historical failure policy exactly (a transient stage failure retries
while budget remains, else escalates; a human gate escalates = parks for sign-off). Later
slices swap in the LLM decider behind this same interface.

A Decision is a bounded instruction the dispatcher EXECUTES with its existing reliable
mechanics — the LLM (later) may propose ONLY from this legal set; it never touches the
belt directly.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# The full legal decision vocabulary (spec §1). Slice 1's executor handles RETRY_STAGE +
# ESCALATE (all the safe-default decider emits); later slices implement the rest.
DECISION_KINDS = ("PROCEED", "RETRY_STAGE", "FIX_AND_RERUN", "RERUN_FROM",
                  "APPROVE_GATE", "ESCALATE", "KILL")


@dataclass(frozen=True)
class Decision:
    """One bounded instruction returned by a decider. `kind` ∈ DECISION_KINDS."""
    kind: str
    stage: str | None = None
    gate: str | None = None
    reason: str = ""
    instructions: str = ""
    payload: dict = field(default_factory=dict)


def safe_default_decider(slug: str, result: dict, context: dict) -> Decision:
    """Today's dispatcher policy, expressed as a Decision (no LLM).

    `context` = {"attempts": int, "max_retries": int}. Reproduces historical behavior:
    - a TRANSIENT stage failure with retry budget left → RETRY_STAGE;
    - any other stage failure (transient exhausted or deterministic) → ESCALATE(failed),
      carrying the original failure_kind so the UI's retry-ability read is unchanged;
    - a human gate (blocked) → ESCALATE(gate) = park for sign-off;
    - anything else → PROCEED.
    """
    status = result.get("status")
    if status == "failed":
        kind = result.get("failure_kind", "transient")
        if kind == "transient" and \
                context.get("attempts", 0) < context.get("max_retries", 0):
            return Decision("RETRY_STAGE", stage=result.get("stage"),
                            reason="transient failure — retry")
        return Decision("ESCALATE", stage=result.get("stage"),
                        reason="; ".join(result.get("errors") or []) or "stage failed",
                        payload={"failure_kind": kind})
    if status == "blocked":
        return Decision("ESCALATE", gate=result.get("gate"),
                        reason=result.get("reason") or "awaiting your sign-off",
                        payload={"blocked": True})
    return Decision("PROCEED")


# ---------------------------------------------------------------------------
# Slice 2 — Task 1: parsing + schema validation
# ---------------------------------------------------------------------------

LEGAL_GATES = ("factcheck", "final_render")
_STAGE_REQUIRED = ("RETRY_STAGE", "RERUN_FROM", "FIX_AND_RERUN")


def decision_from_dict(d) -> "Decision | None":
    """Build a Decision from an untrusted parsed dict (the LLM's JSON). Returns None when
    `d` is not a dict or carries no string `kind` — the caller treats None as malformed."""
    if not isinstance(d, dict):
        return None
    kind = d.get("kind")
    if not isinstance(kind, str) or not kind:
        return None
    payload = d.get("payload")
    return Decision(
        kind=kind,
        stage=d.get("stage"),
        gate=d.get("gate"),
        reason=d.get("reason") or "",
        instructions=d.get("instructions") or "",
        payload=payload if isinstance(payload, dict) else {},
    )


def validate_decision(decision: "Decision") -> "Decision":
    """Coerce an illegal/malformed Decision to ESCALATE (schema legality only — the
    factcheck-approve prohibition + budget caps are EXECUTOR logic, not here). Returns the
    original object unchanged when legal, so callers can identity-check in tests."""
    kind = getattr(decision, "kind", None)
    if kind not in DECISION_KINDS:
        return Decision("ESCALATE", reason=f"illegal decision kind {kind!r}",
                        payload={"illegal_kind": kind})
    if kind in _STAGE_REQUIRED and not decision.stage:
        return Decision("ESCALATE", reason=f"illegal {kind}: missing stage",
                        payload={"illegal_kind": kind})
    if kind == "APPROVE_GATE" and decision.gate not in LEGAL_GATES:
        return Decision("ESCALATE", reason=f"illegal APPROVE_GATE: gate {decision.gate!r}",
                        payload={"illegal_kind": kind})
    return decision


# ---------------------------------------------------------------------------
# Slice 2 — Task 2: persisted supervisor counters (pure dict transforms)
# ---------------------------------------------------------------------------

def ensure_supervisor_block(project: dict) -> dict:
    """Return project['supervisor'], creating the zero-state block if absent (idempotent)."""
    blk = project.get("supervisor")
    if not isinstance(blk, dict):
        blk = {"decisions": 0, "fix_attempts": {}, "log": []}
        project["supervisor"] = blk
    blk.setdefault("decisions", 0)
    blk.setdefault("fix_attempts", {})
    blk.setdefault("log", [])
    blk.setdefault("fix_history", {})
    return blk


def bump_decision(project: dict) -> int:
    blk = ensure_supervisor_block(project)
    blk["decisions"] += 1
    return blk["decisions"]


def decisions_count(project: dict) -> int:
    return ensure_supervisor_block(project)["decisions"]


def bump_fix_attempt(project: dict, gate: str) -> int:
    blk = ensure_supervisor_block(project)
    blk["fix_attempts"][gate] = blk["fix_attempts"].get(gate, 0) + 1
    return blk["fix_attempts"][gate]


def fix_attempts(project: dict, gate: str) -> int:
    return ensure_supervisor_block(project)["fix_attempts"].get(gate, 0)


# Engine decision vocabulary → plain-English phrases for the live "Atlas is doing X"
# line. The dashboard must never show a raw enum token (RETRY_STAGE) to the CEO.
# {stage} is filled from the log entry when present.
_ATLAS_PHRASES = {
    "PROCEED": "Proceeding to the next stage",
    "RETRY_STAGE": "Retrying {stage} after a transient hiccup",
    "FIX_AND_RERUN": "Auto-fixing and re-running {stage}",
    "RERUN_FROM": "Re-running from {stage}",
    "APPROVE_GATE": "Approved the gate — continuing",
    "ESCALATE": "Escalated to you",
    "KILL": "Stopped this video",
}


def humanize_atlas_activity(entry: dict) -> str:
    """Turn one supervisor.log entry into a plain-English 'Atlas is doing X' line.

    Maps the decision `kind` (engine vocabulary) to a human phrase and appends the
    free-text reason verbatim. Unknown/empty kinds degrade safely to a neutral line
    rather than leaking a bare token."""
    entry = entry or {}
    kind = entry.get("kind") or ""
    stage = entry.get("stage") or "the stage"
    phrase = _ATLAS_PHRASES.get(kind)
    if phrase:
        phrase = phrase.format(stage=stage)
    elif kind:
        phrase = "Working"          # known-shaped but unmapped kind — never echo the token
    else:
        phrase = "Working"
    text = f"Atlas: {phrase}"
    reason = entry.get("reason")
    if reason:
        text += f" — {reason}"
    return text


def record_decision(project: dict, *, trigger: str, stage, kind: str, reason: str = "",
                    latency_ms: int | None = None, model: str | None = None) -> dict:
    """Append the decision to BOTH supervisor.log (rich) and project.history (the shared
    audit feed both Atlas call-shapes read). Returns the log entry."""
    blk = ensure_supervisor_block(project)
    entry = {"ts": time.time(), "trigger": trigger, "stage": stage, "kind": kind,
             "reason": reason, "latency_ms": latency_ms, "model": model}
    blk["log"].append(entry)
    project.setdefault("history", []).append(
        {"ts": entry["ts"], "stage": stage, "initiator": "atlas",
         "decision": f"atlas: {kind}", "why": reason})
    return entry


# ---------------------------------------------------------------------------
# Slice 4 — Task 1: fix-attempt snapshot history (pure, parallel to cap counter)
# ---------------------------------------------------------------------------

def record_fix_snapshot(project: dict, gate: str, *, attempt_no: int, flagged: list,
                        instructions: str = "") -> dict:
    """Capture a fix attempt's 'before' state (the flagged claims + Atlas's instructions),
    PARALLEL to the int fix_attempts counter (which the cap reads). The escalation card
    diffs successive snapshots against the current report to show the CEO the trajectory."""
    blk = ensure_supervisor_block(project)
    blk.setdefault("fix_history", {})
    entry = {"n": attempt_no, "ts": time.time(),
             "flagged_before": flagged or [], "instructions": instructions or ""}
    blk["fix_history"].setdefault(gate, []).append(entry)
    return entry


def fix_history(project: dict, gate: str) -> list:
    return ensure_supervisor_block(project).get("fix_history", {}).get(gate, [])
