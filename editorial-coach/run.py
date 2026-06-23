"""Quill, The Editorial Coach — command-line entry point.

Author a coaching addendum for a content specialist (the rubric decides the band +
the direction; Quill only writes the persuasive, in-domain coaching text):
    python run.py propose --band script:info_density \
        --direction "LOWER it to about 2.75" \
        --preserve " Keep script:runtime_fit in [60,90]." --owner Marlow

Talk to Quill (co-worker mode):
    python run.py chat
"""
import argparse
import sys

import coach_engine


def _print_addendum(result: dict) -> None:
    """A compact terminal view of the proposed coaching addendum."""
    print("\n" + "=" * 70)
    print(f"COACH NOTE — {result['domain']} · target {result['band_id']}")
    print("=" * 70)
    print(f"  owner ............. {result.get('owner') or '(unspecified)'}")
    print(f"  direction ......... {result['direction']}")
    print(f"  source ............ {result['source']}")
    print("\n" + result["addendum"])
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(prog="run.py", add_help=True)
    sub = parser.add_subparsers(dest="cmd")

    p_prop = sub.add_parser("propose",
                            help="author a coaching addendum for a content specialist")
    p_prop.add_argument("--band", required=True,
                        help="the band_id to move into range (e.g. script:info_density)")
    p_prop.add_argument("--direction", required=True,
                        help="the direction to move the metric (decided by the rubric)")
    p_prop.add_argument("--preserve", default="",
                        help="sibling properties to keep in range (appended verbatim)")
    p_prop.add_argument("--owner", default="",
                        help="the specialist being coached (e.g. Marlow, Sage)")

    sub.add_parser("chat", help="talk to Quill (co-worker mode)")

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

    if args.cmd == "propose":
        result = coach_engine.propose_addendum(
            band_id=args.band, direction=args.direction,
            preserve=args.preserve, owner=args.owner)
        _print_addendum(result)
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
