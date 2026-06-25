"""The unified Atlas request path — one typed front door for every dashboard/chat action.

Every button and chat intent becomes a `handle_request(...)` call; Atlas (the dispatcher's
reliable hands) executes it. This does NOT bypass any guarantee — it routes to the same
methods the per-action endpoints call, so behavior is identical and the never-ship-unverified
guard still lives in the executor + spine.
"""
from __future__ import annotations

INTENTS = ("make_video", "rerun", "retry", "cancel", "answer_escalation")
_ESCALATION_ACTIONS = ("approve", "guide", "kill")


class UnknownIntent(ValueError):
    """An intent (or answer_escalation action) outside the bounded vocabulary."""


def handle_request(dispatcher, settings_path, intent: str, args: dict) -> dict:
    args = args or {}
    if intent == "make_video":
        result = dispatcher.trigger(
            brief=args.get("brief"), topic=args.get("topic"), length=args.get("length"),
            niche=args.get("niche"), gates=args.get("gates", True), initiator="ceo")
    elif intent == "rerun":
        result = dispatcher.rerun(args["slug"], from_stage=args.get("from_stage"),
                                  initiator="ceo")
    elif intent == "retry":
        result = dispatcher.retry(args["slug"], initiator="ceo")
    elif intent == "cancel":
        result = dispatcher.cancel(args["slug"], initiator="ceo")
    elif intent == "answer_escalation":
        action = args.get("action")
        if action == "approve":
            result = dispatcher.resume(args["slug"], args["gate"], initiator="ceo", wait=True)
        elif action == "guide":
            result = dispatcher.guide(args["slug"], args["instructions"], initiator="ceo")
        elif action == "kill":
            result = dispatcher.kill(args["slug"], args.get("reason", ""), initiator="ceo")
        else:
            raise UnknownIntent(f"unknown escalation action: {action!r}")
    else:
        raise UnknownIntent(f"unknown intent: {intent!r}")
    return {"intent": intent, "result": result}
