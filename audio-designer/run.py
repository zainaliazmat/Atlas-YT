"""Cadence, The Audio / Sound Designer — command-line entry point.

Record the narration for a script (per-scene tts -> narration.wav + transcript):
    python run.py narrate path/to/script.json
    python run.py narrate path/to/project_dir          # holds script.json

Mix the audio (source a cleared bed, place the signature accent, pre-mix master.wav):
    python run.py mix path/to/project_dir              # holds script + style + storyboard
    python run.py mix path/to/script.json

Talk to Cadence (co-worker mode; she can narrate or mix mid-chat):
    python run.py chat

Note: `narrate` runs the real HyperFrames tts (Kokoro) and `mix` hits the live CC/PD
audio allowlist + FFmpeg. With no network/toolchain, the run degrades gracefully — a
bed that can't clear ships as a flagged placeholder and the master runs VO-plus-accent.
"""
import argparse
import sys

import audio_engine as engine


def _print_narration(out: dict) -> None:
    tr = out["transcript"]
    print("\n" + "=" * 70)
    print("NARRATION")
    print("=" * 70)
    print(f"  scenes ........ {len(tr['segments'])}")
    print(f"  total ......... {tr['total_duration_sec']}s")
    print(f"  wav ........... {out['narration_wav']}")
    print("\n  per-scene timing:")
    for s in tr["segments"]:
        print(f"   scene {s['scene_no']:>2}: {s['start_sec']:>7.3f} – {s['end_sec']:>7.3f}s")
    print("=" * 70)


def _print_manifest(manifest: dict, json_path) -> None:
    st = engine.manifest_stats(manifest)
    print("\n" + "=" * 70)
    print("AUDIO MANIFEST")
    print("=" * 70)
    print(f"  tracks ........ {st['tracks']}  ({st['music']} music, {st['sfx']} sfx)")
    print(f"  total ......... {manifest.get('total_duration_sec')}s")
    print(f"  master ........ {'rendered' if st['master'] else 'NOT rendered (VO only)'}")
    print("\n  shape:")
    for t in manifest.get("tracks", []):
        tag = {"cleared": "✓", "sourced": "~", "placeholder": "·"}.get(t.get("status"), "?")
        duck = f" duck:{t['ducking']}" if t.get("ducking") not in (False, None) else ""
        at = f" @{t['at_sec']}s" if t.get("at_sec") is not None else ""
        flag = f"   ⚑ {t.get('flag')}" if t.get("flag") else ""
        print(f"   {tag} {t.get('role'):<9} {t.get('gain_db')}dB{duck}{at}  "
              f"{t.get('license')}{flag}")
    print("\n" + "=" * 70)
    print(f"Saved (for the next agent): {json_path}")


def main():
    parser = argparse.ArgumentParser(prog="run.py", add_help=True)
    sub = parser.add_subparsers(dest="cmd")

    p_narr = sub.add_parser("narrate", help="per-scene tts -> narration.wav + transcript")
    p_narr.add_argument("path", nargs="?", default=None,
                        help="path to a script.json or a project directory")

    p_mix = sub.add_parser("mix", help="source bed + place accent -> master.wav + manifest")
    p_mix.add_argument("path", nargs="?", default=None,
                       help="path to a project directory or a script.json")

    sub.add_parser("chat", help="talk to Cadence (co-worker mode)")

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

    if args.cmd == "narrate":
        if not args.path:
            print("Usage: python run.py narrate <script.json or project_dir>")
            sys.exit(1)
        try:
            out, _ = engine.run_narrate(args.path, quiet=False)
        except (ValueError, RuntimeError) as exc:
            print(f"\nCouldn't record the narration: {exc}")
            sys.exit(1)
        _print_narration(out)
        return

    if args.cmd == "mix":
        if not args.path:
            print("Usage: python run.py mix <project_dir or script.json>")
            sys.exit(1)
        try:
            manifest, json_path = engine.run_mix(args.path, quiet=False)
        except (ValueError, RuntimeError) as exc:
            print(f"\nCouldn't mix the audio: {exc}")
            sys.exit(1)
        _print_manifest(manifest, json_path)
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
