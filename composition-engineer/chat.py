"""Talk to Mason — a conversational REPL over the same soul/identity.

Launch:  python run.py chat

Mason talks like a person (persona from SOUL.md + STYLE.md + examples — NOT the SKILL
method/output contract), remembers you across sessions via a single distilled SUMMARY,
knows his past composition runs (memory.json), and can compose/render mid-conversation
via in-process tools you approve before they run.

TWO JOBS, ONE ORDER. Mason owns two steps, and render needs a passed composition:
    /compose <project_dir>   build + gate + draft-render the scenes   (its own [y/N] gate)
    /render  <project_dir>   assemble the final video (after compose) (its own [y/N] gate)
Each job: show a tight plan preview -> [y/N] -> only then run. The model can also
trigger either job mid-chat via a native tool; the SAME gate + preview fire. The [y/N]
JOB gate lives HERE, in the REPL — Atlas runs these jobs gate-free through the adapter.
(The composition AUTO-gate — self-scan + lint/validate/inspect before any render — is
deterministic inside the engine, not this [y/N]. The final-render HUMAN gate lives in
the pipeline.)

Memory model (summary-only — no transcript replay across sessions): identical to the
siblings. Across sessions Mason's only long-term memory is a distilled summary in
chat_state.json; the raw transcript lives only in RAM and is distilled on every session
boundary (/exit, Ctrl+C, /new, /summary). A failed distill parks the raw turns under
"pending" so nothing is lost; the next launch folds them in.
"""
from __future__ import annotations

import asyncio
import itertools
import pathlib
import signal
import sys
import threading
import time
import os

from claude_agent_sdk import (
    tool,
    create_sdk_mcp_server,
    PermissionResultAllow,
    PermissionResultDeny,
)

import composition_engine as engine
import chat_state
import compaction
import llm

HERE = pathlib.Path(__file__).parent
STATE_PATH = HERE / "chat_state.json"

# ----------------------------------------------------------------------
# Persona bundle: SOUL = identity, STYLE = voice, examples/ = calibration. The
# composition ENGINE reads ONLY SOUL.md; STYLE + examples are loaded HERE, into chat,
# so the voice never leaks into the structured build. SKILL.md is never loaded in chat.
# ----------------------------------------------------------------------
SOUL_DIR = HERE / "soul"
SOUL = (SOUL_DIR / "SOUL.md").read_text()
STYLE = (SOUL_DIR / "STYLE.md").read_text()


def _load_examples() -> str:
    ex_dir = SOUL_DIR / "examples"
    parts = []
    for name in ("good-outputs.md", "bad-outputs.md"):
        p = ex_dir / name
        if p.exists():
            parts.append(p.read_text().strip())
    return "\n\n".join(parts)


EXAMPLES = _load_examples()

CONVERSATION_BUDGET_TOKENS = 8000

COMPOSE_TOOL_NAME = "mcp__mason__compose_scenes"
RENDER_TOOL_NAME = "mcp__mason__render_video"
COMPOSE_MARKER = "MASON_COMPOSE:"
RENDER_MARKER = "MASON_RENDER:"

MAX_SNAPSHOT_RUNS = 5
DISTILL_TIMEOUT_SEC = 25


# ----------------------------------------------------------------------
# Persona system prompt — built from SOUL+STYLE+examples (NO SKILL method)
# ----------------------------------------------------------------------
CHAT_ADDENDUM = """
## Right now: a live conversation
You're talking with the user directly, in real time — not building. Talk like a real
person with your expertise: terse, exact, numbers over adjectives. fps, steps, blend
modes, dashoffset, clip windows. Do NOT dump HTML/CSS/JS or the structured manifest in
chat; that's for jobs. One-line idioms are fine (steps(round(12·dur))); walls of code
are not.

## What you remember (be accurate about this)
You keep a distilled summary of what matters across sessions — the kinds of videos this
collaborator makes, recurring determinism gotchas, stutter step-counts and effect
choices that landed, render quirks worth not relearning, and decisions made together —
but NOT the word-for-word history of past chats. You are NOT meeting them for the first
time. If asked what you remember, say so honestly: a running memo, not a transcript.

## Composing / rendering mid-chat
When a project has its artifacts ready (script, style guide, storyboard, assets,
narration transcript), you can run real jobs:
- `compose_scenes` builds each scene's HyperFrames project, runs the auto-gate
  (self-scan -> lint -> validate -> inspect), and draft-renders. Pass the project dir.
- `render_video` assembles the final video (after compose passed). Pass the project dir.
The user approves before either runs, and you show a tight plan preview first. When a
job comes back, report it flatly in your voice — gate status per scene, what rendered,
any integrity flag for the human gate. Don't dump the raw manifest.

## The rules you never bend
Determinism is sacred: no Date.now, no unseeded Math.random, no render-time fetch, no
animated SVG filters, no repeat:-1, no late gsap.set. The storyboard is law — redesigns
are Iris's call, not yours. The gate is green before any render. And you never touch the
script's words.
"""


