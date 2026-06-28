"""Atlas — the Showrunner. Command-line entry point.

Atlas is the SOLE orchestrator and is reached through a chat UI. He runs the whole
team — including the full video-production flow — as a PLAYBOOK in his own head,
calling his teammates' tools in sequence against one project workspace. There is no
deterministic pipeline and no dashboard.

The chat UIs:
    chainlit run web/app.py -w      # the web meeting room (primary)
    python run.py chat              # the terminal meeting room (dev fallback)

One-shot canonical flow (proves the Scout->Sage orchestration end to end):
    python run.py "home espresso"

    The CEO gives a niche; Atlas runs the default playbook autonomously:
    Scout finds topics  ->  Atlas decides the strongest & says why  ->
    Sage researches it  ->  Atlas reports back. Deterministic 🔎/📚 status lines
    appear as the teammates work; Atlas's decisions stream as its own text.

To make a full video, just talk to Atlas in chat: "make a short video about X."
He starts a project, runs the playbook (research → script → fact-check → style →
storyboard → assets → narration → compose → mix → render), stops at the fact-check
checkpoint, and asks before the final render.
"""
import sys

import validate
from orchestrator import Orchestrator

CANONICAL = ('I want research on a viral topic in this niche: "{niche}". '
             "Find the topic options, pick the single strongest one and tell me why, "
             "then research it and bring the findings back to me.")


def run_canonical(niche: str) -> None:
    ok, reason = validate.validate_niche(niche)
    if not ok:
        print(reason)
        sys.exit(1)

    print("=" * 70)
    print(f"ATLAS — the meeting room (one-shot)   ·   niche: {niche}")
    print("=" * 70)
    msg = CANONICAL.format(niche=niche)
    print(f"\nYou: {msg}\n")
    print("Atlas: ", end="", flush=True)

    orch = Orchestrator()
    try:
        orch.ask(msg)
    except Exception as exc:  # the REPL/meeting never crashes ugly
        print(f"\n\n(The meeting hit a problem: {exc}\n"
              " If it's a rate-limit, wait a minute and retry.)")
        sys.exit(1)
    print("\n")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    if args[0] == "chat":
        try:
            import chat  # arrives in Phase 2
        except ImportError:
            print("The full meeting-room chat (memory, /agents, /ask, direct address) "
                  "lands in Phase 2.\nFor now, prove the orchestration with a niche:\n"
                  '    python run.py "home espresso"')
            sys.exit(1)
        chat.start()
        return

    run_canonical(" ".join(args))


if __name__ == "__main__":
    main()
