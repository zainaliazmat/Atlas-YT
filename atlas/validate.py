"""Input validation — reject garbage BEFORE Atlas spends API calls on it.

Atlas owns this on purpose. Sage's `validate_topic` lives in its ENGINE
(importable), but Scout's `validate_niche` lives in Scout's chat.py (NOT importable
the way the engine is). Rather than reach into a sibling's chat module, Atlas keeps
its own copies here — same logic, no coupling to a sibling's REPL file.
"""
from __future__ import annotations


def _keyboard_smash(text: str) -> bool:
    """5+ consecutive consonants in a single-word input flags gibberish.

    Only judges single words — real multi-word niches/topics with a space are
    almost never smashes, so "asdfkjh" is caught while "chess"/"crypto" are not.
    'y' counts as a vowel here.
    """
    if " " in text:
        return False
    run = best = 0
    for c in text.lower():
        if c.isalpha() and c not in "aeiouy":
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best >= 5


def validate_niche(niche: str) -> tuple[bool, str]:
    """Return (ok, reason). Rejects empty, too-short, and keyboard-smash niches."""
    n = (niche or "").strip()
    if len(n) < 3:
        return False, "That niche is too short — give me a few words to work with."
    if not any(c.isalpha() for c in n):
        return False, "I need an actual niche, not symbols or numbers."
    if _keyboard_smash(n):
        return False, "That looks like a keyboard smash — give me a real niche."
    return True, ""


def validate_topic(topic: str) -> tuple[bool, str]:
    """Return (ok, reason). Same shape as Sage's engine-level check."""
    t = (topic or "").strip()
    if len(t) < 3:
        return False, "That topic is too short — give me a few words to work with."
    if not any(c.isalpha() for c in t):
        return False, "I need an actual topic, not symbols or numbers."
    if _keyboard_smash(t):
        return False, "That looks like a keyboard smash — give me a real topic."
    return True, ""
