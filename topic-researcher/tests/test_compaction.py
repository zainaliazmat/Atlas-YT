"""Offline proof for the in-session compaction budget guard — NO network.

Run:  python tests/test_compaction.py   (or: pytest tests/test_compaction.py)

The summarizer is MOCKED (a fake callable), so we assert the budget plumbing:
  - under budget -> no compaction
  - over budget -> oldest turns fold into the summary, transcript trims, fits=True
  - one oversized message -> fits=False (caller should suggest /new)
  - transcript_text uses the "Sage" label
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import compaction  # noqa: E402


def _fake_summarizer(existing, turns):
    return (existing + " | folded:%d" % len(turns)).strip(" |")


def test_transcript_text_uses_sage_label():
    out = compaction.transcript_text([{"role": "sage", "content": "hello"},
                                      {"role": "user", "content": "hi"}])
    assert out.startswith("Sage: hello")
    assert "User: hi" in out


def test_under_budget_no_compaction():
    state = {"summary": "", "transcript": [{"role": "user", "content": "short"}]}
    info = compaction.compact(state, summarizer=_fake_summarizer, budget=100000)
    assert info["compacted"] is False and info["fits"] is True
    assert len(state["transcript"]) == 1


def test_over_budget_folds_oldest():
    turns = [{"role": "user", "content": "x" * 400} for _ in range(10)]
    state = {"summary": "", "transcript": list(turns)}
    # budget fits ~3 recent turns (~300 tokens) but not all 10 (~1000 tokens).
    info = compaction.compact(state, summarizer=_fake_summarizer,
                              budget=400, recent_window=3)
    assert info["compacted"] is True and info["fits"] is True
    assert len(state["transcript"]) <= 3, "should trim to the recent window"
    assert "folded" in state["summary"], "older turns folded into summary"


def test_single_oversized_message_does_not_fit():
    state = {"summary": "", "transcript": [{"role": "user", "content": "z" * 5000}]}
    info = compaction.compact(state, summarizer=_fake_summarizer, budget=50)
    assert info["fits"] is False
    assert "/new" in info["reason"]


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
            passed += 1
    print(f"\n{passed} tests passed.")
