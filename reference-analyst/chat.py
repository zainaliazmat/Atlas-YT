"""Talk to Vera — a conversational REPL over the same soul/identity.

Launch:  python run.py chat

Vera talks like a person (persona from SOUL.md + STYLE.md — NOT the SKILL output
contract), remembers you across sessions via a single distilled SUMMARY, and can
build/extend a rubric from reference videos mid-conversation via an in-process tool
you approve before it runs.

Memory model (summary-only — no transcript replay across sessions):
- Across sessions, Vera's only long-term memory is a distilled summary in
  chat_state.json. The raw transcript is NOT persisted between sessions.
- DURING a session the full transcript lives in RAM so Vera has working memory.
- On every session boundary (/exit, Ctrl+C, /new, /summary) we distill the session
  into the summary, drop the junk, clear the raw transcript, and persist the summary.
- No data loss: if distill fails/times out we park the raw transcript under "pending"
  in chat_state.json and fold it in on the next launch.

This mirrors the repo's co-worker REPL pattern (topic-researcher/chat.py) exactly.
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

import chat_state
import compaction
import llm
import rubric_store

HERE = pathlib.Path(__file__).parent
STATE_PATH = HERE / "chat_state.json"

# ----------------------------------------------------------------------
# Persona bundle (soul.md framework): SOUL = identity, STYLE = voice,
# examples/ = calibration. The rubric ENGINE reads NONE of this — SOUL/STYLE/
# examples are loaded HERE, into chat, so the voice never leaks into the structured
# rubric. SKILL.md (the engine method) is never loaded into chat.
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

CONVERSATION_BUDGET_TOKENS = 8000  # headroom for summary + recent turns + new msg
VERA_TOOL_NAME = "mcp__vera__build_rubric"   # how the model references the tool
DISTILL_TIMEOUT_SEC = 25


# ----------------------------------------------------------------------
# Persona system prompt — built from SOUL + STYLE + examples (NO SKILL contract)
# ----------------------------------------------------------------------
CHAT_ADDENDUM = """
## Right now: a live conversation
You're talking with the CEO directly, in real time — not producing a rubric. Speak
like a real person with your expertise: measured, exacting, in your own voice. Do
NOT emit the structured rubric format (targets/bands/judged) here; that's for jobs,
not conversation. You can still be precise and reference numbers naturally.

## What you remember (be accurate about this)
You keep a distilled summary of what matters about this user across sessions — the
look and feel they're chasing, the references they admire, their standards (how
snappy, how loud, how kinetic), and the style targets you've settled together — but
NOT the word-for-word history of past chats. So you are NOT meeting them for the
first time. If asked what you remember, describe it honestly: a running summary of
the important stuff, not a transcript.

