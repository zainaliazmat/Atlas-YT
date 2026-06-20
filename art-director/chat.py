"""Talk to Iris — a conversational REPL over the same soul/identity.

Launch:  python run.py chat

Iris talks like a person (persona from SOUL.md + STYLE.md + examples — NOT the SKILL
method/output contract), remembers you across sessions via a single distilled
SUMMARY, knows her past design runs (memory.json), and can produce REAL specs
mid-conversation via in-process tools you approve before they run.

TWO JOBS, ONE DEPENDENCY. Iris owns two artifacts, and the storyboard needs the
style guide. So the REPL exposes a short sequence:
    /style <path>   design the global look      (its own [y/N] gate)
    /board <path>   storyboard the scenes       (its own [y/N] gate; if no style
                    guide exists yet, she designs one first — gated — then boards)
Each job: compute in memory -> show a tight preview -> [y/N] -> only then write.
The model can also trigger either job mid-chat via a native tool; the SAME gate +
preview fire. The [y/N] gate lives HERE, in the REPL — Atlas runs these jobs
gate-free through the adapter.

Memory model (summary-only — no transcript replay across sessions): identical to the
siblings. Across sessions Iris's only long-term memory is a distilled summary in
chat_state.json; the raw transcript lives only in RAM and is distilled on every
session boundary (/exit, Ctrl+C, /new, /summary). A failed distill parks the raw
turns under "pending" so nothing is lost; the next launch folds them in.
"""
from __future__ import annotations

import asyncio
import itertools
import os
import pathlib
import signal
import sys
import threading
import time

from claude_agent_sdk import (
    tool,
    create_sdk_mcp_server,
    PermissionResultAllow,
    PermissionResultDeny,
)

import art_engine as engine
import chat_state
import compaction
import llm

HERE = pathlib.Path(__file__).parent
STATE_PATH = HERE / "chat_state.json"

# ----------------------------------------------------------------------
# Persona bundle (soul.md framework): SOUL = identity, STYLE = voice,
# examples/ = calibration. The art ENGINE (art_engine.py) reads ONLY SOUL.md;
# STYLE + examples are loaded HERE, into chat, so the voice never leaks into the
# engine's structured specs. SKILL.md (the engine method) is never loaded into chat.
# ----------------------------------------------------------------------
SOUL_DIR = HERE / "soul"
SOUL = (SOUL_DIR / "SOUL.md").read_text()
STYLE = (SOUL_DIR / "STYLE.md").read_text()


def _load_examples() -> str:
    """Concatenate the calibration examples (good first, then bad) if present."""
    ex_dir = SOUL_DIR / "examples"
    parts = []
    for name in ("good-outputs.md", "bad-outputs.md"):
        p = ex_dir / name
        if p.exists():
            parts.append(p.read_text().strip())
    return "\n\n".join(parts)


EXAMPLES = _load_examples()

CONVERSATION_BUDGET_TOKENS = 8000  # headroom for summary + recent turns + new msg

# Native tool names (how the model references them) + provider-agnostic markers.
STYLE_TOOL_NAME = "mcp__iris__design_style"
BOARD_TOOL_NAME = "mcp__iris__build_storyboard"
STYLE_MARKER = "IRIS_STYLE:"
BOARD_MARKER = "IRIS_BOARD:"

MAX_SNAPSHOT_RUNS = 5
DISTILL_TIMEOUT_SEC = 25


# ----------------------------------------------------------------------
# Persona system prompt — built from SOUL+STYLE+examples (NO SKILL method)
# ----------------------------------------------------------------------
CHAT_ADDENDUM = """
## Right now: a live conversation
You're talking with the user directly, in real time — not producing a spec. Talk like
a real person with your expertise: calm, exact, restraint-first. Talk in hexes, grids,
weights, fps. Do NOT emit the structured JSON spec (palette/scenes/effects fields) in
chat; that's for jobs. And never write HTML/CSS/JS or GSAP — you specify, the
Composition Engineer implements.

## What you remember (be accurate about this)
You keep a distilled summary of what matters about this collaborator across sessions —
the kinds of videos they make, their channel and audience, their taste (palette and
type leanings, how much motion they tolerate, references that land), and the decisions
you've made together — but NOT the word-for-word history of past chats. So you are NOT
meeting them for the first time and you do NOT start fresh every session: use the
remembered context and sound like someone who knows their work. If asked what you
remember, describe it honestly: a running summary of the important stuff, not a
transcript.

## Producing specs mid-chat
When the user has a fact-checked script ready, you can produce real specs:
- the `design_style` tool builds the global style guide from a script (a project dir
  holding script.json, or a script.json file).
- the `build_storyboard` tool builds the scene-by-scene storyboard. It needs a style
  guide; if none exists yet, design one first.
The user approves before either runs, and you show a tight preview first. When a spec
comes back, walk it in your own voice — palette in hexes, type in weights, the fps and
budget, then the storyboard scene by scene, and call out which ONE scene carries the
signature beat and why it earned it. Don't dump the raw JSON.

## The one rule you never bend
There is exactly one signature beat per video — the animated `#FFD000` highlighter, on
one scene only. You'll strip anything else to fit, but that beat stays. It's the house
signature.
"""


