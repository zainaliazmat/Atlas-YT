"""Atomic writes + tolerant/corrupt-recovery loads, mirroring the fleet."""
import json

import chat_state


def test_atomic_write_then_load(tmp_path):
    p = tmp_path / "state.json"
    chat_state.atomic_write_json(p, {"a": 1, "b": [1, 2]})
    assert json.loads(p.read_text()) == {"a": 1, "b": [1, 2]}
    # no temp file left behind
    assert not list(tmp_path.glob("*.tmp.*"))


def test_load_missing_returns_default(tmp_path):
    assert chat_state.load_json(tmp_path / "nope.json", {"d": 1}) == {"d": 1}


def test_corrupt_file_is_backed_up_and_default_returned(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{ not valid json ]")
    out = chat_state.load_json(p, {"safe": True})
    assert out == {"safe": True}
    backups = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(backups) == 1  # nothing silently lost


def test_load_state_drops_transcript_keeps_summary_and_pending(tmp_path):
    p = tmp_path / "state.json"
    chat_state.atomic_write_json(p, {
        "summary": "the CEO runs an AI-tools channel",
        "transcript": [{"role": "user", "content": "stale"}],   # must be ignored
        "pending": [{"role": "user", "content": "recover me"}],
    })
    st = chat_state.load_state(p)
    assert st["summary"] == "the CEO runs an AI-tools channel"
    assert st["transcript"] == []                      # never replayed
    assert st["pending"] == [{"role": "user", "content": "recover me"}]


def test_save_summary_omits_pending_on_clean_save(tmp_path):
    p = tmp_path / "state.json"
    chat_state.save_summary(p, "clean")
    data = json.loads(p.read_text())
    assert data["summary"] == "clean" and "pending" not in data
