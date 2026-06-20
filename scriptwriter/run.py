"""Marlow, The Scriptwriter — command-line entry point.

Write a script from a research brief:
    python run.py write path/to/research_brief.json
    python run.py write path/to/project_dir          # holds research_brief.json

Talk to Marlow (co-worker mode; he can write a script mid-chat):
    python run.py chat
"""
import argparse
import sys

import script_engine


def _print_summary(script: dict, json_path) -> None:
    """A compact terminal summary; the full script is the saved JSON."""
    print("\n" + "=" * 70)
    print(f"SCRIPT — {script.get('working_title','(untitled)')}")
    print("=" * 70)
    if script.get("hook"):
        print(f"\nHook: {script['hook']}\n")
    print(f"  scenes ............ {script.get('total_scenes', 0)}")
    print(f"  est. runtime ...... ~{script.get('est_runtime_sec', 0)}s")
    n_claims = sum(len(s.get("claims", [])) for s in script.get("scenes", []))
    print(f"  tagged claims ..... {n_claims}")
    print("\n  shape:")
    for s in script.get("scenes", []):
        nclaims = len(s.get("claims", []))
        cite = f"  [{nclaims} sourced]" if nclaims else ""
        print(f"   {s.get('scene_no'):>2}. ({s.get('beat','point')}) "
              f"{s.get('point','')}{cite}")
    if script.get("cta"):
        print(f"\nClose: {script['cta']}")
    print("\n" + "=" * 70)
    print(f"Saved (for the next agent): {json_path}")


def main():
    parser = argparse.ArgumentParser(prog="run.py", add_help=True)
    sub = parser.add_subparsers(dest="cmd")

    p_write = sub.add_parser("write", help="write a script from a research brief")
    p_write.add_argument("path", nargs="?", default=None,
                         help="path to a research_brief.json or a project directory")

    sub.add_parser("chat", help="talk to Marlow (co-worker mode)")

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

    if args.cmd == "write":
        if not args.path:
            print("Usage: python run.py write <research_brief.json or project_dir>")
            sys.exit(1)
        brief = script_engine.load_brief(args.path)
        ok, reason = script_engine.validate_brief(brief)
        if not ok:
            print(reason)
            sys.exit(1)
        try:
            script, json_path = script_engine.run(brief, quiet=False)
        except ValueError as exc:
            print(f"\nCouldn't write the script: {exc}")
            sys.exit(1)
        _print_summary(script, json_path)
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