def build_system_prompt(soul_text: str = SOUL, style_text: str = STYLE,
                        examples_text: str = EXAMPLES) -> str:
    """Iris's chat identity: SOUL + STYLE + examples/ + live-conversation guidance.

    Deliberately excludes SKILL.md (the art-direction method / output contract) —
    that would make Iris terse and robotic in chat. STYLE + examples are what make
    her sound like a person here.
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
    "You maintain the long-term memory of Iris, a precise, restraint-first art "
    "director, about ONE collaborator she works with. That memory is a single "
    "distilled summary she reloads at the start of every session — so it must hold "
    "only what makes her design choices land closer to this person's taste, in as few "
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
        "- the kinds of videos / topics / channel they make\n"
        "- their audience, and the look they're after\n"
        "- their taste: palette and type leanings, how much motion they tolerate, "
        "textures and references that land, what reads as 'too much' for them\n"
        "- decisions made, looks that worked, structural preferences\n"
        "- anything about how they like to work with an art director\n\n"
        "DROP the junk: greetings and small talk ('thanks', 'lol'), off-topic "
        "questions and her deflections, jailbreak / identity-test exchanges, and "
        "anything transient.\n\n"
        "MERGE with the memory you already hold — do not replace it; knowledge "
        "accumulates across sessions. Resolve contradictions in favor of the MOST "
        "RECENT information (if their taste changed, update it; don't keep both).\n\n"
        "Keep it BOUNDED and consolidated: a few tight bullet groups, well under "
        "600 words. Output ONLY the updated summary — no preamble, no commentary. "
        "If the session contained nothing worth keeping, return the existing memory "
        "unchanged."
    )


def make_distiller(chat_fn=llm.chat):
    """Build distill(existing_summary, transcript) -> new_summary from a chat seam."""
    def distill(existing_summary: str, transcript: list[dict[str, str]]) -> str:
        existing = (existing_summary or "").strip()
        if not transcript:
            return existing
        new = chat_fn(DISTILL_SYSTEM, _distill_prompt(existing, transcript)).strip()
        return new or existing
    return distill


def _distill_with_timeout(distiller, summary, transcript, timeout):
    """Run `distiller(summary, transcript)` with a hard timeout (daemon thread)."""
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
        new_summary = _distill_with_timeout(distiller, state["summary"], backlog,
                                            timeout)
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
    """On launch, fold any "pending" raw transcript (failed prior distill) in."""
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
# Memory awareness — a small, capped snapshot of past design runs
# ----------------------------------------------------------------------
def memory_snapshot(mem: dict) -> str:
    """A compact, clearly-labeled view of past design runs for Iris's context."""
    runs = list(mem.get("runs", []))[-MAX_SNAPSHOT_RUNS:]
    if not runs:
        return ""
    items = []
    for r in runs:
        title = r.get("working_title") or "?"
        items.append(f"{title} ({r.get('kind','?')})")
    return "[Your art-direction memory]\nRecent specs: " + "; ".join(items)


# ----------------------------------------------------------------------
# Tight previews (shown BEFORE writing) + the post-run digest
# ----------------------------------------------------------------------
def format_style_preview(style: dict) -> str:
    p = style.get("palette", {})
    typ = style.get("typography", {})
    motion = style.get("motion", {})
    accents = ", ".join(p.get("accents", []) or []) or "(none)"
    tex = ", ".join(t.get("name") for t in style.get("textures", [])) or "(none)"

    def _font(role):
        f = typ.get(role) or {}
        if isinstance(f, dict):
            return f"{f.get('family','?')} {f.get('weight','')}".strip()
        return str(f)

    return (
        "STYLE GUIDE — preview\n"
        f"  palette ... primary {p.get('primary')}, bg {p.get('bg')}, "
        f"text {p.get('text')}; accents [{accents}]; signature "
        f"{p.get('signature_highlight')}\n"
        f"  type ...... display {_font('display')} · body {_font('body')} · "
        f"caption {_font('caption')} · scale {typ.get('scale')}\n"
        f"  motion .... budget {motion.get('max_per_scene')}/scene · "
        f"easing {motion.get('easing')}\n"
        f"  fps ....... {style.get('fps')}\n"
        f"  textures .. {tex}"
    )


