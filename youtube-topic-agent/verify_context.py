"""verify_context.py — prove SOUL (personality) and SKILL (method) actually
reach the model's context through the real llm.chat() seam.

It does NOT modify your SOUL.md / SKILL.md, and it does NOT use "leak this secret"
tricks (those make a well-behaved model refuse — which looks like a failure but
isn't). Instead it asks legitimate questions whose answers exist ONLY in your
files, so a correct answer proves the content was in context.

  Call A: SOUL as the system prompt, a NEUTRAL user question with no SOUL/SKILL
          text in it. If the model knows it is "Viral Scout", the system prompt
          provably arrived — nothing else in the call could have told it.

  Call B: mirrors analyze() exactly (SOUL=system, SKILL inside the user message).
          It asks the model to recite the outlier_ratio formula and the 5/20/100
          thresholds — numbers that appear ONLY in SKILL.md.

Run:  ../venv/bin/python verify_context.py
"""
import pathlib

import llm

HERE = pathlib.Path(__file__).parent
SOUL = (HERE / "SOUL.md").read_text()
SKILL = (HERE / "SKILL.md").read_text()


def main() -> int:
    # --- Call A: does the SYSTEM prompt (SOUL) reach the model? ----------------
    # Neutral user question; it contains no persona text, so a correct identity
    # answer can only come from the system prompt.
    a_user = (
        "Quick check before we start: what is your name, and in one short "
        "sentence, the single principle you trust most when picking topics?"
    )
    print("[Call A] SOUL as system, neutral question (isolates system-prompt delivery)...")
    a_reply = llm.chat(SOUL, a_user)
    print("--- reply A ---\n" + a_reply.strip() + "\n")

    # --- Call B: does SKILL reach the model? (mirrors analyze) -----------------
    b_user = (
        f"=== METHOD ===\n{SKILL}\n\n"
        "From the method above, answer concisely:\n"
        "1) the exact formula for outlier_ratio\n"
        "2) the three ratio thresholds and what each one means"
    )
    print("[Call B] SOUL as system + SKILL in user (mirrors analyze, checks SKILL delivery)...")
    b_reply = llm.chat(SOUL, b_user)
    print("--- reply B ---\n" + b_reply.strip() + "\n")

    # --- Grading: substring checks against facts unique to each file ----------
    a_low = a_reply.lower()
    soul_ok = "viral scout" in a_low

    b_low = b_reply.lower()
    # outlier_ratio = video_views / channel_subscribers; 5+ notable, 20+ breakout, 100+ smash
    skill_ok = ("subscriber" in b_low) and ("100" in b_reply) and ("20" in b_reply)

    print("=" * 60)
    print(f"[{'PASS' if soul_ok else 'FAIL'}] SOUL reached the model via the SYSTEM prompt "
          f"(identity '{'Viral Scout' if soul_ok else 'NOT FOUND'}')")
    print(f"[{'PASS' if skill_ok else 'FAIL'}] SKILL reached the model via the USER message "
          f"(method formula + thresholds {'recited' if skill_ok else 'NOT FOUND'})")
    print("=" * 60)

    if soul_ok and skill_ok:
        print("\n✅ Both provably in context. Reply A shows the personality (SOUL); "
              "reply B shows the method facts (SKILL).")
        return 0
    print("\n❌ At least one piece did not reach the model — inspect the replies above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
