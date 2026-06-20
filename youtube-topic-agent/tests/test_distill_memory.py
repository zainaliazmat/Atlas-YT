"""Offline proof for the distill-based memory model — NO network, NO API keys.

Run (from the project folder):  python tests/test_distill_memory.py

The distiller (the only thing that would hit an LLM) is MOCKED throughout, so we
assert the PLUMBING only:
  - distill runs on /exit, /new, /summary, and SIGINT; the summary is saved and
    the in-RAM transcript is cleared.
  - merge: the distiller receives (existing summary + the session's turns) and
    its result is what gets saved; the transcript is cleared.
  - failure fallback: distiller raises -> raw transcript parked under "pending"
    (not lost) and the program still exits; the next launch folds "pending" in
    and clears it.
  - launch loads ONLY the summary: a fresh session's context carries the summary
    text and NOT old raw turns.

HONEST NOTE: whether the summary actually keeps signal and drops junk is a MANUAL
check (real chat with junk + facts -> /exit -> relaunch -> "what do you know
about me"). Only the plumbing is unit-tested here.
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import chat
import chat_state


def _fresh_state(summary="", turns=None, pending=None):
    return {"summary": summary,
            "transcript": list(turns or []),
            "pending": pending}


def _two_turns():
    return [{"role": "user", "content": "my channel is FinanceFox, faceless finance"},
            {"role": "scout", "content": "got it — faceless finance, noted"}]


class RecordingDistiller:
    """Mock distiller: records the (summary, transcript) it was called with."""
    def __init__(self, result="DISTILLED SUMMARY"):
        self.result = result
        self.calls = []

    def __call__(self, summary, transcript):
        self.calls.append((summary, list(transcript)))
        return self.result


class RaisingDistiller:
    def __call__(self, summary, transcript):
        raise RuntimeError("simulated LLM outage")


def test_exit_distills_and_clears(tmp):
    chat.STATE_PATH = tmp / "chat_state.json"
    state = _fresh_state(summary="old", turns=_two_turns())
    distiller = RecordingDistiller("merged summary")

    keep_looping = chat.handle_command(state, "sys", None, distiller, "/exit")

    assert keep_looping is False, "/exit must end the loop"
    assert distiller.calls, "/exit must invoke the distiller"
    assert state["transcript"] == [], "/exit must clear the in-RAM transcript"
    saved = chat_state.load_state(chat.STATE_PATH)
    assert saved["summary"] == "merged summary", "distilled summary must be saved"
    assert saved["pending"] is None
    print("  PASS /exit: distills, saves summary, clears transcript")


def test_new_distills_keeps_summary_clears_transcript(tmp):
    chat.STATE_PATH = tmp / "chat_state.json"
    state = _fresh_state(summary="old", turns=_two_turns())
    distiller = RecordingDistiller("kept + merged")

    keep_looping = chat.handle_command(state, "sys", None, distiller, "/new")

    assert keep_looping is True, "/new keeps the session going"
    assert distiller.calls, "/new must distill FIRST (capture facts)"
    assert state["transcript"] == [], "/new must clear the transcript"
    assert state["summary"] == "kept + merged", "/new keeps the (updated) summary"
    saved = chat_state.load_state(chat.STATE_PATH)
    assert saved["summary"] == "kept + merged"
    print("  PASS /new: distills first, keeps summary, clears transcript")


def test_summary_distills_and_shows_real_content(tmp, capsys=None):
    chat.STATE_PATH = tmp / "chat_state.json"
    state = _fresh_state(summary="old", turns=_two_turns())
    distiller = RecordingDistiller("FinanceFox — faceless finance channel")

    chat.handle_command(state, "sys", None, distiller, "/summary")

    assert distiller.calls, "/summary must invoke the distiller"
    assert state["transcript"] == [], "/summary checkpoint clears the transcript"
    saved = chat_state.load_state(chat.STATE_PATH)
    assert saved["summary"] == "FinanceFox — faceless finance channel", \
        "/summary must SAVE the distilled summary (a real checkpoint)"
    print("  PASS /summary: distills, saves, clears transcript")


def test_sigint_distills_and_exits(tmp):
    chat.STATE_PATH = tmp / "chat_state.json"
    state = _fresh_state(summary="old", turns=_two_turns())
    distiller = RecordingDistiller("after ctrl-c")
    chat._SESSION.update(state=state, distiller=distiller, interrupting=False)

    raised = False
    try:
        chat._sigint_handler(2, None)  # 2 == SIGINT
    except SystemExit:
        raised = True

    assert raised, "SIGINT handler must exit the program"
    assert distiller.calls, "SIGINT must invoke the distiller (graceful save)"
    assert state["transcript"] == [], "SIGINT must clear the transcript"
    saved = chat_state.load_state(chat.STATE_PATH)
    assert saved["summary"] == "after ctrl-c"
    print("  PASS SIGINT: graceful distill + save + exit, transcript cleared")


def test_merge_receives_existing_summary_plus_turns(tmp):
    chat.STATE_PATH = tmp / "chat_state.json"
    turns = _two_turns()
    state = _fresh_state(summary="EXISTING FACTS", turns=turns)
    distiller = RecordingDistiller("new")

    chat.distill_and_save(state, distiller)

    seen_summary, seen_turns = distiller.calls[0]
    assert seen_summary == "EXISTING FACTS", "distiller must MERGE onto existing"
    assert seen_turns == turns, "distiller must receive this session's turns"
    assert state["transcript"] == [], "transcript cleared after a successful merge"
    assert chat_state.load_state(chat.STATE_PATH)["summary"] == "new"
    print("  PASS merge: distiller gets (existing summary + session turns)")


def test_failure_parks_pending_not_lost(tmp):
    chat.STATE_PATH = tmp / "chat_state.json"
    turns = _two_turns()
    state = _fresh_state(summary="safe summary", turns=turns)

    ok = chat.distill_and_save(state, RaisingDistiller())

    assert ok is False, "a failing distill must report failure"
    saved = chat_state.load_state(chat.STATE_PATH)
    assert saved["summary"] == "safe summary", "existing summary must be kept on failure"
    assert saved["pending"] == turns, "raw transcript must be PARKED, not lost"
    print("  PASS failure: raw transcript parked under 'pending' (no data loss)")


def test_exit_still_exits_when_distill_fails(tmp):
    chat.STATE_PATH = tmp / "chat_state.json"
    state = _fresh_state(summary="s", turns=_two_turns())

    keep_looping = chat.handle_command(state, "sys", None, RaisingDistiller(), "/exit")

    assert keep_looping is False, "/exit must still exit even if distill fails"
    assert chat_state.load_state(chat.STATE_PATH)["pending"], "chat must be parked"
    print("  PASS /exit+fail: program still exits, chat parked under 'pending'")


def test_next_launch_folds_pending_and_clears(tmp):
    chat.STATE_PATH = tmp / "chat_state.json"
    parked = _two_turns()
    # Simulate the file a failed prior session left behind.
    chat_state.save_summary(chat.STATE_PATH, "prior summary", pending=parked)

    # Next launch: load (only summary + pending) then recover.
    state = chat_state.load_state(chat.STATE_PATH)
    assert state["transcript"] == [], "launch must not replay parked turns as transcript"
    assert state["pending"] == parked

    distiller = RecordingDistiller("folded-in summary")
    chat._recover_pending(state, distiller)

    seen_summary, seen_turns = distiller.calls[0]
    assert seen_summary == "prior summary" and seen_turns == parked, \
        "recovery must fold the parked turns into the existing summary"
    assert state["pending"] is None, "recovery must clear pending"
    saved = chat_state.load_state(chat.STATE_PATH)
    assert saved["summary"] == "folded-in summary"
    assert saved["pending"] is None, "pending must be cleared on disk after recovery"
    print("  PASS recovery: next launch folds 'pending' in and clears it")


def test_launch_loads_only_summary_not_raw_turns(tmp):
    chat.STATE_PATH = tmp / "chat_state.json"
    # A previous session distilled to this summary; an old raw turn is also on
    # disk (old format) and must NOT resurface.
    chat_state.atomic_write_json(chat.STATE_PATH, {
        "summary": "Creator: FinanceFox. Niche: faceless finance. Hates clickbait.",
        "transcript": [{"role": "user", "content": "RAW SECRET TURN lol nice weather"}],
        "updated": 1,
    })
    state = chat_state.load_state(chat.STATE_PATH)
    snapshot = chat.memory_snapshot({"wins": [], "runs": []})
    context = chat._context_summary(state, snapshot)

    assert "FinanceFox" in context, "fresh session context must carry the summary"
    assert "RAW SECRET TURN" not in context, "old raw turns must NOT be in context"
    assert state["transcript"] == [], "the in-RAM transcript must start empty"
    print("  PASS launch: context has the summary, not old raw turns")


def main():
    tests = [
        test_exit_distills_and_clears,
        test_new_distills_keeps_summary_clears_transcript,
        test_summary_distills_and_shows_real_content,
        test_sigint_distills_and_exits,
        test_merge_receives_existing_summary_plus_turns,
        test_failure_parks_pending_not_lost,
        test_exit_still_exits_when_distill_fails,
        test_next_launch_folds_pending_and_clears,
        test_launch_loads_only_summary_not_raw_turns,
    ]
    for t in tests:
        with tempfile.TemporaryDirectory() as d:
            t(pathlib.Path(d))
    print("\n✅ PASS — distill runs on /exit, /new, /summary, SIGINT; merges onto "
          "the existing summary; clears the transcript; never loses a chat on "
          "failure; and launch loads only the summary.")


if __name__ == "__main__":
    main()