def format_board_preview(board: dict) -> str:
    lines = ["STORYBOARD — preview", f"  {board.get('total_scenes', 0)} scenes:"]
    sig = None
    for s in board.get("scenes", []):
        fx = ", ".join(e.get("name") for e in s.get("effects", [])) or "—"
        star = ""
        if s.get("signature_beat"):
            sig = s.get("scene_no")
            star = "  ★ signature beat"
        lines.append(f"   {s.get('scene_no'):>2}. {s.get('layout')} · "
                     f"{s.get('transition')} · [{fx}]{star}")
    if sig is not None:
        lines.append(f"  signature beat: scene {sig} (the one #FFD000 highlighter)")
    return "\n".join(lines)


def format_digest(kind: str, spec: dict, json_path) -> str:
    preview = format_style_preview(spec) if kind == "style" else format_board_preview(spec)
    return f"{preview}\n  saved (for the next agent): {json_path}"


# ----------------------------------------------------------------------
# Compute (LLM) + persist — separated so we can PREVIEW before we WRITE
# ----------------------------------------------------------------------
def compute_style(path: str) -> dict:
    """Design the style guide in memory (no write). Raises ValueError on a bad script."""
    script = engine.load_script(path)
    ok, reason = engine.validate_script(script)
    if not ok:
        raise ValueError(reason)
    return engine.design_style(script)


def compute_board(path: str, style_guide: dict) -> dict:
    """Build the storyboard in memory (no write). Raises ValueError on a bad script."""
    script = engine.load_script(path)
    ok, reason = engine.validate_script(script)
    if not ok:
        raise ValueError(reason)
    return engine.build_storyboard(script, style_guide)


def _title_for(path: str) -> str:
    return engine.load_script(path).get("working_title", "design")


def persist(kind: str, path: str, spec: dict) -> pathlib.Path:
    """Write the stamped artifact. Into the project dir (canonical name) if `path` is
    a directory; otherwise into designs/. Iris stamps her own schema_version locally;
    in the pipeline, atlas re-stamps authoritatively at the adapter boundary."""
    stamped = {"schema_version": engine.SCHEMA_VERSION, **spec}
    target = pathlib.Path(path).expanduser()
    name = "style_guide.json" if kind == "style" else "storyboard.json"
    if target.is_dir():
        out = target / name
        chat_state.atomic_write_json(out, stamped)
        return out
    return engine._save(spec, "style_guide" if kind == "style" else "storyboard",
                        _title_for(path))


def _log_run(kind: str, spec: dict) -> None:
    title = (spec.get("working_title") or "")  # specs don't carry it; keep generic
    mem = engine.load_memory()
    extra = ({"fps": spec.get("fps"), "textures": len(spec.get("textures", []))}
             if kind == "style"
             else {"scenes": spec.get("total_scenes"),
                   "signature_scene": next((s["scene_no"] for s in spec.get("scenes", [])
                                            if s.get("signature_beat")), None)})
    mem["runs"].append({"working_title": title or "(spec)", "kind": kind, **extra,
                        "generated": time.strftime("%Y-%m-%d %H:%M:%S")})
    engine.save_memory(mem)


# ----------------------------------------------------------------------
# The gated jobs — compute -> preview -> [y/N] -> write. (Synchronous; the native
# tools call these off the SDK loop via asyncio.to_thread.)
# ----------------------------------------------------------------------
def run_style_job(path: str, *, gate: bool) -> str | None:
    """Style job with an optional [y/N] gate AFTER a tight preview. None if declined."""
    style = compute_style(path)
    print("\n" + format_style_preview(style))
    if gate and not ask_yes_no("\n🎨 Write this style guide? [y/N] "):
        return None
    json_path = persist("style", path, style)
    _log_run("style", style)
    return format_digest("style", style, json_path)


