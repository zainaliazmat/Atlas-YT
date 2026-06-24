"""Marlow folds Atlas's revision hint into the brief on a fix re-run."""
import json
import pathlib
import chat_state
from adapters import scriptwriter


def test_run_write_folds_revision_hint_into_brief(tmp_path, monkeypatch):
    pdir = tmp_path / "vid"
    pdir.mkdir()
    chat_state.atomic_write_json(pdir / "research_brief.json", {"topic": "T", "sources": []})
    chat_state.atomic_write_json(pdir / "project.json",
                                 {"revision": {"stage": "script", "hint": "drop s5c2"}})

    captured = {}
    class FakeEngine:
        def write_script(self, brief):
            captured["brief"] = brief
            return {"scenes": []}
    monkeypatch.setattr(scriptwriter, "_script_engine", lambda: FakeEngine())

    scriptwriter.run_write(pdir)
    assert captured["brief"].get("revision_hint") == "drop s5c2"


def test_run_write_omits_hint_when_absent(tmp_path, monkeypatch):
    pdir = tmp_path / "vid"
    pdir.mkdir()
    chat_state.atomic_write_json(pdir / "research_brief.json", {"topic": "T"})
    chat_state.atomic_write_json(pdir / "project.json", {})

    captured = {}
    class FakeEngine:
        def write_script(self, brief):
            captured["brief"] = brief
            return {"scenes": []}
    monkeypatch.setattr(scriptwriter, "_script_engine", lambda: FakeEngine())

    scriptwriter.run_write(pdir)
    assert "revision_hint" not in captured["brief"]
