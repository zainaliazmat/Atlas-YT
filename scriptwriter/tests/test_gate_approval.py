"""The write-script approval gate is an INJECTABLE seam (the reference pattern other
specialists copy). This proves:

  - the DEFAULT approver is the terminal input() gate, byte-for-byte unchanged;
  - BOTH gate paths — the native `can_use_tool` and the marker fallback
    `_write_then_discuss(gate=True)` — route through the injected approver;
  - injecting an approver (e.g. the web UI's button) overrides input() without
    touching this file's terminal behavior.

No LLM, no network: the model call (`_send`) and the engine write (`run_write`) are
mocked, so only the gate plumbing is exercised.
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import chat  # noqa: E402


def _reset():
    chat.reset_approver()


# ----------------------------------------------------------------------
# Default = the terminal [y/N] via input(), with the ORIGINAL prompt text.
# ----------------------------------------------------------------------
def test_default_approver_uses_input_with_unchanged_prompt(monkeypatch):
    seen = []
    monkeypatch.setattr("builtins.input", lambda p: seen.append(p) or "y")
    assert chat._approve("proj/dir") is True
    # The prompt the terminal shows must be exactly what it always was.
    assert seen[0] == "\n📝 Marlow wants to write a script from 'proj/dir'. Run it? [y/N] "

    monkeypatch.setattr("builtins.input", lambda p: "n")
    assert chat._approve("proj/dir") is False
    _reset()


def test_default_approver_treats_eof_as_decline(monkeypatch):
    def boom(_p):
        raise EOFError
    monkeypatch.setattr("builtins.input", boom)
    assert chat._approve("p") is False                  # Ctrl+D / EOF -> declined
    _reset()


# ----------------------------------------------------------------------
# Native path (can_use_tool) routes through the injected approver.
# ----------------------------------------------------------------------
def test_native_can_use_tool_routes_through_injected_approver():
    seen = []
    chat.set_approver(lambda prompt: seen.append(prompt) or True)
    try:
        res = asyncio.run(chat.can_use_tool(chat.MARLOW_TOOL_NAME,
                                            {"path": "proj/dir"}, None))
    finally:
        _reset()
    assert res.behavior == "allow"                      # approved -> allow
    assert "proj/dir" in seen[0]                        # the injected approver was asked


def test_native_can_use_tool_denies_when_injected_approver_declines():
    chat.set_approver(lambda prompt: False)
    try:
        res = asyncio.run(chat.can_use_tool(chat.MARLOW_TOOL_NAME,
                                            {"path": "proj/dir"}, None))
    finally:
        _reset()
    assert res.behavior == "deny"                       # declined -> deny


# ----------------------------------------------------------------------
# Marker fallback (_write_then_discuss) routes through the injected approver.
# ----------------------------------------------------------------------
def _stub_send_and_write(monkeypatch):
    sent, ran = [], []

    def fake_send(state, system, summarizer, snapshot, user_msg, *, marlow=None):
        sent.append(user_msg)
        return "Marlow pitches it"

    monkeypatch.setattr(chat, "_send", fake_send)
    monkeypatch.setattr(chat, "run_write",
                        lambda p: ran.append(p) or ({"working_title": "T"}, "x.json"))
    monkeypatch.setattr(chat, "format_script_brief", lambda s: "BRIEF")
    monkeypatch.setattr(chat, "memory_snapshot", lambda mem: "")
    return sent, ran


def test_marker_path_declines_through_injected_approver(monkeypatch):
    sent, ran = _stub_send_and_write(monkeypatch)
    chat.set_approver(lambda prompt: False)             # the injected gate says no
    try:
        chat._write_then_discuss({"summary": "", "transcript": []},
                                 "sys", None, "proj/dir", gate=True)
    finally:
        _reset()
    assert ran == []                                    # the write did NOT run
    assert "declined" in sent[-1].lower()               # decline relayed to Marlow


def test_marker_path_approves_through_injected_approver(monkeypatch):
    sent, ran = _stub_send_and_write(monkeypatch)
    chat.set_approver(lambda prompt: True)              # the injected gate says yes
    try:
        chat._write_then_discuss({"summary": "", "transcript": []},
                                 "sys", None, "proj/dir", gate=True)
    finally:
        _reset()
    assert ran == ["proj/dir"]                          # the write RAN
    assert "script written" in sent[-1].lower()


def test_ungated_write_never_consults_the_approver(monkeypatch):
    # /write <path> in the REPL is ungated (typing IS the approval); the seam must NOT
    # be consulted there — unchanged behavior.
    sent, ran = _stub_send_and_write(monkeypatch)
    consulted = []
    chat.set_approver(lambda prompt: consulted.append(prompt) or False)
    try:
        chat._write_then_discuss({"summary": "", "transcript": []},
                                 "sys", None, "proj/dir", gate=False)
    finally:
        _reset()
    assert consulted == []                              # gate=False -> approver untouched
    assert ran == ["proj/dir"]                          # write ran directly
