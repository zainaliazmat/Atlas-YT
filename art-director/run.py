"""Iris, The Art Director — command-line entry point.

Design the global look from a script:
    python run.py style path/to/script.json
    python run.py style path/to/project_dir          # holds script.json

Storyboard the scenes (designs a style guide first if there isn't one beside it):
    python run.py board path/to/script.json
    python run.py board path/to/project_dir

Talk to Iris (co-worker mode; she can produce specs mid-chat):
    python run.py chat
"""
import argparse
import sys

import art_engine as engine


def _print_style(style: dict, json_path) -> None:
    p = style.get("palette", {})
    print("\n" + "=" * 70)
    print("STYLE GUIDE")
    print("=" * 70)
    print(f"  palette ....... primary {p.get('primary')}, bg {p.get('bg')}, "
          f"text {p.get('text')}")
    print(f"  accents ....... {', '.join(p.get('accents', []) or []) or '(none)'}")
    print(f"  signature ..... {p.get('signature_highlight')}")
    print(f"  fps ........... {style.get('fps')}")
    print(f"  budget ........ {style.get('motion', {}).get('max_per_scene')}/scene")
    print(f"  textures ...... {', '.join(t.get('name') for t in style.get('textures', [])) or '(none)'}")
    print("\n" + "=" * 70)
    print(f"Saved (for the next agent): {json_path}")


def _print_board(board: dict, json_path) -> None:
    print("\n" + "=" * 70)
    print("STORYBOARD")
    print("=" * 70)
    print(f"  scenes ........ {board.get('total_scenes', 0)}")
    print("\n  shape:")
    for s in board.get("scenes", []):
        fx = ", ".join(e.get("name") for e in s.get("effects", [])) or "—"
        star = "  ★ signature beat" if s.get("signature_beat") else ""
        print(f"   {s.get('scene_no'):>2}. {s.get('layout')} · {s.get('transition')} "
              f"· [{fx}]{star}")
    print("\n" + "=" * 70)
    print(f"Saved (for the next agent): {json_path}")


def main():
    parser = argparse.ArgumentParser(prog="run.py", add_help=True)
    sub = parser.add_subparsers(dest="cmd")

    p_style = sub.add_parser("style", help="design the style guide from a script")
    p_style.add_argument("path", nargs="?", default=None,
                         help="path to a script.json or a project directory")

    p_board = sub.add_parser("board", help="storyboard the scenes from a script")
    p_board.add_argument("path", nargs="?", default=None,
                         help="path to a script.json or a project directory")

    sub.add_parser("chat", help="talk to Iris (co-worker mode)")

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

    if args.cmd == "style":
        if not args.path:
            print("Usage: python run.py style <script.json or project_dir>")
            sys.exit(1)
        try:
            style, json_path = engine.run_style(args.path, quiet=False)
        except ValueError as exc:
            print(f"\nCouldn't design the style: {exc}")
            sys.exit(1)
        _print_style(style, json_path)
        return

    if args.cmd == "board":
        if not args.path:
            print("Usage: python run.py board <script.json or project_dir>")
            sys.exit(1)
        try:
            board, json_path = engine.run_storyboard(args.path, quiet=False)
        except ValueError as exc:
            print(f"\nCouldn't build the storyboard: {exc}")
            sys.exit(1)
        _print_board(board, json_path)
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
