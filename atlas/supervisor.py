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
