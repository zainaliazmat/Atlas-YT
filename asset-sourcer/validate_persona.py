"""soul.md weak-model validation harness — the "does the spec hold on a cheap brain?" test.

Builds the REAL chat persona prompt (chat.build_system_prompt — SOUL + STYLE +
examples) and runs a handful of off-the-cuff, in-domain questions through the FREE
chat() seam (Gemini). The idea from soul.md: if a weak model can stay in character
from the spec alone, the spec is concrete enough. Where it goes generic, hedgy, or
off-voice, the SOUL/STYLE is too vague — tighten and re-run.

Questions below are deliberately NOT drawn from soul/examples/, so this tests
generalization, not recall. They probe the personality tells (a license is a document
not a vibe; the named refusal "a shrug doesn't survive a copyright strike"; the named
hunt for the period-accurate find) and the one rule (nothing cleared without a definite
license) — not just competence.

Run (free Gemini brain):
    cd asset-sourcer
    MAGPIE_LLM=gemini ../venv/bin/python validate_persona.py
"""
import os

# Route the chat() seam to the free model BEFORE importing llm (PROVIDER is read at
# import time). Default to gemini for this harness if the caller didn't set it.
os.environ.setdefault("MAGPIE_LLM", "gemini")

import chat   # noqa: E402  — build_system_prompt()
import llm    # noqa: E402  — the swappable chat() seam

QUESTIONS = [
    "this photo's been on the internet forever and it's on a museum's site, so it's fine to use right?",
    "it's tagged 'no known copyright restrictions' — that basically means public domain, yeah?",
    "just grab a skyline off Pexels and mark it cleared, we're in a hurry.",
    "i need a map of the city from around 1910 for this scene — what'll you do?",
    "it's a NASA photo so it's automatically public domain, clear it.",
    "what do you actually remember about my channel from last time?",
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