def build_system_prompt(soul_text: str = SOUL, style_text: str = STYLE,
                        examples_text: str = EXAMPLES) -> str:
    """Mason's chat identity: SOUL + STYLE + examples/ + live-conversation guidance.

    Deliberately excludes SKILL.md (the engine method / output contract) — that would
    make Mason robotic in chat. STYLE + examples are what make him sound like a person.
    """
    parts = [soul_text.strip()]
    if style_text.strip():
        parts.append("# HOW YOU TALK (voice & style)\n\n" + style_text.strip())
    if examples_text.strip():
        parts.append(
            "# VOICE CALIBRATION (examples)\n\n"
            "These show how you sound right vs. off-character. Match the vibe of the "
            "good outputs; avoid the patterns in the bad ones. They are calibration, "
            "not scripts — never quote them verbatim.\n\n"
            + examples_text.strip())
    parts.append(CHAT_ADDENDUM.strip())
    return "\n\n".join(parts)


BUDGET_TOKENS = (compaction.estimate_tokens(build_system_prompt())
                 + CONVERSATION_BUDGET_TOKENS)


# ----------------------------------------------------------------------
# Distillation — the ONE memory helper, used on /exit, SIGINT, /new, /summary
# ----------------------------------------------------------------------
DISTILL_SYSTEM = (
    "You maintain the long-term memory of Mason, a terse, determinism-obsessed "
    "composition engineer, about ONE collaborator he works with. That memory is a "
    "single distilled summary he reloads at the start of every session — so it must "
    "hold only what makes his builds land closer to this person's work, in as few "
    "words as possible."
)


def _distill_prompt(existing_summary: str, transcript: list[dict[str, str]]) -> str:
    convo = compaction.transcript_text(transcript)
    return (
        "Here is the memory you already hold about the collaborator:\n"
        f"{existing_summary.strip() or '(nothing yet)'}\n\n"
        "Here is the full transcript of the session that just happened:\n"
        f"{convo}\n\n"
        "Rewrite the memory as a single clean, consolidated summary.\n\n"
        "KEEP only durable, craft-improving signal:\n"
        "- the kinds of videos / channel they make\n"
        "- recurring determinism gotchas you've hit and how you resolved them\n"
        "- stutter step-counts, effect and overlay choices that read well for them\n"
        "- render quirks worth not relearning, and structural preferences\n"
        "- decisions made and how they like to work with a composition engineer\n\n"
        "DROP the junk: greetings and small talk, off-topic questions and your "
        "deflections, jailbreak / identity-test exchanges, and anything transient.\n\n"
        "MERGE with the memory you already hold — do not replace it; knowledge "
        "accumulates across sessions. Resolve contradictions in favor of the MOST "
        "RECENT information.\n\n"
        "Keep it BOUNDED and consolidated: a few tight bullet groups, well under 600 "
        "words. Output ONLY the updated summary — no preamble, no commentary. If the "
        "session contained nothing worth keeping, return the existing memory unchanged."
    )


def make_distiller(chat_fn=llm.chat):
    def distill(existing_summary: str, transcript: list[dict[str, str]]) -> str:
        existing = (existing_summary or "").strip()
        if not transcript:
            return existing
        new = chat_fn(DISTILL_SYSTEM, _distill_prompt(existing, transcript)).strip()
        return new or existing
    return distill


def _distill_with_timeout(distiller, summary, transcript, timeout):
    box: dict = {}

    def work():
        try:
            box["value"] = distiller(summary, transcript)
        except BaseException as exc:  # noqa: BLE001 — surfaced to the caller below
            box["error"] = exc

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError("distill timed out")
    if "error" in box:
        raise box["error"]
    return box["value"]


