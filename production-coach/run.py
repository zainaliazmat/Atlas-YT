"""Flux, The Production Coach — command-line entry point.

Author a soft-tier coaching addendum for a craft specialist (the direction to move
the metric is GIVEN — decided by the rubric, not by Flux):
    python run.py propose --band compose:motion_energy \\
        --direction "RAISE it to about 10" \\
        --preserve "Keep effect_discipline and layout_variety in band." \\
        --owner Mason

Talk to Flux (co-worker mode):
    python run.py chat
"""
import argparse
import sys

import coach_engine


def _print_addendum(result: dict) -> None:
    """A compact terminal view of the proposed coaching addendum."""
    print("\n" + "=" * 70)
    print(f"COACHING ADDENDUM — {result.get('domain', '?')} · "
          f"target {result.get('band_id', '?')}")
    print("=" * 70)
    if result.get("owner"):
        print(f"  for ........ {result['owner']}")
    print(f"  direction .. {result.get('direction', '')}")
    print(f"  source ..... {result.get('source', '?')} "
          "(llm = brain-authored, rule = deterministic fallback)")
    print("\n" + result.get("addendum", "").rstrip())
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(prog="run.py", add_help=True)
    sub = parser.add_subparsers(dest="cmd")

    p_prop = sub.add_parser("propose",
                            help="author a soft-tier coaching addendum for a craft metric")
    p_prop.add_argument("--band", required=True,
                        help="the band_id to move (e.g. compose:motion_energy)")
    p_prop.add_argument("--direction", required=True,
                        help="the direction to move it — DECIDED by the rubric, given to Flux")
    p_prop.add_argument("--preserve", default="",
                        help="sibling properties to keep in band while fixing this one")
    p_prop.add_argument("--owner", default="",
                        help="the craft specialist being coached (e.g. Mason)")

    sub.add_parser("chat", help="talk to Flux (co-worker mode)")

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
