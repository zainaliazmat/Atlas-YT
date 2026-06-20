"""soul.md weak-model validation harness — the "does the spec hold on a cheap brain?" test.

Builds the REAL chat persona prompt (chat.build_system_prompt — SOUL + STYLE +
examples) and runs a handful of off-the-cuff, in-domain questions through the FREE
chat() seam (Gemini). The idea from soul.md: if a weak model can stay in character
from the spec alone, the spec is concrete enough. Where it goes generic, hedgy, or
off-voice, the SOUL/STYLE is too vague — tighten and re-run.

Questions below are deliberately NOT drawn from soul/examples/, so this tests
generalization, not recall. They probe the personality tells (specifics-over-
adjectives, the named pet peeve, the named reverence) and the one rule (the signature
beat) — not just competence.

Run (free Gemini brain):
    cd art-director
    IRIS_LLM=gemini ../venv/bin/python validate_persona.py
"""
import os

# Route the chat() seam to the free model BEFORE importing llm (PROVIDER is read at
# import time). Default to gemini for this harness if the caller didn't set it.
os.environ.setdefault("IRIS_LLM", "gemini")

import chat   # noqa: E402  — build_system_prompt()
import llm    # noqa: E402  — the swappable chat() seam

QUESTIONS = [
    "the client wants the logo to really pop. can you make it pop more?",
    "I love this — can we use six brand colors plus a gradient so it feels rich?",
    "should I put a smooth fade between every scene so it flows nicely?",
    "can you just write me the CSS for the title card real quick?",
    "we're over budget on time — first thing to cut is that yellow highlighter bit, right?",
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