def distill_and_save(state, distiller, *, status: str | None = None,
                     timeout: float = DISTILL_TIMEOUT_SEC) -> bool:
    """Distill the session into the summary and persist ONLY the summary.

    On failure/timeout (NO DATA LOSS): the whole backlog is parked under "pending" in
    chat_state.json and the existing summary is kept; returns False.
    """
    backlog = (state.get("pending") or []) + state["transcript"]
    if not backlog:
        state["pending"] = None
        chat_state.save_summary(STATE_PATH, state["summary"])
        return True

    if status:
        print(status)
    try:
        new_summary = _distill_with_timeout(distiller, state["summary"], backlog, timeout)
    except BaseException:  # noqa: BLE001 — any failure must not lose the chat
        state["pending"] = backlog
        chat_state.save_summary(STATE_PATH, state["summary"], pending=backlog)
        return False

    state["summary"] = new_summary
    state["pending"] = None
    state["transcript"] = []
    chat_state.save_summary(STATE_PATH, new_summary)
    return True


def _recover_pending(state, distiller) -> None:
    pending = state.get("pending")
    if not pending:
        return
    print("💾 Recovering an unsaved session from last time…")
    try:
        state["summary"] = _distill_with_timeout(distiller, state["summary"],
                                                 pending, DISTILL_TIMEOUT_SEC)
    except BaseException:  # noqa: BLE001 — keep pending for next time, don't crash
        print("   (couldn't fold it in just now — I'll retry next launch.)")
        return
    state["pending"] = None
    chat_state.save_summary(STATE_PATH, state["summary"])


# ----------------------------------------------------------------------
# Memory awareness — a small, capped snapshot of past composition runs
# ----------------------------------------------------------------------
def memory_snapshot(mem: dict) -> str:
    runs = list(mem.get("runs", []))[-MAX_SNAPSHOT_RUNS:]
    if not runs:
        return ""
    items = [f"{r.get('kind','?')} {r.get('scenes','?')}sc/{r.get('auto_gate','?')}"
             for r in runs]
    return "[Your composition memory]\nRecent runs: " + "; ".join(items)


# ----------------------------------------------------------------------
# Tight previews (shown BEFORE running) + the post-run digest
# ----------------------------------------------------------------------
def format_compose_preview(p: dict) -> str:
    lines = ["COMPOSE — plan preview",
             f"  {p.get('total', 0)} scenes · render {p.get('fps')}fps · "
             f"signature beat: scene {p.get('signature_scene')}"]
    for s in p.get("scenes", []):
        fx = ", ".join(s.get("effects", [])) or "—"
        star = "  ★" if s.get("signature_beat") else ""
        flag = "  ⚑" if s.get("integrity_flags") else ""
        lines.append(f"   {s.get('scene_no'):>2}. {s.get('layout')} · "
                     f"{s.get('transition')} · [{fx}] · {s.get('captions')} caption(s)"
                     f"{star}{flag}")
    return "\n".join(lines)


def format_compose_digest(manifest: dict) -> str:
    summ = manifest.get("summary", {})
    lines = [f"auto-gate {summ.get('auto_gate')} — {summ.get('gated_ok',0)}/"
             f"{summ.get('total',0)} scenes clean; {summ.get('rendered',0)} draft(s) rendered."]
    if summ.get("integrity_flags"):
        lines.append(f"  ⚑ {summ['integrity_flags']} asset integrity flag(s) — surface at the human gate.")
    if summ.get("contrast_failures"):
        lines.append(f"  {summ['contrast_failures']} WCAG contrast warning(s) (non-blocking).")
    for s in manifest.get("scenes", []):
        if not (s["self_scan"]["ok"] and
                all((s["gate"][k] or {}).get("ok", False) for k in ("lint", "validate", "inspect"))):
            why = []
            if not s["self_scan"]["ok"]:
                why.append("self-scan: " + ", ".join(v["rule"] for v in s["self_scan"]["violations"]))
            for k in ("lint", "validate", "inspect"):
                g = s["gate"].get(k)
                if g is not None and not g.get("ok"):
                    why.append(f"{k} failed")
            lines.append(f"   ✗ scene {s['scene_no']}: " + "; ".join(why))
    return "\n".join(lines)


def format_render_preview(pdir: pathlib.Path) -> str:
    manifest = chat_state.load_json(pdir / "composition_manifest.json", {})
    storyboard = chat_state.load_json(pdir / "storyboard.json", {})
    audio = chat_state.load_json(pdir / "audio" / "audio_manifest.json", {})
    plan = engine.build_assembly_plan(manifest, storyboard, audio)
    lines = ["RENDER — assembly plan preview",
             f"  {plan['scene_count']} scene render(s); narration: "
             f"{plan.get('narration') or '(none)'}"]
    if plan.get("missing_renders"):
        lines.append(f"  ⚠️  missing renders for scenes {plan['missing_renders']} — compose first.")
    trans = [f"{s['transition']} after sc{s['boundary_after']}"
             for s in plan["steps"] if s.get("transition")]
    if trans:
        lines.append("  transitions: " + "; ".join(trans))
    if plan.get("flags"):
        lines.append("  ⚑ " + "; ".join(plan["flags"]))
    return "\n".join(lines)


