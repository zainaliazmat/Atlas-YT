"""Sage, The Researcher — command-line entry point.

Research a topic:
    python run.py research "James Webb Space Telescope discoveries"
    python run.py research "ozempic" --angle "is it safe long-term?"

Hand off from Scout (or anything) via a small JSON file {"topic":..,"angle":..}:
    python run.py research --handoff handoff.json

Talk to Sage (co-worker mode; he can run research mid-chat):
    python run.py chat
"""
import argparse
import json
import sys

import researcher


def _print_summary(pack: dict, json_path, md_path) -> None:
    """A compact terminal summary; the full readable pack is the saved Markdown."""
    def n(key):
        return len(pack.get(key, []))
    print("\n" + "=" * 70)
    print(f"RESEARCH PACK — {pack['topic']}")
    if pack.get("angle"):
        print(f"angle: {pack['angle']}")
    print("=" * 70)
    print(f"\n{pack.get('overview', '')}\n")
    print(f"  ✅ verified facts ......... {n('verified_facts')}")
    print(f"  📊 key statistics ........ {n('key_statistics')}")
    print(f"  🕑 timeline events ....... {n('timeline')}")
    print(f"  ❌ myths corrected ....... {n('myths_and_corrections')}")
    print(f"  ⚖️  contested/uncertain ... {n('contested_or_uncertain')}")
    print(f"  💬 notable quotes ........ {n('notable_quotes')}")
    print(f"  ❓ open questions ........ {n('open_questions')}")
    print(f"  🎬 suggested angles ...... {n('suggested_angles')}")
    print(f"  📚 sources ............... {n('sources')}")
    print("\n" + "=" * 70)
    print("Saved:")
    print(f"  JSON (for the next agent): {json_path}")
    print(f"  Markdown (for you):        {md_path}")


def _load_handoff(path: str) -> tuple[str, str | None]:
    with open(path) as f:
        data = json.load(f)
    topic = (data.get("topic") or "").strip()
    if not topic:
        print(f"Handoff file {path!r} has no 'topic'.")
        sys.exit(1)
    return topic, (data.get("angle") or None)


def main():
    parser = argparse.ArgumentParser(prog="run.py", add_help=True)
    sub = parser.add_subparsers(dest="cmd")

    p_research = sub.add_parser("research", help="run a research pass")
    p_research.add_argument("topic", nargs="*", help="the topic to research")
    p_research.add_argument("--angle", default=None, help="optional angle/context")
    p_research.add_argument("--handoff", default=None,
                            help="path to a JSON handoff {topic, angle}")

    sub.add_parser("chat", help="talk to Sage (co-worker mode)")

    args = parser.parse_args()

    if args.cmd == "chat":
        try:
            import chat
        except ImportError as exc:
            print(f"Couldn't start chat ({exc}). Install deps: "
                  "pip install -r requirements.txt")
            sys.exit(1)
        chat.start()
        return

    if args.cmd == "research":
        if args.handoff:
            topic, angle = _load_handoff(args.handoff)
        else:
            topic = " ".join(args.topic).strip()
            angle = args.angle
        if not topic:
            print('Usage: python run.py research "your topic" [--angle "..."]')
            sys.exit(1)
        ok, reason = researcher.validate_topic(topic)
        if not ok:
            print(reason)
            sys.exit(1)
        pack, json_path, md_path = researcher.run(topic, angle, quiet=False)
        _print_summary(pack, json_path, md_path)
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
