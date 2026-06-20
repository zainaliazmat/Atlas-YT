"""Offline proof that the CHAT persona prompt is persona-only — NO network.

Run:  python tests/test_system_prompt.py   (or: pytest tests/test_system_prompt.py)

The chat system prompt must be built from SOUL (identity/voice) ONLY. It must NOT
carry SKILL.md's research output contract / pack schema — otherwise Sage talks like
a report generator instead of a person. We assert both directions.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import chat  # noqa: E402


def test_prompt_contains_the_persona():
    prompt = chat.build_system_prompt()
    # a distinctive SOUL phrase
    assert "investigative researcher" in prompt.lower()
    # live-conversation guidance + accurate memory self-description
    assert "live conversation" in prompt.lower()
    assert "distilled summary" in prompt.lower()


def test_prompt_excludes_the_research_output_contract():
    prompt = chat.build_system_prompt()
    # none of the pack schema keys / SKILL contract language should leak in
    for banned in ("verified_facts", "myths_and_corrections", "contested_or_uncertain",
                   "output contract", "Return ONLY a JSON"):
        assert banned not in prompt, f"persona prompt leaked SKILL contract: {banned!r}"


def test_prompt_does_not_embed_skill_file():
    # SKILL.md text must not be concatenated into the chat identity.
    skill = (pathlib.Path(chat.HERE) / "SKILL.md").read_text()
    # a line unique to SKILL.md
    assert "The Fact-Validation Method" in skill          # sanity: we read the right file
    assert "The Fact-Validation Method" not in chat.build_system_prompt()


def test_prompt_loads_the_full_soul_bundle():
    # The soul.md bundle is SOUL + STYLE + examples — all three must reach chat.
    prompt = chat.build_system_prompt()
    assert "Prediction Engine" in prompt, "SOUL.md identity must be loaded"
    assert "HOW YOU TALK" in prompt, "STYLE.md voice section must be loaded into chat"
    assert "couldn't verify" in prompt, "a STYLE signature phrase must reach the persona"
    assert "VOICE CALIBRATION" in prompt, "examples/ calibration must be loaded"
    assert "Good Outputs" in prompt and "Bad Outputs" in prompt, \
        "both good- and bad-output calibration files must be loaded"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
            passed += 1
    print(f"\n{passed} tests passed.")
