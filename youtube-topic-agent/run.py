"""Viral Scout — command-line entry point.

Research a niche:
    python run.py "faceless finance youtube channels"

Sharper outliers (slower — divides by each channel's median recent views
instead of its subscriber count; uses a 7-day channel cache):
    python run.py --deep "faceless finance youtube channels"

Talk to Viral Scout (conversational mode; he can run research mid-chat):
    python run.py chat

Record a topic that performed well (teaches the agent your niche over time):
    python run.py win "I tried index investing for 30 days"
"""
import sys
import textwrap

import agent


def _print_ideas(niche, ideas):
    """Pretty-print the ranked topic ideas the agent returned."""
    print("=" * 70)
    print(f"VIRAL SCOUT — {len(ideas)} topic ideas for: {niche}")
    print("=" * 70)
    for i, idea in enumerate(ideas, 1):
        titles = idea.get("titles", [])
        print(f"\n#{i}  [{idea.get('confidence', 'n/a')}]")
        for t in titles:
            print(f"     • {t}")
        # angle / thumbnail / why are wrapped so long lines stay readable.
        for label in ("angle", "thumbnail", "why"):
            val = idea.get(label, "")
            if val:
                wrapped = textwrap.fill(
                    str(val), width=66,
                    initial_indent=f"     {label:9}: ",
                    subsequent_indent=" " * 16,
                )
                print(wrapped)
    print("\n" + "=" * 70)
    print("Saved to memory.json. When one of these works, run:")
    print('  python run.py win "the title that worked"')


def main():
    args = sys.argv[1:]

    # Pull the optional --deep flag out from anywhere in the args; the rest are
    # treated exactly as before (subcommand or niche words).
    deep = "--deep" in args
    args = [a for a in args if a != "--deep"]

    if not args:
        print(__doc__)
        sys.exit(1)

    # Subcommand: talk to Viral Scout in a conversational REPL.
    if args[0] == "chat":
        import chat
        chat.start()
        return

    # Subcommand: record a win.
    if args[0] == "win":
        if len(args) < 2:
            print('Usage: python run.py win "the topic that worked"')
            sys.exit(1)
        topic = " ".join(args[1:])
        agent.record_win(topic)
        print(f"✅ Recorded win: {topic}")
        print("   Future runs will lean toward topics like this in your niche.")
        return

    # Default: research the niche given on the command line.
    niche = " ".join(args)
    ideas = agent.run(niche, deep=deep)
    if ideas:
        _print_ideas(niche, ideas)


if __name__ == "__main__":
    main()
