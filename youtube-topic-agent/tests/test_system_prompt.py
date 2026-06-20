"""Offline proof that the chat persona is the soul.md bundle, not SKILL.

Run (from the project folder):  python tests/test_system_prompt.py

Scout in conversation must sound like a person — built from his SOUL (identity),
STYLE (voice), and examples/ (calibration) — and must NOT inherit SKILL.md's
research output contract (the formula/JSON/output rules), which makes him terse
and robotic in chat.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import chat

HERE = pathlib.Path(__file__).resolve().parent.parent
SKILL = (HERE / "SKILL.md").read_text()


def main():
    sysp = chat.build_system_prompt()

    # Persona present.
    assert "Viral Scout" in sysp, "chat prompt must carry Scout's identity"
    assert "scout_research" in sysp, "chat prompt must tell him about the research tool"

    # The full soul.md bundle must be present: SOUL + STYLE + examples.
    assert "Prediction Engine" in sysp, "SOUL.md identity must be loaded"
    assert "HOW YOU TALK" in sysp, "STYLE.md voice section must be loaded into chat"
    assert "Says who?" in sysp, "a STYLE signature phrase must reach the persona"
    assert "VOICE CALIBRATION" in sysp, "examples/ calibration must be loaded"
    assert "Good Outputs" in sysp and "Bad Outputs" in sysp, \
        "both good- and bad-output calibration files must be loaded"
    print("  PASS: soul.md bundle present (SOUL + STYLE + good/bad examples)")

    # SKILL's research output contract must be ABSENT.
    assert "outlier_ratio" not in sysp, \
        "SKILL's scoring formula must not leak into the chat persona"
    assert "Viral Topic Research Method" not in sysp, \
        "SKILL.md's method header must not be in the chat persona"
    assert "Return ONLY a JSON" not in sysp, \
        "the JSON output contract must not be in the chat persona"
    # Sanity: SKILL really does contain the thing we assert is absent.
    assert "outlier_ratio" in SKILL, "test anchor: SKILL should contain outlier_ratio"

    print("  PASS: persona present (Viral Scout + tool); SKILL contract absent")

    # Memory self-description must be ACCURATE: a distilled summary across
    # sessions, NOT a "I start fresh every time" claim.
    low = sysp.lower()
    assert "distilled summary" in low, \
        "persona must describe its memory as a distilled summary across sessions"
    assert "not the word-for-word" in low, \
        "persona must clarify it does NOT keep the word-for-word history"
    assert "start fresh" in low and "do not start fresh" in low, \
        "persona must explicitly stop Scout from claiming he starts fresh"
    print("  PASS: memory self-description is accurate (summary, not fresh-start)")

    print("\n✅ PASS — chat system prompt is persona-from-SOUL, free of SKILL's "
          "output contract, with an accurate memory self-description.")


if __name__ == "__main__":
    main()
