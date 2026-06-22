"""Vera, The Reference Analyst — command-line entry point.

Build / extend a rubric from one or more reference videos (feeding more videos
TIGHTENS the bands toward their shared DNA):
    python run.py rubric ref1.mp4 ref2.mp4 --standard "my-house-style"
    python run.py rubric another.mp4 --standard "my-house-style"   # merges in

Skip the vision/style-profile pass (objective-only, fully offline):
    python run.py rubric ref.mp4 --no-vision

Talk to Vera (co-worker mode):
    python run.py chat
"""
import argparse
import json
import sys

import chat_state
import rubric_store


def _print_summary(rubric: dict, standard: str, saved, out_path) -> None:
    t = rubric.get("targets", {})

    def band(group, key):
        node = (t.get(group, {}) or {}).get(key, {}) or {}
        v, b = node.get("value"), node.get("band")
        if v is None:
            return "—"
        return f"{v}  band {b}" if b else f"{v}"

    print("\n" + "=" * 70)
    print(f"RUBRIC — standard: {standard}")
    print("=" * 70)
    print(f"source videos ........ {len(rubric.get('source_videos', []))} "
          f"({', '.join(rubric.get('source_videos', []) or ['—'])})")
    print(f"  avg shot (s) ....... {band('pacing', 'avg_shot_sec')}")
    print(f"  cuts / min ......... {band('pacing', 'cuts_per_min')}")
    print(f"  kinetic score ...... {band('motion', 'kinetic_score')}")
    print(f"  saturation ......... {band('color', 'saturation')}")
    print(f"  brightness ......... {band('color', 'brightness')}")
    print(f"  integrated LUFS .... {band('audio', 'integrated_lufs')}")
    print(f"  speech ratio ....... {band('audio', 'speech_ratio')}")
    print(f"  duration (s) ....... {band('structure', 'duration_sec')}")
    judged = rubric.get("judged", {})
    print(f"  judged ............. {judged.get('status', '?')} "
          f"({len(judged.get('frames', []))} frames)")
    if judged.get("error"):
        print(f"      (style profile degraded: {judged['error']})")
    if rubric.get("notes"):
        print(f"  notes .............. {rubric['notes']}")

    oq = rubric.get("open_questions", [])
    if oq:
        print("\nQuestions only taste can answer:")
        for q in oq:
            print(f"  • [{q.get('id')}] {q.get('plain')}")
    print("\n" + "=" * 70)
    print("Saved:")
    print(f"  rubric (durable, merges):  {saved}")
    if out_path:
        print(f"  rubric (this run's copy):  {out_path}")


def main():
    parser = argparse.ArgumentParser(prog="run.py", add_help=True)
    sub = parser.add_subparsers(dest="cmd")

    p_rub = sub.add_parser("rubric", help="build/extend a rubric from reference video(s)")
    p_rub.add_argument("videos", nargs="*", help="one or more reference video files")
    p_rub.add_argument("--standard", default="default",
                       help="the named standard to build/extend (default: 'default')")
    p_rub.add_argument("--out", default=None, help="also write this run's rubric here")
    p_rub.add_argument("--work", default=None, help="frames/work directory override")
    p_rub.add_argument("--no-vision", action="store_true",
                       help="skip the LLM style-profile pass (objective metrics only)")

    sub.add_parser("chat", help="talk to Vera (co-worker mode)")

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

    if args.cmd == "rubric":
        if not args.videos:
            print('Usage: python run.py rubric ref1.mp4 [ref2.mp4 ...] '
                  '[--standard NAME] [--no-vision]')
            sys.exit(1)
        existing, missing = rubric_store.validate_videos(args.videos)
        if missing:
            print(f"⚠️  Skipping {len(missing)} file(s) I couldn't find: "
                  + ", ".join(missing))
        if not existing:
            print("None of the given video files exist — nothing to analyze.")
            sys.exit(1)

        vision_fn = None
        if not args.no_vision:
            try:
                import llm
                vision_fn = llm.make_style_profiler()
            except Exception as exc:  # degrade to objective-only, never crash
                print(f"(style-profile pass unavailable: {exc} — running objective-only.)")

        rubric = rubric_store.build_standard(
            args.standard, args.videos, vision_fn=vision_fn, work_dir=args.work)
        saved = rubric_store.rubric_path(args.standard)
        if args.out:
            chat_state.atomic_write_json(args.out, rubric)
        _print_summary(rubric, args.standard, saved, args.out)
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
