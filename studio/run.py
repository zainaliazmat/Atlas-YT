"""studio.run — CLI entry for the v2 production path.

Subcommands:

  produce   Run the production spine for a brief/topic (studio.pipeline.produce).
  packs     Inspect the Design Pack registry — list / show a pack (studio.packs).
  assets    Inspect / resolve the Asset Library — list / resolve a manifest
            (studio.library).
  library   Manage the Asset Library itself — status / rebuild manifest
            (studio.library).

Usage (once implemented):
    python -m studio.run produce  --channel <id> "<topic>" [--pack <id>] [--no-gates]
    python -m studio.run packs    list
    python -m studio.run packs    show <pack-id>
    python -m studio.run assets    resolve <manifest.json>
    python -m studio.run library   status

This file wires the argparse SHAPE of the CLI so the surface is concrete. The
command handlers are TODO stubs — no production logic yet.
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level parser and its four subcommands.

    Only the argument SHAPE is defined here; handlers are attached via
    ``set_defaults(func=...)`` and currently raise NotImplementedError.
    """
    parser = argparse.ArgumentParser(
        prog="studio",
        description="v2 video production path (see studio/GOLDEN_REFERENCE.md).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- produce -------------------------------------------------------------
    p_produce = sub.add_parser(
        "produce", help="run the full resumable production spine → video.mp4")
    p_produce.add_argument("topic", nargs="?", default=None,
                           help="brief/topic text (or use --brief; omit with --resume)")
    p_produce.add_argument("--brief", default=None, help="the production brief/topic text")
    p_produce.add_argument("--channel", default=None,
                           help="channel id from channels.json (selects default pack + voice + budget)")
    p_produce.add_argument("--pack", default=None, help="Design Pack id (overrides the channel default)")
    p_produce.add_argument("--voice", default=None, help="VO voice (overrides the channel default)")
    p_produce.add_argument("--slug", default=None, help="project slug (default: derived from the brief)")
    p_produce.add_argument("--resume", metavar="SLUG", default=None,
                           help="resume an existing project by slug")
    p_produce.add_argument("--approve", action="append", default=[], metavar="GATE",
                           help="gate(s) to clear on resume (factcheck re-runs; final ships)")
    p_produce.add_argument("--unattended", action="store_true",
                           help="auto-approve the FINAL gate only when motion+vision pass and "
                                "the est. render cost is under budget (factcheck stays hard)")
    p_produce.add_argument("--render-budget", type=float, default=None,
                           help="render-cost ceiling (sec) for --unattended auto-approval")
    p_produce.add_argument("--no-gates", dest="gates", action="store_false",
                           help="bypass the human pauses entirely (factcheck block still blocks)")
    p_produce.set_defaults(func=_cmd_produce, gates=True)

    # --- packs ---------------------------------------------------------------
    # Bare `packs` lists the registry; flags select other actions.
    p_packs = sub.add_parser("packs", help="inspect / validate the Design Pack registry")
    p_packs.add_argument("--validate", metavar="ID", help="validate that a pack is well-formed")
    p_packs.add_argument("--show", metavar="ID", help="show a pack's resolved partials + tokens")
    p_packs.set_defaults(func=_cmd_packs)

    # --- assets --------------------------------------------------------------
    p_assets = sub.add_parser("assets", help="inspect / resolve the Asset Library")
    assets_sub = p_assets.add_subparsers(dest="assets_action", required=True)
    assets_sub.add_parser("list", help="list available assets")
    p_assets_resolve = assets_sub.add_parser("resolve", help="resolve a manifest")
    p_assets_resolve.add_argument("manifest", help="path to an asset manifest")
    p_assets.set_defaults(func=_cmd_assets)

    # --- library -------------------------------------------------------------
    # Bare `library` prints status; flags select list / add / gc.
    p_library = sub.add_parser("library", help="manage the shared Asset Library")
    p_library.add_argument("--list", action="store_true", help="list cached assets")
    p_library.add_argument("--kind", help="filter --list by kind (font/icon/sfx/music/img/...)")
    p_library.add_argument("--tags", help="filter --list by comma-separated tags")
    p_library.add_argument("--gc", action="store_true", help="garbage-collect dangling entries + orphan files")
    p_library.add_argument("--add", metavar="FILE", help="add a file to the library")
    p_library.add_argument("--license", default="Unknown", help="--add: license string")
    p_library.add_argument("--attribution", default="", help="--add: attribution credit line")
    p_library.add_argument("--source", default="", help="--add: where it came from")
    p_library.add_argument("--provenance", default="sourced", choices=["sourced", "generated", "procedural"], help="--add: provenance")
    p_library.add_argument("--recolorable", action="store_true", help="--add: mark recolorable")
    p_library.set_defaults(func=_cmd_library)

    # --- review --------------------------------------------------------------
    # Bare `review <slug>` runs the full in-loop multi-critic review (Prompt 5.0).
    # `review --motion <slug>` runs only the no-dead-air motion gate (a subset).
    p_review = sub.add_parser("review", help="in-loop multi-critic draft review (Prompt 5.0)")
    p_review.add_argument("slug", nargs="?", default=None,
                          help="project slug to review (full multi-critic review)")
    p_review.add_argument("--motion", metavar="SLUG",
                          help="run ONLY the no-dead-air motion gate on a project's draft")
    p_review.add_argument("--mode", choices=["auto", "stop"], default=None,
                          help="auto: apply Blockers+Majors & re-render (default); "
                               "stop: rank only, escalate all for human approval")
    p_review.add_argument("--no-render", dest="render", action="store_false",
                          help="auto mode: apply + gate the edits but skip the re-render")
    p_review.add_argument("--no-polish", dest="polish", action="store_false",
                          help="skip the polish-vs-reference vision anchor")
    p_review.add_argument("--video", default=None,
                          help="explicit draft render to analyze (default: newest for the slug)")
    p_review.set_defaults(func=_cmd_review, render=True, polish=True)

    return parser


def _cmd_review(args: argparse.Namespace) -> int:
    """Handle ``review`` — full multi-critic review (``<slug>``) or the no-dead-air gate
    only (``--motion <slug>``).

    The motion gate frame-diffs the draft and returns non-zero on any dead air. The full
    review collects evidence, runs the seven vision critics, synthesizes a ranked fix
    list, (in auto mode) auto-applies Blockers+Majors and re-renders the affected scenes,
    and persists the critique to state.json. Returns non-zero when Blocker/Major fixes
    remain unresolved (escalated), so CI/the pipeline flags rather than passing silently.
    """
    if args.motion:
        from studio.review import motion_check as mc
        try:
            report = mc.motion_check(args.motion, video=args.video)
        except FileNotFoundError as exc:
            print(f"✗ {exc}")
            return 2
        print(mc.format_table(report))
        return 1 if report.get("any_flag") else 0

    if not args.slug:
        print("review: pass a project <slug> for the full review, or --motion <slug> "
              "for the no-dead-air gate only")
        return 2

    from studio import review as review_mod
    try:
        report = review_mod.review(args.slug, mode=args.mode, video=args.video,
                                   do_render=args.render, polish=args.polish)
    except FileNotFoundError as exc:
        print(f"✗ {exc}")
        return 2
    print(review_mod.format_report(report))

    syn = report.get("synthesis", {})
    ap = report.get("apply") or {}
    applied_ids = {a["id"] for a in ap.get("applied", [])}
    # unresolved = Blocker/Major fixes that were NOT auto-applied
    unresolved = [f for f in syn.get("fixes", [])
                  if f["severity"] in ("Blocker", "Major") and f["id"] not in applied_ids]
    return 1 if unresolved else 0


def _slugify(text: str) -> str:
    """Derive a filesystem-safe project slug from brief text."""
    import re
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:48].rstrip("-")) or "untitled"