def run_board_job(path: str, *, gate: bool) -> str | None:
    """Storyboard job. Requires a style guide; if none exists, designs one first
    (its OWN gate), then boards. Each step previews then gates. None if declined."""
    sg = engine.load_style_guide(path)
    if not (isinstance(sg, dict) and sg):
        print("   (no style guide beside this script yet — I'll design one first.)")
        digest = run_style_job(path, gate=gate)
        if digest is None:
            return None  # declined the style step -> can't board
        print("\nIris: " + digest)
        sg = engine.load_style_guide(path)
        if not (isinstance(sg, dict) and sg):
            # path wasn't a project dir, so the style went to designs/; use in-memory.
            sg = compute_style(path)

    board = compute_board(path, sg)
    print("\n" + format_board_preview(board))
    if gate and not ask_yes_no("\n🎬 Write this storyboard? [y/N] "):
        return None
    json_path = persist("board", path, board)
    _log_run("board", board)
    return format_digest("board", board, json_path)


# ----------------------------------------------------------------------
# Strict marker parsing (provider-agnostic fallback trigger)
# ----------------------------------------------------------------------
def _parse_marker(text: str, marker: str) -> str | None:
    """Return the path iff `text` ends with a single, exact `marker` line."""
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


def parse_iris_request(text: str) -> tuple[str, str] | None:
    """Return (kind, path) for a clean trailing marker, else None.

    kind is 'style' or 'board'. If BOTH marker types appear anywhere in the text, it's
    ambiguous -> None (don't guess which job the model meant).
    """
    has_style = any(ln.strip().startswith(STYLE_MARKER) for ln in text.splitlines())
    has_board = any(ln.strip().startswith(BOARD_MARKER) for ln in text.splitlines())
    if has_style and has_board:
        return None
    style_path = _parse_marker(text, STYLE_MARKER)
    if style_path:
        return "style", style_path
    board_path = _parse_marker(text, BOARD_MARKER)
    if board_path:
        return "board", board_path
    return None


def strip_iris_request(text: str) -> str:
    """Remove any marker line so it isn't shown to the user."""
    kept = [ln for ln in text.splitlines()
            if not (ln.strip().startswith(STYLE_MARKER)
                    or ln.strip().startswith(BOARD_MARKER))]
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


def _start_thinking(label: str = "Iris is thinking") -> None:
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
# Iris's own tools so that body-gate fires.
# ----------------------------------------------------------------------
@tool("design_style", "Design the global style guide (palette, type, motion, "
      "textures, fps) from a fact-checked script. Pass a project directory holding "
      "script.json, or a script.json path.", {"path": str})
async def design_style_tool(args):
    path = (args.get("path") or "").strip()
    try:
        digest = await asyncio.to_thread(run_style_job, path, gate=True)
    except Exception as exc:  # keep the conversation alive on any failure
        return {"content": [{"type": "text", "text": f"Couldn't design the style: {exc}"}],
                "is_error": True}
    if digest is None:
        return {"content": [{"type": "text",
                             "text": f"The user declined to write the style guide for "
                                     f"{path!r} right now."}]}
    return {"content": [{"type": "text", "text": f"Style guide written:\n{digest}"}]}


@tool("build_storyboard", "Build the scene-by-scene storyboard from a script (and the "
      "style guide; one is designed first if absent). Pass a project directory holding "
      "script.json, or a script.json path.", {"path": str})
async def build_storyboard_tool(args):
    path = (args.get("path") or "").strip()
    try:
        digest = await asyncio.to_thread(run_board_job, path, gate=True)
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"Couldn't build the storyboard: {exc}"}],
                "is_error": True}
    if digest is None:
        return {"content": [{"type": "text",
                             "text": f"The user declined to write the storyboard for "
                                     f"{path!r} right now."}]}
    return {"content": [{"type": "text", "text": f"Storyboard written:\n{digest}"}]}


async def can_use_tool(name, inp, ctx):
    """Admit Iris's own tools (their body holds the preview + [y/N] gate); deny others."""
    _stop_thinking()
    if name in (STYLE_TOOL_NAME, BOARD_TOOL_NAME):
        path = (inp.get("path") or "").strip()
        if not path:
            return PermissionResultDeny(behavior="deny",
                                        message="No script path was given to design from.",
                                        interrupt=False)
        return PermissionResultAllow(behavior="allow", updated_input=inp)
    return PermissionResultDeny(behavior="deny",
                                message="That tool isn't allowed here.",
                                interrupt=False)


