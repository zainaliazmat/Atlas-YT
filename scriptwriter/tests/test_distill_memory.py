"""Offline proof for the summary-only memory model — NO network, NO API keys.

Run:  python tests/test_distill_memory.py   (or: pytest tests/test_distill_memory.py)

The distiller (the only thing that would hit an LLM) is MOCKED throughout, so we
assert the PLUMBING only:
  - distill runs on /exit, /new, /summary, and SIGINT; the summary is saved and the
    in-RAM transcript is cleared.
  - merge: the distiller receives (existing summary + the session's turns) and its
    result is what gets saved; the transcript is cleared.
  - failure fallback: distiller raises -> raw transcript parked under "pending" (not
    lost) and the program still proceeds; the next launch folds "pending" in and
    clears it.
  - launch loads ONLY the summary: a fresh session carries the summary text and NOT
    old raw turns.

HONEST NOTE: whether the summary actually keeps signal and drops junk is a MANUAL
check (real chat with junk + craft notes -> /exit -> relaunch -> "what do you know
about my channel"). Only the plumbing is unit-tested here.
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import chat        # noqa: E402
import chat_state  # noqa: E402


def _state(summary="", transcript=None, pending=None):
    return {"summary": summary, "transcript": transcript or [], "pending": pending}


def _good_distiller(calls):
    def distill(summary, transcript):
        calls.append((summary, list(transcript)))
        return "DISTILLED: " + summary
    return distill


def _boom_distiller(summary, transcript):
    raise RuntimeError("LLM down")


# ----------------------------------------------------------------------
# /exit, /new, /summary, SIGINT all distill + save + clear transcript
# ----------------------------------------------------------------------
def test_exit_distills_and_clears(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        sp = pathlib.Path(d) / "chat_state.json"
        monkeypatch.setattr(chat, "STATE_PATH", sp)
        calls = []
        state = _state("known facts", [{"role": "user", "content": "hi"}])
        keep = chat.handle_command(state, "sys", None, _good_distiller(calls), "/exit")
        assert keep is False
        assert calls and calls[0][0] == "known facts"          # got existing summary
        assert state["transcript"] == []                        # cleared
        assert json.loads(sp.read_text())["summary"].startswith("DISTILLED")


def test_new_distills_keeps_summary_clears_thread(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(chat, "STATE_PATH", pathlib.Path(d) / "chat_state.json")
        calls = []
        state = _state("s", [{"role": "user", "content": "a"}])
        keep = chat.handle_command(state, "sys", None, _good_distiller(calls), "/new")
        assert keep is True
        assert state["transcript"] == []
        assert state["summary"].startswith("DISTILLED")


def test_summary_command_distills_and_shows(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(chat, "STATE_PATH", pathlib.Path(d) / "chat_state.json")
        calls = []
        state = _state("s", [{"role": "user", "content": "a"}])
        chat.handle_command(state, "sys", None, _good_distiller(calls), "/summary")
        assert calls, "distill should run on /summary"
        assert state["transcript"] == []
        assert state["summary"].startswith("DISTILLED")


def test_sigint_distills_and_saves(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        sp = pathlib.Path(d) / "chat_state.json"
        monkeypatch.setattr(chat, "STATE_PATH", sp)
        calls = []
        state = _state("s", [{"role": "user", "content": "a"}])
        chat._SESSION.update(state=state, distiller=_good_distiller(calls),
                             interrupting=False)
        try:
            chat._sigint_handler(2, None)
        except SystemExit:
            pass
        assert calls, "SIGINT should distill"
        assert state["transcript"] == []
        assert json.loads(sp.read_text())["summary"].startswith("DISTILLED")


# ----------------------------------------------------------------------
# Failure fallback -> "pending" (no data loss) + next-launch fold-in
# ----------------------------------------------------------------------
def test_distill_failure_parks_pending(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        sp = pathlib.Path(d) / "chat_state.json"
        monkeypatch.setattr(chat, "STATE_PATH", sp)
        turns = [{"role": "user", "content": "important"}]
        state = _state("orig", list(turns))
        ok = chat.distill_and_save(state, _boom_distiller)
        assert ok is False
        on_disk = json.loads(sp.read_text())
        assert on_disk["summary"] == "orig"               # summary untouched
        assert on_disk["pending"] == turns                # raw chat parked, not lost
        assert state["transcript"] == turns               # in-RAM transcript intact


def test_recover_pending_folds_in_next_launch(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        sp = pathlib.Path(d) / "chat_state.json"
        monkeypatch.setattr(chat, "STATE_PATH", sp)
        chat_state.save_summary(sp, "orig",
                                pending=[{"role": "user", "content": "unsaved"}])
        state = chat_state.load_state(sp)
        assert state["pending"], "precondition: pending present"
        calls = []
        chat._recover_pending(state, _good_distiller(calls))
        assert calls, "recovery should distill the pending turns"
        assert state["pending"] is None
        assert state["summary"].startswith("DISTILLED")
        assert "pending" not in json.loads(sp.read_text())


def test_launch_loads_only_summary_not_transcript():
    with tempfile.TemporaryDirectory() as d:
        sp = pathlib.Path(d) / "chat_state.json"
        chat_state.atomic_write_json(sp, {
            "summary": "durable stuff",
            "transcript": [{"role": "user", "content": "OLD RAW TURN"}],
        })
        state = chat_state.load_state(sp)
        assert state["summary"] == "durable stuff"
        assert state["transcript"] == [], "raw transcript must NOT be replayed"


# ----------------------------------------------------------------------
# standalone runner
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import types

    class _MP:
        def __init__(self): self._undo = []
        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, val in reversed(self._undo):
                setattr(obj, name, val)
            self._undo.clear()

    passed = 0
    for fn_name, fn in sorted(globals().items()):
        if not (fn_name.startswith("test_") and isinstance(fn, types.FunctionType)):
            continue
        mp = _MP()
        try:
            if "monkeypatch" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                fn(mp)
            else:
                fn()
            print(f"  ok  {fn_name}")
            passed += 1
        finally:
            mp.undo()
    print(f"\n{passed} tests passed.")
