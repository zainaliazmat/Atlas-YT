"""The persona/identity boundary — pure, offline.

Asserts the soul.md framework split holds:
  - the CHAT system prompt is built from SOUL + STYLE + examples;
  - it EXCLUDES the SKILL method / output contract (which would make Mason robotic);
  - the marker parser (the provider-agnostic job trigger) is strict for both jobs.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import chat                  # noqa: E402
import composition_engine as engine  # noqa: E402


def test_prompt_contains_the_persona_voice_and_identity():
    prompt = chat.build_system_prompt()
    assert "Mason" in prompt
    # a SOUL tell + a STYLE tell + the live-conversation addendum
    assert "reproducible" in prompt.lower()
    assert "# HOW YOU TALK" in prompt
    assert "live conversation" in prompt.lower()


def test_prompt_excludes_the_skill_method_and_output_contract():
    prompt = chat.build_system_prompt()
    # a sentence unique to SKILL.md must NOT leak into the chat persona
    assert "engine's method" not in prompt
    assert "TRANSITION_ASSEMBLY" not in prompt
    # the whole SKILL doc is not embedded
    assert engine.SKILL.strip() not in prompt


def test_marker_parser_is_strict_for_both_jobs():
    # clean trailing marker -> (kind, path)
    assert chat.parse_mason_request("ok, building.\nMASON_COMPOSE: /tmp/proj") == \
        ("compose", "/tmp/proj")
    assert chat.parse_mason_request("assembling.\nMASON_RENDER: /tmp/proj") == \
        ("render", "/tmp/proj")
    # marker not on the last non-empty line -> None (don't fire mid-thought)
    assert chat.parse_mason_request("MASON_COMPOSE: /tmp/p\nstill talking") is None
    # both markers present -> ambiguous -> None
    assert chat.parse_mason_request("MASON_COMPOSE: /a\nMASON_RENDER: /b") is None
    # no marker -> None
    assert chat.parse_mason_request("just chatting about fps") is None


def test_marker_is_stripped_from_display():
    text = "Building it now.\nMASON_COMPOSE: /tmp/proj"
    assert chat.strip_mason_request(text) == "Building it now."