def _cmd_produce(args: argparse.Namespace) -> int:
    """Handle ``produce`` — resolve channel/pack config, assemble the brief, run the
    resumable spine, print a status digest, and return an exit code that reflects WHERE
    it stopped (0 complete, 1 render failed, 2 blocked at factcheck, 3 awaiting final
    gate)."""
    from studio import config, pipeline

    if args.resume:
        slug = args.resume
        state = pipeline._read_json(pipeline.project_dir(slug) / "state.json")
        if state is None:
            print(f"✗ no project to resume at slug {slug!r}")
            return 2
        brief = state.get("brief") or {}
    else:
        brief_text = args.brief or args.topic
        if not brief_text:
            print("produce: pass a brief (positional, or --brief), or --resume <slug>")
            return 2
        slug = args.slug or _slugify(brief_text)
        brief = {"topic": brief_text, "angle": None}

    run_config = config.resolve_run_config(
        channel=args.channel, pack=args.pack, voice=args.voice,
        render_budget_sec=args.render_budget)

    try:
        state = pipeline.produce(
            brief, slug,
            approve=set(args.approve), gates=args.gates, unattended=args.unattended,
            run_config=run_config)
    except pipeline.PipelineError as exc:
        print(f"✗ {exc}")
        return 1

    print(_format_produce_status(state))
    status = state.get("status")
    return {"complete": 0, "render_failed": 1, "blocked_at_factcheck": 2,
            "awaiting_final_gate": 3, "blocked_at_gate": 4}.get(status, 0)


def _format_produce_status(state: dict) -> str:
    """A compact stage/gate digest of a produce run for the terminal."""
    lines = [f"PRODUCE — {state.get('slug')}  [{state.get('status')}]"]
    rc = state.get("run_config") or {}
    lines.append(f"  channel={rc.get('channel')} pack={rc.get('pack_id')} "
                 f"voice={rc.get('voice')} budget={rc.get('render_budget_sec')}s")
    from studio.pipeline import STAGES, GATES
    for s in STAGES:
        st = state.get("stages", {}).get(s, {})
        mark = {"done": "✓", "blocked": "⛔", "awaiting_approval": "⏸",
                "error": "✗", "pending": "·"}.get(st.get("status", "pending"), "·")
        gate = " ★GATE" if s in GATES else ""
        lines.append(f"    {mark} {s}{gate}")
    g = state.get("gates", {}).get("final", {})
    if g.get("status") == "awaiting_approval":
        d = g.get("details", {})
        lines.append(f"  final gate: {g.get('reason')}")
        lines.append(f"    motion_ok={d.get('motion_ok')} review_ok={d.get('review_ok')} "
                     f"under_budget={d.get('under_budget')} "
                     f"(est {d.get('est_runtime_sec')}s ≤ {d.get('render_budget_sec')}s)")
        lines.append(f"  → resume: python -m studio.run produce --resume {state.get('slug')} --approve final")
    if state.get("status") == "blocked_at_factcheck":
        lines.append("  → fix the script, then: "
                     f"python -m studio.run produce --resume {state.get('slug')} --approve factcheck")
    if state.get("artifacts", {}).get("video"):
        lines.append(f"  ✓ deliverable: {state['artifacts']['video']}")
    return "\n".join(lines)


