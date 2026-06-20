"""Keep the chat prompt under a strict, configurable token budget.

We never let the live conversation grow unbounded. Each turn, before sending, we
check whether (system prompt + summary + reference context + recent turns + the
new user message) fits the budget. If not, we COMPACT: fold the oldest raw turns
into the rolling in-RAM `summary` and keep only the last few turns verbatim.

This is the IN-SESSION budget guard. It is distinct from the cross-session DISTILL
(chat.distill_and_save), which is what persists Magpie's durable memory at session
boundaries. The summary both touch is the same field; compaction just keeps a long
single session bounded.

The summarizer is INJECTED (a callable), so this whole module is unit-testable with
a fake summarizer and no API calls.
"""
from __future__ import annotations

from typing import Any, Callable

# Conservative defaults so even a small-context model has room to answer.
DEFAULT_BUDGET_TOKENS = 6000
DEFAULT_RECENT_WINDOW = 6   # keep this many raw turns verbatim
MIN_RECENT_WINDOW = 2       # never shrink the verbatim window below this
CHARS_PER_TOKEN = 4         # rough heuristic; no tokenizer dependency

# A Summarizer takes (existing_summary, turns_to_fold) and returns a new summary.
Summarizer = Callable[[str, list[dict[str, str]]], str]

COMPACTION_SYSTEM = (
    "You compress a conversation into a durable memo about the USER, for an "
    "asset-sourcing / licensing co-worker who must remember them across sessions."
)


def _compaction_user_prompt(existing_summary: str, turns: list[dict[str, str]]) -> str:
    convo = transcript_text(turns)
    return (
        "Here is what you already know about the user:\n"
        f"{existing_summary or '(nothing yet)'}\n\n"
        "Here are older conversation turns to fold in:\n"
        f"{convo}\n\n"
        "Update the memo. KEEP only durable, intelligence-improving facts: the "
        "kinds of videos and topics they make, their channel and audience, the "
        "eras / archives / look they keep reaching for, their licensing posture "
        "(monetized? attribution tolerance? jurisdictions that matter), and "
        "rulings they've made on what clears. DISCARD greetings, small talk, "
        "pleasantries, and transient chatter. Merge with what you already knew; "
        "don't repeat. Output ONLY the updated memo as concise prose — no preamble, "
        "no commentary."
    )


def make_summarizer(chat_fn: Callable[[str, str], str]) -> Summarizer:
    """Build a production summarizer from the provider seam llm.chat(system, user)."""
    def summarize(existing_summary: str, turns: list[dict[str, str]]) -> str:
        return chat_fn(COMPACTION_SYSTEM,
                       _compaction_user_prompt(existing_summary, turns)).strip()
    return summarize


def estimate_tokens(text: str, chars_per_token: int = CHARS_PER_TOKEN) -> int:
    """Rough token estimate. Intentionally simple and provider-independent."""
    return (len(text) + chars_per_token - 1) // chars_per_token


def transcript_text(turns: list[dict[str, str]]) -> str:
    """Render turns to the labeled text we both measure and send."""
    label = {"user": "User", "magpie": "Magpie"}
    return "\n".join(f"{label.get(t['role'], t['role'])}: {t['content']}" for t in turns)


def total_tokens(system: str, summary: str, extra: str,
                 turns: list[dict[str, str]], pending_user_msg: str) -> int:
    """Estimate the full prompt size for a prospective send."""
    blob = "\n".join([system, summary, extra, transcript_text(turns), pending_user_msg])
    return estimate_tokens(blob)


def compact(
    state: dict[str, Any],
    *,
    summarizer: Summarizer,
    system: str = "",
    extra: str = "",
    pending_user_msg: str = "",
    budget: int = DEFAULT_BUDGET_TOKENS,
    recent_window: int = DEFAULT_RECENT_WINDOW,
    min_window: int = MIN_RECENT_WINDOW,
) -> dict[str, Any]:
    """Ensure the next send fits `budget`, compacting `state` in place if needed.

    Folds the oldest turns of state['transcript'] into state['summary'] and trims
    the transcript to the recent window. Returns:
        {"compacted": bool, "fits": bool, "reason": str}
    fits=False means even the minimum window + this message won't fit (e.g. one
    enormous message) — the caller should warn the user and suggest /new.
    """
    info = {"compacted": False, "fits": True, "reason": ""}

    def over() -> bool:
        return total_tokens(system, state["summary"], extra,
                            state["transcript"], pending_user_msg) > budget

    # 1) Fold oldest turns into the summary while over budget and there are turns
    #    beyond the recent window to fold.
    while over() and len(state["transcript"]) > recent_window:
        fold = state["transcript"][:-recent_window]
        state["summary"] = summarizer(state["summary"], fold)
        state["transcript"] = state["transcript"][-recent_window:]
        info["compacted"] = True

    # 2) Still over budget? Shrink the verbatim window toward the floor.
    window = recent_window
    while over() and window > min_window and len(state["transcript"]) > min_window:
        window -= 1
        fold = state["transcript"][:-window]
        if fold:
            state["summary"] = summarizer(state["summary"], fold)
            state["transcript"] = state["transcript"][-window:]
            info["compacted"] = True

    # 3) Still over -> a single message (summary or one turn) is too big.
    if over():
        info["fits"] = False
        info["reason"] = (
            "This turn is too large to fit the context budget even after "
            "compaction. Start a fresh thread with /new (your summary is kept)."
        )
    return info