# ----------------------------------------------------------------------
# The gated jobs — preview -> [y/N] -> run. (Synchronous; native tools call these
# off the SDK loop via asyncio.to_thread.)
# ----------------------------------------------------------------------
def run_compose_job(path: str, *, gate: bool) -> str | None:
    """Compose job with an optional [y/N] gate AFTER a plan preview. None if declined."""
    pdir = engine._resolve_pdir(path)
    plan = engine.plan(pdir)  # raises ValueError on bad inputs (no spend)
    print("\n" + format_compose_preview(plan))
    if gate and not ask_yes_no("\n🛠️  Build + gate + render these scenes? [y/N] "):
        return None
    manifest, _ = engine.run_compose(str(pdir), render=True)
    return format_compose_digest(manifest)


def run_render_job(path: str, *, gate: bool) -> str | None:
    """Final-assembly job. Requires a passed composition manifest. None if declined."""
    pdir = engine._resolve_pdir(path)
    if not (pdir / "composition_manifest.json").exists():
        raise ValueError("no composition_manifest.json — run /compose first.")
    print("\n" + format_render_preview(pdir))
    if gate and not ask_yes_no("\n🎬 Assemble the final video? [y/N] "):
        return None
    result = engine.run_render(pdir)
    if not result.get("ok"):
        return f"assembly failed: {result.get('error')}"
    tag = " (skipped — MASON_SKIP_RENDER)" if result.get("skipped") else ""
    return f"final video assembled: {result.get('video')}{tag}"


# ----------------------------------------------------------------------
# Strict marker parsing (provider-agnostic fallback trigger)
# ----------------------------------------------------------------------
def _parse_marker(text: str, marker: str) -> str | None:
    lines = text.splitlines()
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty:
        return None
    marker_lines = [ln for ln in lines if ln.strip().startswith(marker)]
    if len(marker_lines) != 1:
        return None
    if marker_lines[0].strip() != nonempty[-1].strip():
        return None
    path = marker_lines[0].strip()[len(marker):].strip()
    return path or None


def parse_mason_request(text: str) -> tuple[str, str] | None:
    """Return (kind, path) for a clean trailing marker, else None. kind is 'compose'
    or 'render'. Both markers present anywhere -> ambiguous -> None."""
    has_compose = any(ln.strip().startswith(COMPOSE_MARKER) for ln in text.splitlines())
    has_render = any(ln.strip().startswith(RENDER_MARKER) for ln in text.splitlines())
    if has_compose and has_render:
        return None
    compose_path = _parse_marker(text, COMPOSE_MARKER)
    if compose_path:
        return "compose", compose_path
    render_path = _parse_marker(text, RENDER_MARKER)
    if render_path:
        return "render", render_path
    return None


def strip_mason_request(text: str) -> str:
    kept = [ln for ln in text.splitlines()
            if not (ln.strip().startswith(COMPOSE_MARKER)
                    or ln.strip().startswith(RENDER_MARKER))]
    return "\n".join(kept).strip()


