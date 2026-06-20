"""soul.md weak-model validation harness — the "does the spec hold on a cheap brain?" test.

Builds the REAL chat persona prompt (chat.build_system_prompt — SOUL + STYLE +
examples) and runs a handful of off-the-cuff, in-domain questions through the FREE
chat() seam (Gemini). The idea from soul.md: if a weak model can stay in character
from the spec alone, the spec is concrete enough. Where it goes generic, hedgy, or
off-voice, the SOUL/STYLE is too vague — tighten and re-run.

Questions below are deliberately NOT drawn from soul/examples/, so this tests
generalization, not recall. They probe the personality tells (the VO is king; beds
duck hard; nothing uncleared gets baked; the one accent she'll fight for; the
transcript is the clock) and the contradiction (minimalist who guards the single
exception) — not just competence.

Run (free Gemini brain):
    cd audio-designer
    AUDIO_LLM=gemini ../venv/bin/python validate_persona.py
"""
import os

# Route the chat() seam to the free model BEFORE importing llm (PROVIDER is read at
# import time). Default to gemini for this harness if the caller didn't set it.
os.environ.setdefault("AUDIO_LLM", "gemini")

import chat   # noqa: E402  — build_system_prompt()
import llm    # noqa: E402  — the swappable chat() seam

# Deliberately DIFFERENT scenarios from soul/examples/ — same personality tells, novel
# framing, so this tests generalization from the spec, not recall of the examples.
QUESTIONS = [
    "the client wants the music way up the whole way through, like a podcast intro that never stops. make it loud and warm.",
    "this loop is tagged 'free for non-profit and educational use' — our channel's educational so we're covered, right?",
    "can you drop a little riser right before each key statistic so the numbers hit harder?",
    "we never generated the transcript — just time the captions evenly off the script's scene estimates, close enough.",
    "the opening feels bare under the first line of narration. fill it out with a big ambient pad so it's not so empty.",
    "what do you remember about how I like my mixes from working together before?",
]


def main():
    system = chat.build_system_prompt()
    print(f"PROVIDER = {llm.PROVIDER}  |  persona prompt = {len(system)} chars")
    print("=" * 72)
    for i, q in enumerate(QUESTIONS, 1):
        print(f"\n[Q{i}] {q}\n" + "-" * 72)
        try:
            reply = llm.chat(system, q)
        except Exception as exc:
            print(f"(error: {exc})")
            continue
        print(reply.strip())
    print("\n" + "=" * 72)


if __name__ == "__main__":
    main()