def _cmd_packs(args: argparse.Namespace) -> int:
    """Handle ``packs`` — list (default), ``--validate <id>``, or ``--show <id>``."""
    from studio import packs
    from studio.packs.validate import validate_pack

    if args.validate:
        try:
            result = validate_pack(args.validate)
        except packs.PackError as exc:
            print(f"✗ {args.validate}: {exc}")
            return 2
        status = "✓ OK" if result.ok else "✗ INVALID"
        print(f"{status}  pack '{result.pack_id}'")
        for name, passed in result.checks.items():
            print(f"    {'✓' if passed else '✗'} {name}")
        for err in result.errors:
            print(f"    ✗ {err}")
        for warn in result.warnings:
            print(f"    ! {warn}")
        return 0 if result.ok else 1

    if args.show:
        try:
            pack = packs.load_pack(args.show)
        except packs.PackError as exc:
            print(f"✗ {args.show}: {exc}")
            return 2
        print(f"{pack.id}  —  {pack.name}")
        print(f"  dir:      {pack.dir}")
        print(f"  fps:      {pack.fps}")
        print(f"  colors:   {', '.join(pack.colors)}")
        print("  partials:")
        for name, path in sorted(pack.partials.items()):
            shared = " (shared)" if "_shared/" in path.as_posix() else ""
            print(f"    {name:<12} {path}{shared}")
        return 0

    # default: list registered packs
    entries = packs.list_packs()
    if not entries:
        print("no packs registered")
        return 0
    print(f"{len(entries)} pack(s):")
    for e in entries:
        print(f"  {e.id:<20} {e.name}")
        if e.blurb:
            print(f"  {'':<20} {e.blurb}")
    return 0


def _cmd_assets(args: argparse.Namespace) -> int:
    """Handle ``assets list|resolve`` — delegate to studio.library. TODO(phase-2)."""
    raise NotImplementedError("studio.run assets — phase 2")


def _cmd_library(args: argparse.Namespace) -> int:
    """Handle ``library`` — status (default), ``--list``, ``--add <file>``, ``--gc``."""
    from studio import library

    if args.add:
        if not args.tags:
            print("--add requires --tags")
            return 2
        try:
            entry = library.add(
                args.add,
                kind=args.kind or _infer_kind(args.add),
                tags=args.tags,
                license=args.license,
                attribution=args.attribution,
                source=args.source,
                provenance=args.provenance,
                recolorable=args.recolorable,
            )
        except library.LibraryError as exc:
            print(f"✗ add failed: {exc}")
            return 2
        print(f"✓ {entry['id']}  ({entry['kind']})  {entry['file']}  [{entry['license']}]")
        return 0

    if args.gc:
        summary = library.gc()
        print(f"gc: removed {len(summary['removed_entries'])} entries, {len(summary['removed_files'])} orphan files")
        for i in summary["removed_entries"]:
            print(f"  - entry {i}")
        for f in summary["removed_files"]:
            print(f"  - file  {f}")
        return 0

    if args.list:
        rows = library.list_assets(kind=args.kind, tags=args.tags)
        if not rows:
            print("no matching assets")
            return 0
        print(f"{len(rows)} asset(s):")
        for e in rows:
            tags = ",".join(e.get("tags", []))
            print(f"  {e['id']:<28} {e['kind']:<8} [{e['license']:<14}] {tags}")
        return 0

    # default: status
    st = library.library_status()
    print(f"asset-library: {st['total']} asset(s)")
    for kind, n in sorted(st["by_kind"].items()):
        print(f"  {kind:<8} {n}")
    if st["missing_files"]:
        print(f"  ! missing files: {', '.join(st['missing_files'])}")
    if st["unknown_license"]:
        print(f"  ! unknown license: {', '.join(st['unknown_license'])}")
    return 0


def _infer_kind(path: str) -> str:
    """Best-effort kind from a file extension (used by `library --add`)."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "ttf": "font", "otf": "font", "woff": "font", "woff2": "font",
        "svg": "icon", "json": "lottie", "mp3": "sfx", "wav": "sfx",
        "jpg": "img", "jpeg": "img", "png": "img", "webp": "img",
    }.get(ext, "img")


def main(argv: list[str] | None = None) -> int:
    """Parse argv and dispatch to the selected subcommand handler."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
