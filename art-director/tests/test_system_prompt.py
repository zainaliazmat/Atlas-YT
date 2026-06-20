"""Offline proof that the CHAT persona prompt is persona-only — NO network.

Run:  python tests/test_system_prompt.py   (or: pytest tests/test_system_prompt.py)

The chat system prompt must be built from SOUL (identity) + STYLE (voice) + examples
ONLY. It must NOT carry SKILL.md's art-direction method / output contract — otherwise
Iris talks like a spec generator instead of a person. We assert both directions, plus
the strict marker parser the provider-agnostic fallback relies on.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import chat  # noqa: E402


def test_prompt_contains_the_persona():
    prompt = chat.build_system_prompt().lower()
    assert "art director" in prompt                  # a distinctive SOUL phrase
    assert "restraint" in prompt                      # her core stance
    assert "müller-brockmann" in prompt              # her named reverence
    assert "live conversation" in prompt             # chat guidance present
    assert "distilled summary" in prompt             # accurate memory self-description
    assert "#ffd000" in prompt                        # the one rule


def test_prompt_excludes_the_skill_method_and_output_contract():
    prompt = chat.build_system_prompt()
    # SKILL.md (the method + output contracts) must NOT leak into the chat persona.
    # These phrases are unique to SKILL.md.
    for banned in ("Your style output contract", "Your storyboard output contract",
                   "The hard invariants (enforced in code", "use ONLY these names",
                   "The three axes (never let one token span two)"):
        assert banned not in prompt, banned


def test_marker_parser_is_strict_for_both_jobs():
    # a clean, single, last-line marker triggers, tagged by job
    assert chat.parse_iris_request("sure\nIRIS_STYLE: ./proj") == ("style", "./proj")
    assert chat.parse_iris_request("ok\nIRIS_BOARD: ./proj") == ("board", "./proj")
    # a mid-text mention must NOT trigger
    assert chat.parse_iris_request("I could IRIS_STYLE: x but let's talk first") is None
    # two of the same marker -> ambiguous -> no trigger
    assert chat.parse_iris_request("IRIS_STYLE: a\nIRIS_STYLE: b") is None
    # both markers at once -> ambiguous -> no trigger
    assert chat.parse_iris_request("IRIS_STYLE: a\nIRIS_BOARD: b") is None
    # empty path -> no trigger
    assert chat.parse_iris_request("IRIS_STYLE:   ") is None
    # not the last non-empty line -> no trigger
    assert chat.parse_iris_request("IRIS_STYLE: a\nand then chatter") is None


def test_marker_is_stripped_from_display():
    text = "Here's the look.\nIRIS_STYLE: ./proj"
    assert chat.strip_iris_request(text) == "Here's the look."
    text2 = "Boarding it now.\nIRIS_BOARD: ./proj"
    assert chat.strip_iris_request(text2) == "Boarding it now."


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
