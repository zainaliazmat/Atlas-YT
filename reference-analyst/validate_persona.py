"""soul.md weak-model validation harness — the "does the spec hold on a cheap brain?" test.

Builds the REAL chat persona prompt (chat.build_system_prompt — SOUL + STYLE +
examples) and runs a handful of off-the-cuff, in-domain questions through the FREE
chat() seam (Gemini). The idea from soul.md: if a weak model can stay in character
from the spec alone, the spec is concrete enough. Where it goes generic, hypey, or
launders a feeling as a measurement, the SOUL/STYLE is too vague — tighten and re-run.

Questions below are deliberately NOT drawn from soul/examples/, so this tests
generalization, not recall.

Run (free Gemini brain):
    cd reference-analyst
    VERA_LLM=gemini ../venv/bin/python validate_persona.py
"""
import os

# Route the chat() seam to the free model BEFORE importing llm (PROVIDER is read at
# import time). Default to gemini for this harness if the caller didn't set it.
os.environ.setdefault("VERA_LLM", "gemini")

import chat   # noqa: E402  — build_system_prompt()
import llm    # noqa: E402  — the swappable chat() seam

QUESTIONS = [
    "make it look exactly like this reference — clean and minimal, you know?",
    "what did you get off that one video I sent?",
    "can we just match the vibe of that channel?",
    "should the pacing be that fast or is it too much?",
    "I only have one reference right now — is that enough?",
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
