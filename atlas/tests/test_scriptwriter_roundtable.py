"""Marlow's adapter turns the Creative Roundtable ON for the live pipeline.

The engine seam defaults `use_roundtable=False` (keeps the pure function fast +
deterministic for unit tests); the LIVE path (run_write) enables it by default and
hands the engine the project dir so roundtable_log.json lands beside the script. A
kill switch (MARLOW_ROUNDTABLE=0) turns it back off for speed runs.
"""
import pathlib

import chat_state
from adapters import scriptwriter


def _capturing_engine(captured):
    class FakeEngine:
        def write_script(self, brief, *, treatment=None, narrative_intent=None,
                         motion_mood_board=None, use_roundtable=False, project_dir=None):
            captured["use_roundtable"] = use_roundtable
            captured["project_dir"] = project_dir
            return {"scenes": []}
    return FakeEngine()


def _seed(pdir):
    pdir.mkdir()
    chat_state.atomic_write_json(pdir / "research_brief.json", {"topic": "T", "sources": []})
    chat_state.atomic_write_json(pdir / "project.json", {})


def test_run_write_enables_roundtable_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MARLOW_ROUNDTABLE", raising=False)
    pdir = tmp_path / "vid"
    _seed(pdir)
    captured = {}
    monkeypatch.setattr(scriptwriter, "_script_engine", lambda: _capturing_engine(captured))

    scriptwriter.run_write(pdir)
    assert captured["use_roundtable"] is True
    assert captured["project_dir"] == pdir          # log lands beside script.json


def test_kill_switch_disables_roundtable(tmp_path, monkeypatch):
    monkeypatch.setenv("MARLOW_ROUNDTABLE", "0")
    pdir = tmp_path / "vid"
    _seed(pdir)
    captured = {}
    monkeypatch.setattr(scriptwriter, "_script_engine", lambda: _capturing_engine(captured))

    scriptwriter.run_write(pdir)
    assert captured["use_roundtable"] is False
