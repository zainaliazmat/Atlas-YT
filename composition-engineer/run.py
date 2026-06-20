"""Mason, The Composition Engineer — command-line entry point.

Compose the per-scene HyperFrames projects from a project dir (the 5 artifacts):
    python run.py compose path/to/project_dir
    python run.py compose path/to/project_dir --no-render   # build + gate, skip render

Assemble the final video (after the human gate; concat + transitions + narration):
    python run.py render path/to/project_dir

Talk to Mason (co-worker mode; he can compose/render mid-chat behind a [y/N] gate):
    python run.py chat
"""
import argparse
import sys

import composition_engine as engine


def _print_compose(manifest: dict, json_path) -> None:
    summ = manifest.get("summary", {})
    print("\n" + "=" * 70)
    print("COMPOSITION")
    print("=" * 70)
    print(f"  scenes ......... {summ.get('total', 0)}")
    print(f"  auto-gate ...... {summ.get('auto_gate')}  "
          f"({summ.get('gated_ok', 0)}/{summ.get('total', 0)} clean)")
    print(f"  rendered ....... {summ.get('rendered', 0)} draft(s)")
    if summ.get("integrity_flags"):
        print(f"  ⚑ integrity .... {summ['integrity_flags']} asset flag(s) for the human gate")
    if summ.get("contrast_failures"):
        print(f"  contrast ....... {summ['contrast_failures']} WCAG warning(s) (non-blocking)")
    for s in manifest.get("scenes", []):
        fx = ", ".join(s.get("effects", [])) or "—"
        star = "  ★" if s.get("signature_beat") else ""
        print(f"   {s.get('scene_no'):>2}. {s.get('layout')} · {s.get('transition')} "
              f"· [{fx}] · {s.get('render_status')}{star}")
    print("\n" + "=" * 70)
    print(f"Saved (for the pipeline): {json_path}")


def main():
    parser = argparse.ArgumentParser(prog="run.py", add_help=True)
    sub = parser.add_subparsers(dest="cmd")

    p_compose = sub.add_parser("compose", help="build + gate + draft-render scenes")
    p_compose.add_argument("path", nargs="?", default=None,
                           help="project directory holding the 5 artifacts")
    p_compose.add_argument("--no-render", action="store_true",
                           help="build + gate only; skip the draft renders")

    p_render = sub.add_parser("render", help="assemble the final video (post-gate)")
    p_render.add_argument("path", nargs="?", default=None, help="project directory")

    sub.add_parser("chat", help="talk to Mason (co-worker mode)")

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

    if args.cmd == "compose":
        if not args.path:
            print("Usage: python run.py compose <project_dir> [--no-render]")
            sys.exit(1)
        try:
            manifest, json_path = engine.run_compose(args.path, render=not args.no_render)
        except ValueError as exc:
            print(f"\nCouldn't compose: {exc}")
            sys.exit(1)
        _print_compose(manifest, json_path)
        sys.exit(0 if manifest.get("verdict") == "pass" else 2)

    if args.cmd == "render":
        if not args.path:
            print("Usage: python run.py render <project_dir>")
            sys.exit(1)
        result = engine.run_render(args.path)
        if result.get("ok"):
            tag = " (skipped — MASON_SKIP_RENDER)" if result.get("skipped") else ""
            print(f"\nFinal video: {result.get('video')}{tag}")
            sys.exit(0)
        print(f"\nFinal assembly failed: {result.get('error')}")
        sys.exit(2)

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