## Building a rubric mid-chat
When measuring a reference would genuinely help, you can run the job: call the
`build_rubric` tool with `videos` (a path or comma-separated paths) and an optional
`standard` name. The user approves before it runs. When results come back, talk
through them in your own voice — what the numbers say about the look, and the few
questions only taste can answer (the open_questions). Be honest when a property the
reference has is something the generator can't reproduce.
"""


def build_system_prompt(soul_text: str = SOUL, style_text: str = STYLE,
                        examples_text: str = EXAMPLES) -> str:
    """Vera's chat identity: SOUL (who she is) + STYLE (how she talks) +
    examples/ (calibration) + live-conversation guidance. Excludes SKILL.md."""
    parts = [soul_text.strip()]
    if style_text.strip():
        parts.append("# HOW YOU TALK (voice & style)\n\n" + style_text.strip())
    if examples_text.strip():
        parts.append(
            "# VOICE CALIBRATION (examples)\n\n"
            "These show how you sound right vs. off-character. Match the vibe of "
            "the good outputs; avoid the patterns in the bad ones. They are "
            "calibration, not scripts — never quote them verbatim.\n\n"
            + examples_text.strip())
    parts.append(CHAT_ADDENDUM.strip())
    return "\n\n".join(parts)


BUDGET_TOKENS = (compaction.estimate_tokens(build_system_prompt())
                 + CONVERSATION_BUDGET_TOKENS)


# ----------------------------------------------------------------------
# Distillation — the ONE memory helper, used on /exit, SIGINT, /new, /summary
# ----------------------------------------------------------------------
DISTILL_SYSTEM = (
    "You maintain the long-term memory of Vera, a discerning reference analyst, "
    "about ONE collaborator she works with. That memory is a single distilled "
    "summary she reloads at the start of every session — so it must hold only what "
    "makes her help sharper, in as few words as possible."
)


def _distill_prompt(existing_summary: str, transcript: list[dict[str, str]]) -> str:
    convo = compaction.transcript_text(transcript)
    return (
        "Here is the memory you already hold about the collaborator:\n"
        f"{existing_summary.strip() or '(nothing yet)'}\n\n"
        "Here is the full transcript of the session that just happened:\n"
        f"{convo}\n\n"
        "Rewrite the memory as a single clean, consolidated summary.\n\n"
        "KEEP only durable, taste-improving signal:\n"
        "- the look and feel they're chasing\n"
        "- the references they admire and what about them they want kept\n"
        "- their standards (how snappy/loud/kinetic, palette, typography)\n"
        "- decisions made and style targets settled (incl. ceo_prefs answers)\n"
        "- anything about how they like to work\n\n"
        "DROP the junk: greetings and small talk, off-topic questions, identity-test "
        "exchanges, and anything transient.\n\n"
        "MERGE with the memory you already hold — do not replace it; knowledge "
        "accumulates across sessions. Resolve contradictions in favor of the MOST "
        "RECENT information.\n\n"
        "Keep it BOUNDED: a few tight bullet groups, well under 600 words. Output "
        "ONLY the updated summary — no preamble. If the session contained nothing "
        "worth keeping, return the existing memory unchanged."
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

    On success: summary updated, transcript + pending cleared; returns True. On
    failure/timeout (NO DATA LOSS): the backlog is parked under "pending" and the
    existing summary kept; returns False.
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
# Compact rubric presentation for in-chat handoff (not the raw rubric)
# ----------------------------------------------------------------------
def format_rubric_brief(rubric: dict) -> str:
    """A short, in-character-friendly digest of a rubric for Vera to talk through."""
    t = rubric.get("targets", {})

    def band(group, key):
        node = (t.get(group, {}) or {}).get(key, {}) or {}
        v, b = node.get("value"), node.get("band")
        if v is None:
            return None
        return f"{v} (band {b})" if b else f"{v}"

    out = [f"Standard built from {len(rubric.get('source_videos', []))} reference(s): "
           f"{', '.join(rubric.get('source_videos', []) or ['—'])}"]
    rows = [("avg shot (s)", band("pacing", "avg_shot_sec")),
            ("cuts/min", band("pacing", "cuts_per_min")),
            ("kinetic", band("motion", "kinetic_score")),
            ("saturation", band("color", "saturation")),
            ("brightness", band("color", "brightness")),
            ("integrated LUFS", band("audio", "integrated_lufs")),
            ("speech ratio", band("audio", "speech_ratio")),
            ("duration (s)", band("structure", "duration_sec"))]
    for label, val in rows:
        if val is not None:
            out.append(f"  - {label}: {val}")
    judged = rubric.get("judged", {})
    out.append(f"  - judged: {judged.get('status', '?')} "
               f"({len(judged.get('frames', []))} frames)"
               + (f" — degraded: {judged['error']}" if judged.get("error") else ""))
    oq = rubric.get("open_questions", [])
    if oq:
        out.append("\nQuestions only taste can answer:")
        for q in oq:
            out.append(f"  - [{q.get('id')}] {q.get('plain')}")
    return "\n".join(out)


# ----------------------------------------------------------------------
# "Thinking…" indicator
# ----------------------------------------------------------------------
_spinner: tuple | None = None


def _start_thinking(label: str = "Vera is thinking") -> None:
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
# Helpers shared by the tool path and the /rubric command
# ----------------------------------------------------------------------
def _parse_videos(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").replace(",", " ").split() if p.strip()]


def run_rubric(videos: list[str], standard: str = "default", *, vision: bool = True) -> dict:
    """Build/extend `standard` from `videos`. Raises ValueError when nothing is usable."""
    existing, missing = rubric_store.validate_videos(videos)
    if not existing:
        raise ValueError("none of those video files exist locally "
                         f"({', '.join(videos) or 'no paths given'}).")
    vision_fn = llm.make_style_profiler() if vision else None
    return rubric_store.build_standard(standard, existing, vision_fn=vision_fn)


# ----------------------------------------------------------------------
# The native rubric tool + approval callback (Claude path)
# ----------------------------------------------------------------------
@tool("build_rubric", "Measure one or more reference videos into a rubric (banded "
      "quality targets + a style profile), merging into a named standard. Pass "
      "'videos' (a path or comma-separated paths) and an optional 'standard' name.",
      {"videos": str, "standard": str})
async def build_rubric_tool(args):
    videos = _parse_videos(args.get("videos") or "")
    standard = (args.get("standard") or "default").strip() or "default"
    if not videos:
        return {"content": [{"type": "text", "text": "No video paths were given."}],
                "is_error": True}
    try:
        rubric = await asyncio.to_thread(run_rubric, videos, standard)
    except Exception as exc:  # keep the conversation alive on any failure
        return {"content": [{"type": "text",
                             "text": f"Rubric build couldn't run: {exc}"}], "is_error": True}
    return {"content": [{"type": "text",
                         "text": f"Rubric for standard {standard!r}:\n"
                                 + format_rubric_brief(rubric)}]}


async def can_use_tool(name, inp, ctx):
    """Intercept the tool call and ask the user before anything runs."""
    if name == VERA_TOOL_NAME:
        _stop_thinking()
        videos = _parse_videos(inp.get("videos") or "")
        if not videos:
            return PermissionResultDeny(behavior="deny",
                                        message="No video paths were given.",
                                        interrupt=False)
        approved = await asyncio.to_thread(
            ask_yes_no, f"\n🔬 Vera wants to measure {len(videos)} reference(s). Run it? [y/N] ")
        if approved:
            print("   …measuring the reference (this can take a moment)…")
            return PermissionResultAllow(behavior="allow", updated_input=inp)
        return PermissionResultDeny(
            behavior="deny",
            message="The user declined to build the rubric right now.",
            interrupt=False)
    return PermissionResultDeny(behavior="deny",
                                message="That tool isn't allowed here.",
                                interrupt=False)


_VERA_SERVER = create_sdk_mcp_server("vera", tools=[build_rubric_tool])
VERA_WIRING = {"server": _VERA_SERVER, "can_use_tool": can_use_tool}


def ask_yes_no(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# ----------------------------------------------------------------------
# Context assembly + a single conversational turn
# ----------------------------------------------------------------------
def _send(state, system, summarizer, user_msg):
    """Compact if needed, call the model, return Vera's reply text (or None)."""
    _start_thinking()
    try:
        info = compaction.compact(
            state, summarizer=summarizer, system=system,
            pending_user_msg=user_msg, budget=BUDGET_TOKENS)
        if not info["fits"]:
            _stop_thinking()
            print("⚠️  " + info["reason"])
            return None
        return llm.converse(system, state["summary"], state["transcript"], user_msg)
    finally:
        _stop_thinking()


