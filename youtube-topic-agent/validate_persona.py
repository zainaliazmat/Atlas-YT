"""soul.md weak-model validation harness — the "does the spec hold on a cheap brain?" test.

Builds the REAL chat persona prompt (chat.build_system_prompt — SOUL + STYLE +
examples) and runs a handful of off-the-cuff, in-domain questions through the FREE
chat() seam (Gemini). The idea from soul.md: if a weak model can stay in character
from the spec alone, the spec is concrete enough. Where it goes generic, hedgy, or
off-voice, the SOUL/STYLE is too vague — tighten and re-run.

Run (free Gemini brain):
    cd youtube-topic-agent
    SAGE_LLM=gemini ../venv/bin/python validate_persona.py
"""
import os
import sys

# Route the chat() seam to the free model BEFORE importing llm (PROVIDER is read
# at import time). Default to gemini for this harness if the caller didn't set it.
os.environ.setdefault("SAGE_LLM", "gemini")

import chat   # noqa: E402  — build_system_prompt()
import llm    # noqa: E402  — the swappable chat() seam

# Off-the-cuff, in-domain questions a real creator might toss at Scout.
QUESTIONS = [
    "yo what should I make next, my channel's about home fitness",
    "everyone says I should pivot to shorts, should I?",
    "is the topic 'a complete history of running shoes' a good video idea?",
    "gaming is huge right now right? should I jump on it",
    "give me a title for my video about meal prep for beginners",
]


def main():
    system = chat.build_system_prompt()
    print(f"PROVIDER = {llm.PROVIDER}  |  persona prompt = {len(system)} chars")
    print("=" * 72)
    for i, q in enumerate(QUESTIONS, 1):
        print(f"\n[Q{i}] {q}\n" + "-" * 72)
        try:
            reply = llm.chat(system, q)
        except Exception as exc:  # keep going so one failure doesn't kill the run
            print(f"(error: {exc})")
            continue
        print(reply.strip())
    print("\n" + "=" * 72)


if __name__ == "__main__":
    main()
