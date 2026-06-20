"""Offline proof for the summary-only memory store — NO network, NO API keys.

Run (from the project folder):  python tests/test_chat_state.py

Checks the NEW memory model: only the distilled summary persists across sessions
(the raw transcript does NOT), a "pending" recovery backlog round-trips, writes
are atomic, and a corrupt state file is recovered (backed up + fresh start)
instead of crashing.
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import chat_state


def test_summary_roundtrips_transcript_does_not(tmp):
    path = tmp / "chat_state.json"
    chat_state.save_summary(path, "User runs a faceless finance channel.")

    loaded = chat_state.load_state(path)
    assert loaded["summary"] == "User runs a faceless finance channel."
    assert loaded["transcript"] == [], "load must start a FRESH empty transcript"
    assert loaded["pending"] is None
    print("  PASS summary: summary persists; transcript is fresh-empty on load")


def test_old_format_transcript_is_ignored(tmp):
    # An old-format file still carrying a raw transcript must NOT be replayed.
    path = tmp / "chat_state.json"
    chat_state.atomic_write_json(path, {
        "summary": "Niche: AI tools.",
        "transcript": [{"role": "user", "content": "old raw turn"}],
        "updated": 123,
    })
    loaded = chat_state.load_state(path)
    assert loaded["summary"] == "Niche: AI tools."
    assert loaded["transcript"] == [], "stale on-disk transcript must be dropped"
    print("  PASS migrate: old-format raw transcript is ignored on load")


def test_pending_roundtrips(tmp):
    path = tmp / "chat_state.json"
    backlog = [{"role": "user", "content": "hi"},
               {"role": "scout", "content": "hey"}]
    chat_state.save_summary(path, "Known facts.", pending=backlog)

    loaded = chat_state.load_state(path)
    assert loaded["pending"] == backlog, "pending backlog must survive for recovery"
    assert loaded["summary"] == "Known facts."
    print("  PASS pending: recovery backlog round-trips")


def test_clean_save_clears_pending(tmp):
    path = tmp / "chat_state.json"
    chat_state.save_summary(path, "x", pending=[{"role": "user", "content": "q"}])
    chat_state.save_summary(path, "y")  # clean save, no pending arg
    loaded = chat_state.load_state(path)
    assert loaded["pending"] is None, "a clean save must not leave a stale pending"
    print("  PASS clean: clean save drops a stale pending")


def test_atomic_no_temp_left(tmp):
    path = tmp / "chat_state.json"
    chat_state.save_summary(path, "anything")
    leftovers = list(tmp.glob("*.tmp.*"))
    assert not leftovers, f"atomic write must leave no temp files, found {leftovers}"
    print("  PASS atomic: no temp files left behind")


def test_missing_returns_fresh(tmp):
    loaded = chat_state.load_state(tmp / "does_not_exist.json")
    assert loaded["transcript"] == [] and loaded["summary"] == ""
    assert loaded["pending"] is None
    print("  PASS missing: absent file yields a fresh state")


def test_corrupt_recovers(tmp):
    path = tmp / "chat_state.json"
    path.write_text("{this is : not valid json,,,")
    loaded = chat_state.load_state(path)          # must NOT raise
    assert loaded["summary"] == "" and loaded["transcript"] == []
    backups = list(tmp.glob("chat_state.json.corrupt-*"))
    assert backups, "corrupt file must be backed up, not silently dropped"
    print("  PASS corrupt: bad file backed up + fresh start, no crash")


def main():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        test_summary_roundtrips_transcript_does_not(tmp)
        test_old_format_transcript_is_ignored(tmp)
        test_pending_roundtrips(tmp)
        test_clean_save_clears_pending(tmp)
        test_atomic_no_temp_left(tmp)
        test_missing_returns_fresh(tmp)
        test_corrupt_recovers(tmp)
    print("\n✅ PASS — summary-only memory persists, transcript stays in RAM, "
          "pending recovers, writes are atomic, corruption is survived.")


if __name__ == "__main__":
    main()