def handle_message(state, system, summarizer, user_msg):
    """One user message -> Vera's reply, kept in the in-RAM transcript only."""
    try:
        reply = _send(state, system, summarizer, user_msg)
    except Exception as exc:
        print(f"\n(Vera hit a problem: {exc}\n Try again, or /new if it persists.)")
        return
    if reply is None:
        return
    print(f"\nVera: {reply}")
    chat_state.append_turn(state, "user", user_msg)
    chat_state.append_turn(state, "vera", reply)


def _rubric_then_discuss(state, system, summarizer, videos, standard, *, gate):
    """Build a rubric (optional [y/N] gate) and let Vera discuss it in voice."""
    if gate and not ask_yes_no(
            f"\n🔬 Vera wants to measure {len(videos)} reference(s). Run it? [y/N] "):
        feedback = ("[note] The user declined to build the rubric. Acknowledge and "
                    "keep talking.")
    else:
        print("   …measuring the reference…")
        try:
            rubric = run_rubric(videos, standard)
        except Exception as exc:
            print(f"   (rubric build failed: {exc})")
            return
        # Show the measured rubric UNCONDITIONALLY — the numbers are the deliverable and
        # they're already saved to disk, so the user must see them even if the in-voice
        # discussion below fails (a flaky brain must never hide a completed measurement).
        print(f"\n[rubric for standard {standard!r} — saved to "
              f"standards/{rubric_store._slug(standard)}.json]\n"
              f"{format_rubric_brief(rubric)}")
        feedback = (f"[rubric for standard {standard!r}]\n{format_rubric_brief(rubric)}\n"
                    "Present this to the user in your own voice — what the numbers say "
                    "about the look, and the few questions only taste can answer. Be "
                    "honest if a property is something the generator can't reproduce.")

    try:
        reply = _send(state, system, summarizer, feedback)
    except Exception as exc:
        print(f"\n(Vera measured it — the rubric above is saved — but couldn't talk it "
              f"through just now: {exc})")
        return
    if reply:
        print(f"\nVera: {reply}")
        chat_state.append_turn(state, "user", feedback)
        chat_state.append_turn(state, "vera", reply)


# ----------------------------------------------------------------------
# Slash commands
# ----------------------------------------------------------------------
HELP = """Commands:
  /rubric <path> [path...] [--standard NAME]   measure reference(s) and talk it through
  /summary            distill the session so far, then show what Vera remembers
  /new                distill + start a fresh thread (keeps what Vera knows about you)
  /help               show this
  /exit               save (distill) and quit
Anything else is just conversation."""


def _split_rubric_args(arg: str) -> tuple[list[str], str]:
    """Parse '<paths...> [--standard NAME]' into (videos, standard)."""
    standard = "default"
    if "--standard" in arg:
        head, _, tail = arg.partition("--standard")
        standard = tail.strip().split()[0] if tail.strip() else "default"
        arg = head
    return _parse_videos(arg), standard


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
        print("\n[What Vera remembers about you]\n" + body)
        if not ok:
            print("(I couldn't fully update just now — kept what I had; your chat "
                  "is safe and I'll fold it in next launch.)")
    elif cmd == "/new":
        distill_and_save(state, distiller,
                         status="💾 Saving what matters before clearing the thread…")
        state["transcript"] = []
        print("Fresh thread. I've folded this chat into what I remember about you "
              "— the look you're after and our settled targets stay; the back-and-"
              "forth is cleared.")
    elif cmd == "/rubric":
        videos, standard = _split_rubric_args(arg)
        if not videos:
            print("Usage: /rubric <path> [path...] [--standard NAME]")
        else:
            _rubric_then_discuss(state, system, summarizer, videos, standard, gate=False)
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
    print("Talk to Vera.  /help for commands, /exit to leave.")
    if state["summary"].strip():
        print("(Vera remembers what matters about you from before — pick up "
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