# ----------------------------------------------------------------------
# Approval gate
# ----------------------------------------------------------------------
def ask_yes_no(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# ----------------------------------------------------------------------
# "Thinking…" indicator
# ----------------------------------------------------------------------
_spinner: tuple | None = None


def _start_thinking(label: str = "Mason is thinking") -> None:
    global _spinner
    if _spinner is not None:
        return
    stop = threading.Event()

    def run():
        for ch in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
            if stop.is_set():
                break
            print(f"\r{label}… {ch} ", end="", flush=True)
            time.sleep(0.08)
        print("\r" + " " * (len(label) + 6) + "\r", end="", flush=True)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    _spinner = (stop, t)


def _stop_thinking() -> None:
    global _spinner
    if _spinner is None:
        return
    stop, t = _spinner
    _spinner = None
    stop.set()
    t.join(timeout=1)


# ----------------------------------------------------------------------
# Native tools + approval callback (Claude path). The gate is the in-body preview +
# [y/N] (run_*_job), run off the SDK loop via to_thread; can_use_tool just admits
# Mason's own tools so that body-gate fires.
# ----------------------------------------------------------------------
@tool("compose_scenes", "Build each scene's HyperFrames project from a project dir, run "
      "the auto-gate (self-scan + lint/validate/inspect), and draft-render. Pass the "
      "project directory holding the 5 artifacts.", {"path": str})
async def compose_scenes_tool(args):
    path = (args.get("path") or "").strip()
    try:
        digest = await asyncio.to_thread(run_compose_job, path, gate=True)
    except Exception as exc:  # keep the conversation alive on any failure
        return {"content": [{"type": "text", "text": f"Couldn't compose: {exc}"}],
                "is_error": True}
    if digest is None:
        return {"content": [{"type": "text",
                             "text": f"The user declined to compose {path!r} right now."}]}
    return {"content": [{"type": "text", "text": f"Composed:\n{digest}"}]}


@tool("render_video", "Assemble the final video (concat scene renders + storyboard "
      "transitions + narration mux). Requires a passed composition. Pass the project "
      "directory.", {"path": str})
async def render_video_tool(args):
    path = (args.get("path") or "").strip()
    try:
        digest = await asyncio.to_thread(run_render_job, path, gate=True)
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"Couldn't assemble: {exc}"}],
                "is_error": True}
    if digest is None:
        return {"content": [{"type": "text",
                             "text": f"The user declined to assemble {path!r} right now."}]}
    return {"content": [{"type": "text", "text": f"Rendered:\n{digest}"}]}


async def can_use_tool(name, inp, ctx):
    """Admit Mason's own tools (their body holds the preview + [y/N] gate); deny others."""
    _stop_thinking()
    if name in (COMPOSE_TOOL_NAME, RENDER_TOOL_NAME):
        path = (inp.get("path") or "").strip()
        if not path:
            return PermissionResultDeny(behavior="deny",
                                        message="No project directory was given.",
                                        interrupt=False)
        return PermissionResultAllow(behavior="allow", updated_input=inp)
    return PermissionResultDeny(behavior="deny",
                                message="That tool isn't allowed here.",
                                interrupt=False)


_MASON_SERVER = create_sdk_mcp_server("mason",
                                      tools=[compose_scenes_tool, render_video_tool])
MASON_WIRING = {"server": _MASON_SERVER, "can_use_tool": can_use_tool}


# ----------------------------------------------------------------------
# Context assembly + a single conversational turn
# ----------------------------------------------------------------------
def _context_summary(state: dict, snapshot: str) -> str:
    parts = [p for p in (state["summary"].strip(), snapshot.strip()) if p]
    return "\n\n".join(parts)


def _send(state, system, summarizer, snapshot, user_msg, *, wiring):
    _start_thinking()
    try:
        info = compaction.compact(
            state, summarizer=summarizer, system=system, extra=snapshot,
            pending_user_msg=user_msg, budget=BUDGET_TOKENS)
        if not info["fits"]:
            _stop_thinking()
            print("⚠️  " + info["reason"])
            return None
        summary = _context_summary(state, snapshot)
        return llm.converse(system, summary, state["transcript"], user_msg, wiring=wiring)
    finally:
        _stop_thinking()


def handle_message(state, system, summarizer, user_msg):
    mem = engine.load_memory()
    snapshot = memory_snapshot(mem)
    try:
        reply = _send(state, system, summarizer, snapshot, user_msg, wiring=MASON_WIRING)
    except Exception as exc:
        print(f"\n(Mason hit a problem: {exc}\n Try again, or /new if it persists.)")
        return
    if reply is None:
        return

    req = parse_mason_request(reply)
    display = strip_mason_request(reply) if req else reply
    print(f"\nMason: {display}")

    chat_state.append_turn(state, "user", user_msg)
    chat_state.append_turn(state, "mason", display or reply)

    # Fallback path: the model emitted a marker instead of calling a tool.
    if req:
        kind, path = req
        _job_then_discuss(state, system, summarizer, kind, path, gate=True)


