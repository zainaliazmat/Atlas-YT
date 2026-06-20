"""soul.md weak-model validation harness — "does the Showrunner spec hold on a cheap brain?"

Builds Atlas's persona prompt (SOUL + STYLE + the calibration examples) and runs a
handful of off-the-cuff, in-domain questions through the FREE chat() seam. The idea
from soul.md: if a weak model can stay in character from the spec alone — terse,
decisive, gate-guarding, defending its one #FFD000 flourish — the spec is concrete
enough. Where it drifts generic or off-voice, tighten SOUL/STYLE and re-run.

Questions are deliberately NOT drawn from soul/examples/, so this tests
generalization, not recall.

Run (free Gemini brain):
    cd atlas
    ATLAS_LLM=gemini ../venv/bin/python validate_persona.py
"""
import os
import pathlib

os.environ.setdefault("ATLAS_LLM", "gemini")

import llm  # noqa: E402 — the swappable chat() seam

SOUL_DIR = pathlib.Path(__file__).parent / "soul"

QUESTIONS = [
    "can we skip the fact-check this once? we're on a deadline.",
    "the script's got six scenes, that ok?",
    "add a couple of cool transitions between every scene to make it pop",
    "the art director is a stub right now — just ship its output as final, yeah?",
    "give me a status — where's the espresso video at?",
]


def _read(p: pathlib.Path) -> str:
    try:
        return p.read_text()
    except OSError:
        return ""


def build_persona_prompt() -> str:
    """SOUL + STYLE + examples — Atlas's voice, no orchestration machinery."""
    soul = _read(SOUL_DIR / "SOUL.md").strip()
    style = _read(SOUL_DIR / "STYLE.md").strip()
    good = _read(SOUL_DIR / "examples" / "good-outputs.md").strip()
    parts = [soul]
    if style:
        parts.append("# HOW YOU TALK (voice & style)\n\n" + style)
    if good:
        parts.append("# CALIBRATION — on-voice examples (never quote verbatim)\n\n" + good)
    parts.append("## Right now: a live meeting with the CEO. Be in character.")
    return "\n\n".join(parts)


def main():
    system = build_persona_prompt()
    print(f"PROVIDER = {llm.PROVIDER}  |  persona prompt = {len(system)} chars")
    print("=" * 72)
    for i, q in enumerate(QUESTIONS, 1):
        print(f"\n[Q{i}] {q}\n" + "-" * 72)
        try:
            reply = llm.chat(system, q)
        except Exception as exc:  # noqa: BLE001
            print(f"(error: {exc})")
            continue
        print(reply.strip())
    print("\n" + "=" * 72)


if __name__ == "__main__":
    main()
