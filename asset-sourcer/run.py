"""Magpie, The Asset Sourcer — command-line entry point.

Source + license the assets a storyboard needs:
    python run.py source path/to/storyboard.json
    python run.py source path/to/project_dir          # holds storyboard.json (+ style_guide.json)

Talk to Magpie (co-worker mode; she can source a manifest mid-chat):
    python run.py chat

Note: `source` hits the live allowlist archives over the network. With no network /
no keys, every shot degrades gracefully to a flagged local placeholder — the run still
produces a schema-shaped manifest, just an all-placeholder one.
"""
import argparse
import sys

import source_engine as engine


def _print_manifest(manifest: dict, json_path) -> None:
    st = engine.manifest_stats(manifest)
    print("\n" + "=" * 70)
    print("ASSET MANIFEST")
    print("=" * 70)
    print(f"  assets ........ {st['total']}")
    print(f"  cleared ....... {st['cleared']}")
    print(f"  sourced ....... {st['sourced']}  (licensed, clearance incomplete — flagged)")
    print(f"  placeholder ... {st['placeholder']}")
    print("\n  shape:")
    for a in manifest.get("assets", []):
        tag = {"cleared": "✓", "sourced": "~", "placeholder": "·"}.get(a.get("status"), "?")
        flag = f"   ⚑ {a.get('flag')}" if a.get("flag") else ""
        print(f"   {tag} {a.get('asset_id'):>8} (sc{a.get('scene_no')}) "
              f"{a.get('type'):<8} {a.get('source'):<16} {a.get('license')}{flag}")
    print("\n" + "=" * 70)
    print(f"Saved (for the next agent): {json_path}")


def main():
    parser = argparse.ArgumentParser(prog="run.py", add_help=True)
    sub = parser.add_subparsers(dest="cmd")

    p_source = sub.add_parser("source", help="source + license a storyboard's assets")
    p_source.add_argument("path", nargs="?", default=None,
                          help="path to a storyboard.json or a project directory")

    sub.add_parser("chat", help="talk to Magpie (co-worker mode)")

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

    if args.cmd == "source":
        if not args.path:
            print("Usage: python run.py source <storyboard.json or project_dir>")
            sys.exit(1)
        try:
            manifest, json_path = engine.run_source(args.path, quiet=False)
        except ValueError as exc:
            print(f"\nCouldn't source the assets: {exc}")
            sys.exit(1)
        _print_manifest(manifest, json_path)
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
