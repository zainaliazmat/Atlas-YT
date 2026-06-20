"""Offline proof for the context-budget compaction — NO network, NO API keys.

Run (from the project folder):  python tests/test_compaction.py

Uses a FAKE summarizer (no LLM) so we can assert the mechanics: compaction fires
when the budget is exceeded, old turns fold into the summary, only the recent
window survives verbatim, and an oversized single message is reported as
unfittable instead of crashing.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import compaction


def fake_summarizer(existing, turns):
    """Deterministic stand-in: records how many turns it folded."""
    folded = len(turns)
    prev = existing + " " if existing else ""
    return f"{prev}[folded {folded} turns]"


def test_no_compaction_when_small():
    state = {"summary": "", "transcript": [
        {"role": "user", "content": "hi"},
        {"role": "scout", "content": "hey"},
    ]}
    info = compaction.compact(state, summarizer=fake_summarizer, budget=6000,
                              pending_user_msg="what's up")
    assert info["compacted"] is False, "tiny conversation must not compact"
    assert info["fits"] is True
    assert len(state["transcript"]) == 2, "transcript untouched when under budget"
    print("  PASS small: no compaction under budget")


def test_compaction_folds_old_turns():
    # 20 chunky turns, tiny budget -> must compact down to the recent window.
    turns = [{"role": "user" if i % 2 == 0 else "scout",
              "content": f"turn {i} " + "x" * 200} for i in range(20)]
    state = {"summary": "", "transcript": list(turns)}
    info = compaction.compact(state, summarizer=fake_summarizer, budget=300,
                              recent_window=6, pending_user_msg="next")
    assert info["compacted"] is True, "must compact when over budget"
    assert len(state["transcript"]) <= 6, \
        f"recent window must be trimmed, got {len(state['transcript'])}"
    assert "[folded" in state["summary"], "older turns must fold into the summary"
    # The surviving turns must be the LAST ones, not the first.
    assert state["transcript"][-1]["content"].startswith("turn 19"), \
        "the most recent turn must survive verbatim"
    print("  PASS fold: old turns summarized, recent window kept verbatim")


def test_huge_single_message_reported_unfittable():
    state = {"summary": "", "transcript": [
        {"role": "user", "content": "x" * 100_000},  # one giant message
    ]}
    info = compaction.compact(state, summarizer=fake_summarizer, budget=300,
                              recent_window=6, pending_user_msg="hi")
    assert info["fits"] is False, "an oversized message must report fits=False"
    assert "/new" in info["reason"], "reason should guide the user to /new"
    print("  PASS huge: oversized message reported, not crashed")


def test_estimate_monotonic():
    assert compaction.estimate_tokens("") == 0
    assert compaction.estimate_tokens("a" * 40) == 10  # 40 chars / 4
    print("  PASS estimate: token heuristic behaves")


def main():
    test_no_compaction_when_small()
    test_compaction_folds_old_turns()
    test_huge_single_message_reported_unfittable()
    test_estimate_monotonic()
    print("\n✅ PASS — compaction respects the budget, folds signal into summary, "
          "and fails soft on oversized turns.")


if __name__ == "__main__":
    main()
