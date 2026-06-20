"""Offline proof that the CHAT persona prompt is persona-only — NO network.

Run:  python tests/test_system_prompt.py   (or: pytest tests/test_system_prompt.py)

The chat system prompt must be built from SOUL (identity/voice) ONLY. It must NOT
carry SKILL.md's scripting method / output contract — otherwise Marlow talks like a
script generator instead of a person. We assert both directions, plus the strict
marker parser the provider-agnostic fallback relies on.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import chat  # noqa: E402


def test_prompt_contains_the_persona():
    prompt = chat.build_system_prompt().lower()
    assert "scriptwriter" in prompt                 # a distinctive SOUL phrase
    assert "through-line" in prompt                 # his core obsession
    assert "live conversation" in prompt            # chat guidance present
    assert "distilled summary" in prompt            # accurate memory self-description


def test_prompt_excludes_the_scripting_output_contract():
    prompt = chat.build_system_prompt()
    # SKILL.md (the method + output contract) must NOT leak into the chat persona.
    # These phrases are unique to SKILL.md — the persona may *mention* field names as
    # things NOT to dump in chat, but the contract/method text itself never appears.
    for banned in ("Your output contract", "Return ONLY the JSON object",
                   "Tag every factual line", "Bound the runtime, then self-check"):
        assert banned not in prompt, banned


def test_marker_parser_is_strict():
    # a clean, single, last-line marker triggers
    assert chat.parse_marlow_request("sure, let me write it\nMARLOW_REQUEST: ./proj") == "./proj"
    # a mid-text mention must NOT trigger
    assert chat.parse_marlow_request("I could MARLOW_REQUEST: x but let's chat first") is None
    # two markers -> ambiguous -> no trigger
    assert chat.parse_marlow_request("MARLOW_REQUEST: a\nMARLOW_REQUEST: b") is None
    # empty path -> no trigger
    assert chat.parse_marlow_request("MARLOW_REQUEST:   ") is None
    # not the last non-empty line -> no trigger
    assert chat.parse_marlow_request("MARLOW_REQUEST: a\nand then some chatter") is None


def test_marker_is_stripped_from_display():
    text = "Here's the plan.\nMARLOW_REQUEST: ./proj"
    assert chat.strip_marlow_request(text) == "Here's the plan."


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