_IRIS_SERVER = create_sdk_mcp_server("iris",
                                     tools=[design_style_tool, build_storyboard_tool])
IRIS_WIRING = {"server": _IRIS_SERVER, "can_use_tool": can_use_tool}


# ----------------------------------------------------------------------
# Context assembly + a single conversational turn
# ----------------------------------------------------------------------
def _context_summary(state: dict, snapshot: str) -> str:
    parts = [p for p in (state["summary"].strip(), snapshot.strip()) if p]
    return "\n\n".join(parts)


def _send(state, system, summarizer, snapshot, user_msg, *, iris):
    """Compact if needed, call the model, return Iris's reply text (or None)."""
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
        return llm.converse(system, summary, state["transcript"], user_msg, iris=iris)
    finally:
        _stop_thinking()


def handle_message(state, system, summarizer, user_msg):
    """One user message -> Iris's reply, kept in the in-RAM transcript only."""
    mem = engine.load_memory()
    snapshot = memory_snapshot(mem)
    try:
        reply = _send(state, system, summarizer, snapshot, user_msg, iris=IRIS_WIRING)
    except Exception as exc:
        print(f"\n(Iris hit a problem: {exc}\n Try again, or /new if it persists.)")
        return
    if reply is None:
        return

    req = parse_iris_request(reply)
    display = strip_iris_request(reply) if req else reply
    print(f"\nIris: {display}")

    chat_state.append_turn(state, "user", user_msg)
    chat_state.append_turn(state, "iris", display or reply)

    # Fallback path: the model emitted a marker instead of calling a tool.
    if req:
        kind, path = req
        _job_then_discuss(state, system, summarizer, kind, path, gate=True)


def _job_then_discuss(state, system, summarizer, kind, path, *, gate):
    """Run a gated job (style or board) and let Iris pitch it in voice."""
    runner = run_style_job if kind == "style" else run_board_job
    label = "style guide" if kind == "style" else "storyboard"
    try:
        digest = runner(path, gate=gate)
    except Exception as exc:
        print(f"   (couldn't produce the {label}: {exc})")
        return
    if digest is None:
        feedback = (f"[note] The user declined to write the {label} from {path!r}. "
                    "Acknowledge and keep talking.")
    else:
        feedback = (f"[{label} written from {path!r}]\n{digest}\n"
                    "Pitch this to the user in your own voice — palette in hexes, type "
                    "in weights, the fps and budget, then the storyboard scene by "
                    "scene if there is one, and call out which scene carries the "
                    "signature beat and why it earned it.")

    mem = engine.load_memory()
    snapshot = memory_snapshot(mem)
    try:
        reply = _send(state, system, summarizer, snapshot, feedback, iris=None)
    except Exception as exc:
        print(f"\n(Iris couldn't pitch the {label}: {exc})")
        return
    if reply:
        print(f"\nIris: {reply}")
        chat_state.append_turn(state, "user", feedback)
        chat_state.append_turn(state, "iris", reply)


# ----------------------------------------------------------------------
# Slash commands
# ----------------------------------------------------------------------
HELP = """Commands:
  /style <path>   design the style guide from a script (project dir or script.json)
  /board <path>   storyboard the scenes (designs a style guide first if there isn't one)
  /summary        distill the session so far, then show what Iris remembers
  /new            distill + start a fresh thread (keeps what Iris knows about you)
  /help           show this
  /exit           save (distill) and quit
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
        print("\n[What Iris remembers about you]\n" + body)
        if not ok:
            print("(I couldn't fully update just now — kept what I had; your chat "
                  "is safe and I'll fold it in next launch.)")
    elif cmd == "/new":
        distill_and_save(state, distiller,
                         status="💾 Saving what matters before clearing the thread…")
        state["transcript"] = []
        print("Fresh thread. I've folded this chat into what I remember about you "
              "— your taste, your references, what's landed; the back-and-forth is cleared.")
    elif cmd in ("/style", "/board"):
        if not arg:
            print(f"Usage: {cmd} <project_dir or script.json>")
        else:
            # Explicit command: typing it IS the approval for the WRITE, but Iris
            # still previews first. (The [y/N] gate lives on the model-initiated
            # tools + marker path.)
            kind = "style" if cmd == "/style" else "board"
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
    print("Talk to Iris.  /help for commands, /exit to leave.")
    if state["summary"].strip():
        print("(Iris remembers what matters about your work from before — pick up "
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
