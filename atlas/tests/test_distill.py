"""Summary-only memory: distill saves the summary + clears the transcript; a failed
distill parks the raw turns under 'pending' (no data loss); empty is a no-op.

Uses a MOCK distiller (no LLM/network). Covers the boundary helper that /exit, /new,
/summary and SIGINT all funnel through.
"""
import json

import chat


def test_distill_saves_summary_and_clears_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "STATE_PATH", tmp_path / "cs.json")
    state = {"summary": "old", "pending": None,
             "transcript": [{"role": "user", "content": "we picked topic A"}]}
    distiller = chat.make_distiller(lambda system, user: "NEW SUMMARY")

    ok = chat.distill_and_save(state, distiller)

    assert ok is True
    assert state["summary"] == "NEW SUMMARY"
    assert state["transcript"] == []                    # transcript cleared
    data = json.loads((tmp_path / "cs.json").read_text())
    assert data["summary"] == "NEW SUMMARY"
    assert "pending" not in data                        # clean save, no stale pending


def test_failed_distill_parks_pending_and_keeps_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "STATE_PATH", tmp_path / "cs.json")
    state = {"summary": "old", "pending": None,
             "transcript": [{"role": "user", "content": "keep me safe"}]}

    def boom(system, user):
        raise RuntimeError("distiller down")

    ok = chat.distill_and_save(state, chat.make_distiller(boom), timeout=5)

    assert ok is False
    assert state["summary"] == "old"                    # unchanged, not lost
    data = json.loads((tmp_path / "cs.json").read_text())
    assert data["pending"] == [{"role": "user", "content": "keep me safe"}]


def test_pending_backlog_is_folded_in_on_next_distill(tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "STATE_PATH", tmp_path / "cs.json")
    # A prior session left raw turns parked under pending; a new turn arrives.
    state = {"summary": "base", "pending": [{"role": "user", "content": "earlier"}],
             "transcript": [{"role": "user", "content": "now"}]}
    seen = {}

    def distiller(summary, transcript):
        seen["n"] = len(transcript)
        return "merged"

    ok = chat.distill_and_save(state, distiller)
    assert ok is True
    assert seen["n"] == 2                               # pending + live both folded
    assert state["pending"] is None and state["transcript"] == []


def test_empty_session_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "STATE_PATH", tmp_path / "cs.json")
    state = {"summary": "keep", "pending": None, "transcript": []}
    called = []
    distiller = chat.make_distiller(lambda s, u: called.append(1) or "x")

    ok = chat.distill_and_save(state, distiller)
    assert ok is True
    assert state["summary"] == "keep" and not called    # distiller never invoked
