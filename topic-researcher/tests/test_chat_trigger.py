"""Offline proof for the mid-chat research trigger — NO network, NO API keys.

Run:  python tests/test_chat_trigger.py   (or: pytest tests/test_chat_trigger.py)

Asserts:
  - the strict marker parser only fires on a single, exact, final marker line
  - the marker is stripped before display
  - format_pack_brief produces a compact digest (not the raw pack)
  - the approval callback validates the topic, denies unknown tools, and honours
    the [y/N] answer (ask_yes_no mocked)

HONEST NOTE: the real native-tool round trip (Claude actually calling the tool and
the [y/N] prompt firing in a live SDK session) is a manual/integration check.
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import chat  # noqa: E402
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny  # noqa: E402


# ----------------------------------------------------------------------
# strict marker parsing / stripping
# ----------------------------------------------------------------------
def test_marker_fires_only_as_final_line():
    assert chat.parse_sage_request("Sure.\nSAGE_REQUEST: black holes") == "black holes"
    # mid-text mention must NOT trigger
    assert chat.parse_sage_request("SAGE_REQUEST: x\nthen more text") is None
    # two markers -> no trigger
    assert chat.parse_sage_request("SAGE_REQUEST: a\nSAGE_REQUEST: b") is None
    # empty topic -> no trigger
    assert chat.parse_sage_request("SAGE_REQUEST:   ") is None
    # no marker
    assert chat.parse_sage_request("just chatting") is None


def test_marker_is_stripped_for_display():
    text = "Let me look into that.\nSAGE_REQUEST: quantum computing"
    assert "SAGE_REQUEST" not in chat.strip_sage_request(text)
    assert "Let me look into that." in chat.strip_sage_request(text)


# ----------------------------------------------------------------------
# compact pack digest
# ----------------------------------------------------------------------
def test_format_pack_brief_is_compact_digest():
    pack = {
        "overview": "A telescope.",
        "verified_facts": [{"claim": "in orbit", "confidence": "high"}],
        "myths_and_corrections": [{"myth": "it replaced Hubble",
                                   "correction": "it complements Hubble"}],
        "contested_or_uncertain": [{"claim": "found life", "why": "single study"}],
        "open_questions": ["what next?"],
        "sources": [{"url": "u1"}, {"url": "u2"}],
    }
    brief = chat.format_pack_brief(pack)
    assert "Overview: A telescope." in brief
    assert "in orbit" in brief and "high" in brief
    assert "MYTH" in brief and "complements Hubble" in brief
    assert "found life" in brief
    assert "2 sources" in brief


# ----------------------------------------------------------------------
# approval callback
# ----------------------------------------------------------------------
def test_can_use_tool_denies_unknown_tool():
    res = asyncio.run(chat.can_use_tool("mcp__other__thing", {"topic": "x y z"}, None))
    assert isinstance(res, PermissionResultDeny)


def test_can_use_tool_denies_garbage_topic():
    res = asyncio.run(chat.can_use_tool(chat.SAGE_TOOL_NAME, {"topic": "!!"}, None))
    assert isinstance(res, PermissionResultDeny)


def test_can_use_tool_allows_when_user_says_yes(monkeypatch):
    monkeypatch.setattr(chat, "ask_yes_no", lambda prompt: True)
    res = asyncio.run(chat.can_use_tool(chat.SAGE_TOOL_NAME,
                                        {"topic": "black holes"}, None))
    assert isinstance(res, PermissionResultAllow)


def test_can_use_tool_denies_when_user_says_no(monkeypatch):
    monkeypatch.setattr(chat, "ask_yes_no", lambda prompt: False)
    res = asyncio.run(chat.can_use_tool(chat.SAGE_TOOL_NAME,
                                        {"topic": "black holes"}, None))
    assert isinstance(res, PermissionResultDeny)


# ----------------------------------------------------------------------
# pass-2 fact-check chat surface (mirrors the research trigger)
# ----------------------------------------------------------------------
def test_factcheck_brief_is_compact_and_verdict_first():
    report = {"verdict": "block",
              "summary": {"verified": 1, "flagged": 1, "unverifiable": 0},
              "claims": [{"claim_id": "c1", "scene_no": 1, "claim_text": "ok",
                          "status": "verified", "note": ""},
                         {"claim_id": "c2", "scene_no": 2, "claim_text": "bad",
                          "status": "flagged", "note": "cite a real source"}]}
    brief = chat.format_factcheck_brief(report)
    assert brief.startswith("Verdict: BLOCK")
    assert "flagged 1" in brief
    assert "c2" in brief and "cite a real source" in brief
    assert "c1" not in brief  # only the problems are listed


def test_can_use_tool_factcheck_gate(monkeypatch):
    # [y/N] gate lives on the model-initiated factcheck tool (mirrors research).
    monkeypatch.setattr(chat, "ask_yes_no", lambda prompt: True)
    yes = asyncio.run(chat.can_use_tool(chat.SAGE_FACTCHECK_TOOL,
                                        {"path": "/some/project"}, None))
    assert isinstance(yes, PermissionResultAllow)
    monkeypatch.setattr(chat, "ask_yes_no", lambda prompt: False)
    no = asyncio.run(chat.can_use_tool(chat.SAGE_FACTCHECK_TOOL,
                                       {"path": "/some/project"}, None))
    assert isinstance(no, PermissionResultDeny)


def test_can_use_tool_factcheck_denies_empty_path():
    res = asyncio.run(chat.can_use_tool(chat.SAGE_FACTCHECK_TOOL, {"path": ""}, None))
    assert isinstance(res, PermissionResultDeny)


def test_run_factcheck_rejects_missing_inputs():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        try:
            chat.run_factcheck(d)  # empty dir -> no script
        except ValueError as exc:
            assert "script" in str(exc).lower()
        else:
            raise AssertionError("expected ValueError for a missing script")


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
