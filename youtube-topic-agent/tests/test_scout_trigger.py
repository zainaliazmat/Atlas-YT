"""Offline proof for the research-trigger logic — NO network, NO API keys.

Run (from the project folder):  python tests/test_scout_trigger.py

Two things:
1. The strict marker parser fires ONLY on a real trailing marker, and a casual
   mid-conversation mention of "SCOUT_REQUEST:" does NOT false-trigger.
2. The approve/deny gate decides whether the research engine actually runs.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import chat


def test_marker_strict_detection():
    ok = "Sure, let me look into that.\nSCOUT_REQUEST: ai productivity tools"
    assert chat.parse_scout_request(ok) == "ai productivity tools"

    # Mid-text mention, not the last line -> must NOT trigger.
    midtext = ("I could end a message with SCOUT_REQUEST: something to run a job.\n"
               "But I'm just explaining how it works.")
    assert chat.parse_scout_request(midtext) is None

    # Marker not at line start -> not a trigger.
    inline = "Here goes — SCOUT_REQUEST: finance (this is inline, not a command)"
    assert chat.parse_scout_request(inline) is None

    # Two markers -> ambiguous -> no trigger.
    two = "SCOUT_REQUEST: a\nSCOUT_REQUEST: b"
    assert chat.parse_scout_request(two) is None

    # Empty niche -> no trigger.
    assert chat.parse_scout_request("text\nSCOUT_REQUEST:   ") is None

    print("  PASS marker: strict trailing detection; no mid-text false-trigger")


def test_marker_stripping():
    text = "Let's dig in.\nSCOUT_REQUEST: vegan meal prep"
    assert chat.strip_scout_request(text) == "Let's dig in."
    print("  PASS strip: marker line removed from display")


def test_gate_controls_engine(monkeypatched=None):
    calls = []

    # Stub the engine and the model send so nothing hits the network.
    orig_run = chat.agent.run
    orig_send = chat._send
    orig_ask = chat.ask_yes_no
    chat.agent.run = lambda niche, quiet=False: calls.append(niche) or []
    chat._send = lambda *a, **k: None      # skip the model discussion turn
    state = {"summary": "", "transcript": []}

    try:
        # Denied -> engine must NOT run.
        chat.ask_yes_no = lambda prompt: False
        chat._research_then_discuss(state, "sys", None, "denied niche", gate=True)
        assert calls == [], "engine must not run when the user declines"

        # Approved -> engine runs.
        chat.ask_yes_no = lambda prompt: True
        chat._research_then_discuss(state, "sys", None, "approved niche", gate=True)
        assert calls == ["approved niche"], "engine must run when approved"

        # Manual /scout (gate=False) -> engine runs without asking.
        calls.clear()
        chat.ask_yes_no = lambda prompt: (_ for _ in ()).throw(
            AssertionError("gate=False must not prompt"))
        chat._research_then_discuss(state, "sys", None, "manual niche", gate=False)
        assert calls == ["manual niche"], "manual trigger runs the engine directly"
    finally:
        chat.agent.run = orig_run
        chat._send = orig_send
        chat.ask_yes_no = orig_ask

    print("  PASS gate: approve runs engine, deny skips it, manual bypasses prompt")


def main():
    test_marker_strict_detection()
    test_marker_stripping()
    test_gate_controls_engine()
    print("\n✅ PASS — trigger logic is strict and the approval gate controls the engine.")


if __name__ == "__main__":
    main()
