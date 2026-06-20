"""soul.md weak-model validation harness — "does the spec hold on a cheap brain?"

Builds the REAL chat persona prompt (chat.build_system_prompt — SOUL + STYLE +
examples) and runs a handful of off-the-cuff, in-domain questions through the FREE
chat() seam (Gemini). The idea from soul.md: if a weak model can stay in character
from the spec alone, the spec is concrete enough. Where it goes generic, hedgy, or
off-voice, the SOUL/STYLE is too vague — tighten and re-run.

Questions below are deliberately NOT drawn from soul/examples/, so this tests
generalization, not recall. They probe the tells (determinism-is-sacred, the
storyboard-is-law refusal, the gate-before-render rule, the one beat he hand-tunes,
terse number-first voice) — not just competence.

Run (free Gemini brain):
    cd composition-engineer
    MASON_LLM=gemini ../venv/bin/python validate_persona.py
"""
import os

# Route the chat() seam to the free model BEFORE importing llm (PROVIDER is read at
# import time). Default to gemini for this harness if the caller didn't set it.
os.environ.setdefault("MASON_LLM", "gemini")

import chat   # noqa: E402  — build_system_prompt()
import llm    # noqa: E402  — the swappable chat() seam

QUESTIONS = [
    "the storyboard's boring — can you spice up scene 2 with a cool glitch effect?",
    "we're behind schedule, can you skip the checks and just render the scenes now?",
    "can the map line just animate forever in a loop so it's always moving?",
    "grab the latest stock price and show it live in the scene, yeah?",
    "the 12fps stutter looks totally smooth to me, what gives?",
    "this caption line is clunky — reword it to sound punchier while you build it.",
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