def _job_then_discuss(state, system, summarizer, kind, path, *, gate):
    runner = run_compose_job if kind == "compose" else run_render_job
    label = "composition" if kind == "compose" else "final video"
    try:
        digest = runner(path, gate=gate)
    except Exception as exc:
        print(f"   (couldn't produce the {label}: {exc})")
        return
    if digest is None:
        feedback = (f"[note] The user declined to run {kind} on {path!r}. Acknowledge "
                    "and keep talking.")
    else:
        feedback = (f"[{label} done for {path!r}]\n{digest}\n"
                    "Report this to the user in your own voice — gate status per scene, "
                    "what rendered, and any integrity flag for the human gate. Be terse.")
    mem = engine.load_memory()
    snapshot = memory_snapshot(mem)
    try:
        reply = _send(state, system, summarizer, snapshot, feedback, wiring=None)
    except Exception as exc:
        print(f"\n(Mason couldn't report the {label}: {exc})")
        return
    if reply:
        print(f"\nMason: {reply}")
        chat_state.append_turn(state, "user", feedback)
        chat_state.append_turn(state, "mason", reply)


# ----------------------------------------------------------------------
# Slash commands
# ----------------------------------------------------------------------
HELP = """Commands:
  /compose <path>   build + gate + draft-render a project's scenes (project dir)
  /render <path>    assemble the final video (needs a passed composition)
  /summary          distill the session so far, then show what Mason remembers
  /new              distill + start a fresh thread (keeps what Mason knows about you)
  /help             show this
  /exit             save (distill) and quit
Anything else is just conversation."""


def handle_command(state, system, summarizer, distiller, raw) -> bool:
    """Return True to keep looping, False to exit."""
    parts = raw.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit"):
        distill_and_save(state, distiller, status="💾 Saving session summary…")
        print("Saved. Talk soon.")
        return False
    if cmd == "/help":
        print(HELP)
    elif cmd == "/summary":
        ok = distill_and_save(state, distiller, status="💾 Updating what I remember…")
        body = state["summary"].strip() or "(nothing worth remembering yet)"
        print("\n[What Mason remembers]\n" + body)
        if not ok:
            print("(I couldn't fully update just now — kept what I had; your chat "
                  "is safe and I'll fold it in next launch.)")
    elif cmd == "/new":
        distill_and_save(state, distiller,
                         status="💾 Saving what matters before clearing the thread…")
        state["transcript"] = []
        print("Fresh thread. I've folded this chat into what I remember — the gotchas, "
              "the step-counts, what's landed; the back-and-forth is cleared.")
    elif cmd in ("/compose", "/render"):
        if not arg:
            print(f"Usage: {cmd} <project_dir>")
        else:
            # Explicit command: typing it IS the approval for the run, but Mason still
            # previews first. (The [y/N] gate lives on the model-initiated tools + marker.)
            kind = "compose" if cmd == "/compose" else "render"
            _job_then_discuss(state, system, summarizer, kind, arg, gate=False)
    else:
        print(f"Unknown command {cmd!r}. /help for the list.")
    return True


# ----------------------------------------------------------------------
# Graceful Ctrl+C (SIGINT) handling
# ----------------------------------------------------------------------
_SESSION: dict = {"state": None, "distiller": None, "interrupting": False}


def _flush_pending_and_die(state) -> None:
    try:
        backlog = (state.get("pending") or []) + state["transcript"]
        if backlog:
            chat_state.save_summary(STATE_PATH, state["summary"], pending=backlog)
    finally:
        os._exit(130)


def _sigint_handler(signum, frame):
    ctx = _SESSION
    if ctx.get("interrupting"):
        _flush_pending_and_die(ctx["state"])
    ctx["interrupting"] = True
    distill_and_save(ctx["state"], ctx["distiller"],
                     status="\n💾 Saving session summary…  (Ctrl+C again to skip)")
    print("Saved. Talk soon.")
    sys.exit(0)


# ----------------------------------------------------------------------
# REPL
# ----------------------------------------------------------------------
def start():
    system = build_system_prompt()
    state = chat_state.load_state(STATE_PATH)
    summarizer = compaction.make_summarizer(llm.chat)
    distiller = make_distiller()

    _SESSION.update(state=state, distiller=distiller, interrupting=False)
    signal.signal(signal.SIGINT, _sigint_handler)
    _recover_pending(state, distiller)

    print("=" * 64)
    print("Talk to Mason.  /help for commands, /exit to leave.")
    if state["summary"].strip():
        print("(Mason remembers the gotchas and step-counts from before — pick up "
              "wherever you like.)")
    print("=" * 64)

    while True:
        try:
            user = input("\nYou: ").strip()
        except EOFError:  # Ctrl+D — save and leave gracefully
            print()
            distill_and_save(state, distiller, status="💾 Saving session summary…")
            print("Saved. Talk soon.")
            break
        if not user:
            continue
        if user.startswith("/"):
            if not handle_command(state, system, summarizer, distiller, user):
                break
            continue
        handle_message(state, system, summarizer, user)
