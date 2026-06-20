"""Offline proof for the storage layer — NO network.

Run:  python tests/test_chat_state.py   (or: pytest tests/test_chat_state.py)

Asserts:
  - atomic_write_json + load_json round-trip
  - a corrupt file is backed up (not lost) and load returns the default
  - load_state loads ONLY the summary (never replays a saved transcript) and
    surfaces a 'pending' recovery list
  - save_summary omits 'pending' on a clean save
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import chat_state  # noqa: E402


def test_atomic_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "m.json"
        chat_state.atomic_write_json(p, {"runs": [1, 2]})
        assert chat_state.load_json(p, {}) == {"runs": [1, 2]}


def test_missing_returns_default():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "nope.json"
        assert chat_state.load_json(p, {"x": 1}) == {"x": 1}


def test_corrupt_file_is_backed_up_and_recovered():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "m.json"
        p.write_text("{not valid json")
        got = chat_state.load_json(p, {"safe": True})
        assert got == {"safe": True}
        backups = list(pathlib.Path(d).glob("m.json.corrupt-*"))
        assert backups, "corrupt file should be preserved as a backup"


def test_load_state_ignores_transcript_keeps_summary_and_pending():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "chat_state.json"
        # An old-format file with a raw transcript that must NOT be replayed.
        chat_state.atomic_write_json(p, {
            "summary": "user makes space docs",
            "transcript": [{"role": "user", "content": "old raw turn"}],
            "pending": [{"role": "user", "content": "unsaved turn"}],
        })
        state = chat_state.load_state(p)
        assert state["summary"] == "user makes space docs"
        assert state["transcript"] == [], "raw transcript must not be loaded"
        assert state["pending"] == [{"role": "user", "content": "unsaved turn"}]


def test_save_summary_clean_has_no_pending():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "chat_state.json"
        chat_state.save_summary(p, "clean summary")
        on_disk = json.loads(p.read_text())
        assert on_disk["summary"] == "clean summary"
        assert "pending" not in on_disk
        # and with pending given, it IS written
        chat_state.save_summary(p, "s", pending=[{"role": "user", "content": "x"}])
        assert "pending" in json.loads(p.read_text())


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
            passed += 1
    print(f"\n{passed} tests passed.")
