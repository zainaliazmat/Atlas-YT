"""Atlas — the Showrunner. Command-line entry point.

The meeting room (PRIMARY interface):
    python run.py chat

One-shot canonical flow (proves the Scout->Sage orchestration end to end):
    python run.py "home espresso"

    The CEO gives a niche; Atlas runs the default playbook autonomously:
    Scout finds topics  ->  Atlas decides the strongest & says why  ->
    Sage researches it  ->  Atlas reports back. Deterministic 🔎/📚 status lines
    appear as the teammates work; Atlas's decisions stream as its own text.

The production pipeline (the Showrunner's full video playbook, against stub
specialists; deterministic + offline):
    python run.py produce "home espresso"            # gated run (pauses at gates)
    python run.py produce "home espresso" --unattended   # run straight through
    python run.py produce --resume <slug> --approve factcheck      # clear a gate
    python run.py produce --resume <slug> --approve final_render
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


def run_produce(args: list[str]) -> None:
    """Drive the production pipeline from the CLI (new run, or resume past a gate)."""
    import pipeline

    unattended = False
    resume = None
    approve: list[str] = []
    brief_parts: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--unattended":
            unattended = True
        elif a == "--resume" and i + 1 < len(args):
            resume = args[i + 1]; i += 1
        elif a == "--approve" and i + 1 < len(args):
            approve += [g for g in args[i + 1].split(",") if g]; i += 1
        else:
            brief_parts.append(a)
        i += 1
    brief = " ".join(brief_parts).strip()

    if not resume and not brief:
        print('Give me a brief:  python run.py produce "home espresso"')
        sys.exit(1)

    print("=" * 70)
    print("ATLAS — the Showrunner   ·   production pipeline (stub specialists)")
    print("=" * 70)
    result = pipeline.produce(brief or None, slug=resume, approve=approve or None,
                              unattended=unattended)

    status = result.get("status")
    print()
    if status == "done":
        print(f"🎬 Video produced: {result['video']}")
    elif status == "blocked":
        gate = result.get("gate")
        print(f"⏸️  Paused at the {gate} gate. {result.get('reason','')}")
        print(f"    Details: {result.get('details')}")
        nxt = "factcheck" if gate == "factcheck" else "final_render"
        print(f"    Resume after sign-off:\n"
              f"      python run.py produce --resume {result['slug']} --approve {nxt}")
    else:
        print(f"❌ {status} at stage {result.get('stage')}: {result.get('errors')}")
        sys.exit(1)
    print(f"    Project: {result['project_dir']}")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    if args[0] == "produce":
        run_produce(args[1:])
        return

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
