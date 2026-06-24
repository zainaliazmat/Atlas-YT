"""Atlas's single-shot decision call — the LLM behind the dispatcher's `decide_fn` seam.

`make_llm_decider(chat_fn=…)` returns a `(slug, result, context) -> Decision` callable with
the SAME signature as `supervisor.safe_default_decider`, so it drops straight into
`Dispatcher(decide_fn=…)`. The LLM only PROPOSES — `supervisor.validate_decision` clamps the
reply to the legal vocabulary, and the dispatcher's executor enforces the hard guarantees
(never approve a factcheck block, cap fix attempts, budget). Any chat error degrades to the
safe-default decider = today's deterministic policy.
"""
from __future__ import annotations

import json
from typing import Callable

import llm
import supervisor
from supervisor import Decision

DECIDER_MODEL = "claude-opus-4-8"

_SYSTEM = """You are Atlas, the autonomous supervisor of a YouTube video production belt.
A stage just FAILED or a gate is BLOCKED. Decide the single best next move. You may ONLY
return one decision from this exact vocabulary (JSON object, no prose):

  PROCEED                              — the exception is benign; continue down the belt
  RETRY_STAGE   {stage}               — re-run a station after a transient hiccup
  FIX_AND_RERUN {stage, instructions} — delegate a fix to the specialist, then re-run from
                                        that station. For a FACT-CHECK block: stage="script",
                                        instructions = concrete edits so Marlow re-grounds or
                                        drops the flagged claims.
  RERUN_FROM    {stage}               — send the video back to an earlier station
  APPROVE_GATE  {gate}                — self-approve a gate (NEVER legal for factcheck)
  ESCALATE      {reason}              — hand the decision to the CEO
  KILL          {reason}              — abandon a genuinely unworkable video

Reply with ONE JSON object: {"kind": "...", "stage": "...", "gate": "...",
"instructions": "...", "reason": "..."} — include only the fields the kind needs.
NEVER approve a fact-check block: a video that fails fact-check must never ship. If you
cannot fix it within the attempts left, ESCALATE.
"""


def build_decision_prompt(slug: str, result: dict, context: dict) -> tuple[str, str]:
    """Compact, fully-specified decision brief — underspecified input is where an LLM
    hallucinates, so hand it the failing stage, contract errors, flagged claims, the
    attempt counters, and recent history."""
    brief = {
        "slug": slug,
        "status": result.get("status"),
        "stage": result.get("stage"),
        "gate": result.get("gate"),
        "errors": result.get("errors") or [],
        "reason": result.get("reason") or "",
        "flagged_claims": context.get("flagged_claims") or [],
        "counters": {
            "transient_attempts": context.get("attempts", 0),
            "max_retries": context.get("max_retries", 0),
            "fix_attempts": context.get("fix_attempts") or {},
            "decisions_so_far": context.get("decisions", 0),
        },
        "recent_history": (context.get("history") or [])[-6:],
    }
    user = "DECIDE on this exception:\n" + json.dumps(brief, indent=2, default=str)
    return _SYSTEM, user


def _extract_json(text: str):
    """Pull the first JSON object out of a possibly chatty reply. Returns a dict or None."""
    if not isinstance(text, str):
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def make_llm_decider(chat_fn: Callable[[str, str], str] | None = None, *,
                     model: str = DECIDER_MODEL,
                     safe_default=supervisor.safe_default_decider):
    """Build a decider callable for `Dispatcher(decide_fn=…)`."""
    chat_fn = chat_fn or (lambda system, user: llm.chat(system, user, model=model))

    def decide(slug: str, result: dict, context: dict) -> Decision:
        system, user = build_decision_prompt(slug, result, context)
        try:
            reply = chat_fn(system, user)
        except Exception:  # noqa: BLE001 — any LLM failure degrades to today's policy
            return safe_default(slug, result, context)
        parsed = _extract_json(reply)
        decision = supervisor.decision_from_dict(parsed)
        if decision is None:
            return Decision("ESCALATE", gate=result.get("gate"), stage=result.get("stage"),
                            reason="Atlas could not produce a valid decision",
                            payload={"blocked": bool(result.get("gate"))})
        return supervisor.validate_decision(decision)

    return decide
