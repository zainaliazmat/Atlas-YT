"""Talk to Quill — a conversational REPL over the same soul/identity.

Launch:  python run.py chat

Quill talks like a person (persona from SOUL.md only — NOT the SKILL coaching
method/output contract), remembers you across sessions via a single distilled
SUMMARY, and can author a REAL coaching addendum mid-conversation via an in-process
tool that you approve before it runs.

Memory model (summary-only — no transcript replay across sessions):
- Across sessions, Quill's only long-term memory is a distilled summary in
  chat_state.json. The raw transcript is NOT persisted between sessions.
- DURING a session the full transcript lives in RAM (state["transcript"]) so Quill
  has normal working memory of the live conversation.
- On every session boundary (/exit, Ctrl+C, /new, /summary) we run ONE helper,
  distill(existing_summary, transcript) -> new_summary, fold the session into the
  summary, drop the junk, clear the raw transcript, and persist only the summary.
- No data loss: if distill fails/times out we park the raw transcript under
  "pending" in chat_state.json and fold it in on the next launch.

Design notes:
- Durable state is OUR chat_state.json, not a Claude session id (provider-portable).
- distill() uses the provider-agnostic llm.chat() seam, so distillation stays cheap
  and brain-swappable.
- Authoring mid-chat uses the SDK's native tool + can_use_tool approval gate. A strict
  text marker ("QUILL_REQUEST: <band>|<direction>") is kept as a provider-agnostic
  fallback for brains without tools.
- The coach engine (coach_engine.propose_addendum) is pure + synchronous, so we call
  it via asyncio.to_thread to avoid clashing with the SDK's loop.
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
import coach_engine
import llm

HERE = pathlib.Path(__file__).parent
STATE_PATH = HERE / "chat_state.json"

# ----------------------------------------------------------------------
# Persona bundle (soul.md framework): SOUL = identity, STYLE = voice,
# examples/ = calibration. The coach ENGINE (coach_engine.py) reads ONLY SOUL via
# its COACHING_PHILOSOPHY; STYLE + examples are loaded HERE, into chat, so the voice
# never leaks into the engine's structured addendum. SKILL.md (the engine method) is
# never loaded into chat — that separation keeps Quill human here, rigorous in jobs.
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

# The persona prompt (SOUL + STYLE + examples) is large, FIXED overhead that
# compaction can never shrink — it only folds conversation history. So the budget is
# persona size + a conversation allowance, computed AFTER build_system_prompt().
CONVERSATION_BUDGET_TOKENS = 8000  # headroom for summary + recent turns + new msg
QUILL_TOOL_NAME = "mcp__quill__author_addendum"   # how the model references the tool
QUILL_MARKER = "QUILL_REQUEST:"                    # strict fallback trigger

# How long we'll wait for a session-end distill before falling back to "pending".
DISTILL_TIMEOUT_SEC = 25


# ----------------------------------------------------------------------
# Persona system prompt — built from SOUL only (NO SKILL method/output contract)
# ----------------------------------------------------------------------
CHAT_ADDENDUM = """
## Right now: a live conversation
You're talking with the user directly, in real time — not authoring a formal coach
note. Talk like a real person with your expertise: crisp, declarative, opinionated,
fast. Do NOT emit the structured "## Coach note (Quill …)" addendum format here;
that's for jobs, not conversation. Talk the editorial shape — the hook, one idea per
scene, the arc, claim support, whether the visuals are actually relevant — like a
story editor in the room.

## What you remember (be accurate about this)
You keep a distilled summary of what matters about this user across sessions — the
kinds of videos and topics they make, their channel and audience, their taste
(hooks, clarity, claim support, how much room they give a tangent), the decisions
you've made together, and notes that landed — but NOT the word-for-word history of
past chats. So you are NOT meeting them for the first time and you do NOT start fresh
every session: use the remembered context you're given and sound like someone who
knows their work. If asked what you remember, describe it honestly: a running summary
of the important stuff, not a transcript.

## Authoring a coach note mid-chat
When the user has a diagnosed metric (a band_id + the direction the rubric says to
move it) and wants the actual addendum, you can author a real one: call the
`author_addendum` tool with `band` and `direction` (and optional `preserve` /
`owner`). The user is asked to approve before it runs, so only call it when you mean
it. When the note comes back, hand it over plainly — the band it targets, the move
it asks for. Don't dump it raw mid-banter.

## The one rule you never bend
The rubric decides the band and the direction — you do NOT. You never invent a new
goal beyond the metric named, and you never let a fix break a sibling property you're
told to preserve. You author the coaching text; the CEO-owned rubric decides
pass/fail. If a request asks you to judge quality or change the target, route it back:
that's not your call.
"""


def build_system_prompt(soul_text: str = SOUL, style_text: str = STYLE,
                        examples_text: str = EXAMPLES) -> str:
    """Quill's chat identity: SOUL (who she is) + STYLE (how she talks) +
    examples/ (calibration) + live-conversation guidance.

    This is the soul.md persona bundle. It deliberately excludes SKILL.md (the
    coaching method / output contract) — that would make Quill terse and robotic in
    chat. STYLE and the examples are what make her sound like a person here.
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


# Whole-prompt ceiling = fixed persona overhead + conversation allowance. Computed
# from the real built prompt so any persona enrichment self-adjusts the budget.
BUDGET_TOKENS = (compaction.estimate_tokens(build_system_prompt())
                 + CONVERSATION_BUDGET_TOKENS)


# ----------------------------------------------------------------------
# Distillation — the ONE memory helper, used on /exit, SIGINT, /new, /summary
# ----------------------------------------------------------------------
DISTILL_SYSTEM = (
    "You maintain the long-term memory of Quill, a sharp editorial coach, about ONE "
    "collaborator she works with. That memory is a single distilled summary she "
    "reloads at the start of every session — so it must hold only what makes her "
    "coaching land closer to what this person wants, in as few words as possible."
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
        "- their audience and the tone and pacing they like\n"
        "- their editorial taste: how punchy a hook, how much claim support, how much "
        "room for a tangent, what kind of arcs land for them\n"
        "- decisions made, notes that worked, structural preferences\n"
        "- anything about how they like to work with a coach\n\n"
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
    """Build distill(existing_summary, transcript) -> new_summary from a chat seam.

    Injectable so tests can pass a fake chat function (no API). An empty transcript
    is a no-op that returns the existing summary verbatim.
    """
    def distill(existing_summary: str, transcript: list[dict[str, str]]) -> str:
        existing = (existing_summary or "").strip()
        if not transcript:
            return existing
        new = chat_fn(DISTILL_SYSTEM, _distill_prompt(existing, transcript)).strip()
        return new or existing
    return distill


def _distill_with_timeout(distiller, summary, transcript, timeout):
    """Run `distiller(summary, transcript)` with a hard timeout.

    Executes in a daemon thread so a slow/hung LLM call can't block exit and so
    abandoning it on timeout never keeps the interpreter alive. Re-raises whatever
    the distiller raised; raises TimeoutError if it overruns `timeout` seconds.
    """
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

    The backlog to fold = any previously-stranded "pending" turns + the live in-RAM
    transcript. On success: summary updated, transcript + pending cleared, summary
    persisted; returns True. On failure/timeout (NO DATA LOSS): the whole backlog is
    parked under "pending" in chat_state.json and the existing summary is kept; the
    in-RAM transcript is left intact and returns False.
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
    """On launch, fold any "pending" raw transcript (failed prior distill) in.

    Retries the distill; on success the summary absorbs it and "pending" is cleared.
    On failure we keep "pending" untouched for a future launch — never dropping it.
    """
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
# Compact addendum presentation for in-chat handoff
# ----------------------------------------------------------------------
def format_addendum_brief(result: dict) -> str:
    """A short, in-character-friendly digest of a coach note for Quill to hand over."""
    out = []
    out.append(f"Target: {result.get('domain', 'editorial')} · {result['band_id']}")
    if result.get("owner"):
        out.append(f"For: {result['owner']}")
    out.append(f"Move: {result['direction']}")
    out.append(f"(authored by: {result.get('source', 'rule')})")
    out.append("\n" + result["addendum"].strip())
    return "\n".join(out)


# ----------------------------------------------------------------------
# Run an author job: call the pure engine, format a digest
# ----------------------------------------------------------------------
def run_author(band: str, direction: str, preserve: str = "",
               owner: str = "") -> dict:
    """Author a coaching addendum and return the engine result dict.

    Raises ValueError with a plain message when the inputs aren't usable, so the REPL
    apologises instead of dumping a traceback.
    """
    band = (band or "").strip()
    direction = (direction or "").strip()
    if not band:
        raise ValueError("No band_id was given to coach toward.")
    if not direction:
        raise ValueError("No direction was given — the rubric decides which way to move.")
    return coach_engine.propose_addendum(
        band_id=band, direction=direction, preserve=preserve, owner=owner)


# ----------------------------------------------------------------------
# Strict marker parsing (provider-agnostic fallback trigger)
# ----------------------------------------------------------------------
def parse_quill_request(text: str):
    """Return (band, direction) iff `text` ends with a single, exact marker line.

    Marker shape:  QUILL_REQUEST: <band>|<direction>
    Strict on purpose so a mid-text MENTION of the marker can't false-trigger: the
    marker line must (a) appear exactly once, (b) be the last non-empty line,
    (c) start the line, and (d) carry a non-empty band and direction.
    """
    lines = text.splitlines()
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty:
        return None
    marker_lines = [ln for ln in lines if ln.strip().startswith(QUILL_MARKER)]
    if len(marker_lines) != 1:
        return None
    if marker_lines[0].strip() != nonempty[-1].strip():
        return None
    payload = marker_lines[0].strip()[len(QUILL_MARKER):].strip()
    if "|" not in payload:
        return None
    band, direction = payload.split("|", 1)
    band, direction = band.strip(), direction.strip()
    if not band or not direction:
        return None
    return band, direction


def strip_quill_request(text: str) -> str:
    """Remove any marker line so it isn't shown to the user."""
    kept = [ln for ln in text.splitlines() if not ln.strip().startswith(QUILL_MARKER)]
    return "\n".join(kept).strip()


# ----------------------------------------------------------------------
# Approval gate (shared by the native tool and the marker fallback)
#
# The decision of whether to run a model-initiated author job is an INJECTABLE seam,
# so a non-terminal frontend (the web operator UI) can replace the [y/N] prompt with
# its own approval — e.g. a button — WITHOUT changing this file's terminal behavior.
# The default approver is the terminal input() gate, so the REPL is byte-for-byte
# unchanged. Both gate paths (the native can_use_tool and the marker fallback) route
# through `_approve`. This is the reference pattern the other specialists copy.
# ----------------------------------------------------------------------
def ask_yes_no(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def gate_prompt(band: str, direction: str) -> str:
    """The one approval prompt both gate paths show."""
    return (f"\n🖋️  Quill wants to author a coach note for '{band}' "
            f"({direction}). Run it? [y/N] ")


# The injectable approver: a sync (prompt: str) -> bool. Default = the terminal gate.
_approver = ask_yes_no


def set_approver(fn) -> None:
    """Replace the approval mechanism (e.g. the web UI injects a button). `None`
    restores the default terminal input() gate."""
    global _approver
    _approver = fn or ask_yes_no


def reset_approver() -> None:
    """Restore the default terminal [y/N] gate."""
    global _approver
    _approver = ask_yes_no


def _approve(band: str, direction: str) -> bool:
    """Ask the current approver whether to author. Used by BOTH gate paths."""
    return _approver(gate_prompt(band, direction))


# ----------------------------------------------------------------------
# "Thinking…" indicator — model calls block for several seconds.
# ----------------------------------------------------------------------
_spinner: tuple | None = None


def _start_thinking(label: str = "Quill is thinking") -> None:
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
    """Stop the spinner and clear its line. Idempotent; safe to call anytime."""
    global _spinner
    if _spinner is None:
        return
    stop, t = _spinner
    _spinner = None
    stop.set()
    t.join(timeout=1)


# ----------------------------------------------------------------------
# The native author tool + approval callback (Claude path)
# ----------------------------------------------------------------------
@tool("author_addendum", "Author a soft-tier editorial coaching addendum for a "
      "content specialist. The rubric decides the band + the direction; pass `band` "
      "(the band_id), `direction` (which way to move it), and optional `preserve` "
      "(sibling properties to keep in range) and `owner` (the specialist).",
      {"band": str, "direction": str, "preserve": str, "owner": str})
async def author_addendum_tool(args):
    band = (args.get("band") or "").strip()
    direction = (args.get("direction") or "").strip()
    preserve = (args.get("preserve") or "")
    owner = (args.get("owner") or "").strip()
    try:
        # run_author is sync (pure engine) -> off-thread to keep the SDK loop clean.
        result = await asyncio.to_thread(run_author, band, direction, preserve, owner)
    except Exception as exc:  # keep the conversation alive on any failure
        return {"content": [{"type": "text",
                             "text": f"Couldn't author the note: {exc}"}],
                "is_error": True}
    return {"content": [{"type": "text",
                         "text": f"Coach note authored for {band!r}:\n"
                                 + format_addendum_brief(result)}]}


async def can_use_tool(name, inp, ctx):
    """Intercept the tool call and ask the user before anything runs."""
    if name == QUILL_TOOL_NAME:
        _stop_thinking()  # clear the spinner before we prompt the user
        band = (inp.get("band") or "").strip()
        direction = (inp.get("direction") or "").strip()
        if not band or not direction:
            return PermissionResultDeny(
                behavior="deny",
                message="A coach note needs both a band and a direction.",
                interrupt=False)
        approved = await asyncio.to_thread(_approve, band, direction)
        if approved:
            print("   …authoring the coach note…")
            return PermissionResultAllow(behavior="allow", updated_input=inp)
        return PermissionResultDeny(
            behavior="deny",
            message=f"The user declined to author a note for '{band}' right now.",
            interrupt=False)
    return PermissionResultDeny(behavior="deny",
                                message="That tool isn't allowed here.",
                                interrupt=False)


_QUILL_SERVER = create_sdk_mcp_server("quill", tools=[author_addendum_tool])
QUILL_WIRING = {"server": _QUILL_SERVER, "can_use_tool": can_use_tool}


# ----------------------------------------------------------------------
# Context assembly + a single conversational turn
# ----------------------------------------------------------------------
def _context_summary(state: dict) -> str:
    """The distilled summary about the user (Quill keeps no run snapshot)."""
    return state["summary"].strip()


def _send(state, system, summarizer, user_msg, *, quill):
    """Compact if needed, call the model, return Quill's reply text (or None)."""
    _start_thinking()
    try:
        info = compaction.compact(
            state, summarizer=summarizer, system=system,
            pending_user_msg=user_msg, budget=BUDGET_TOKENS)
        if not info["fits"]:
            _stop_thinking()
            print("⚠️  " + info["reason"])
            return None
        summary = _context_summary(state)
        return llm.converse(system, summary, state["transcript"], user_msg, quill=quill)
    finally:
        _stop_thinking()


def handle_message(state, system, summarizer, user_msg):
    """One user message -> Quill's reply, kept in the in-RAM transcript only."""
    try:
        reply = _send(state, system, summarizer, user_msg, quill=QUILL_WIRING)
    except Exception as exc:
        print(f"\n(Quill hit a problem: {exc}\n Try again, or /new if it persists.)")
        return
    if reply is None:
        return  # budget warning already printed

    parsed = parse_quill_request(reply)
    display = strip_quill_request(reply) if parsed else reply
    print(f"\nQuill: {display}")

    chat_state.append_turn(state, "user", user_msg)
    chat_state.append_turn(state, "quill", display or reply)

    # Fallback path: the model emitted a marker instead of calling the tool.
    if parsed:
        band, direction = parsed
        _author_then_discuss(state, system, summarizer, band, direction, gate=True)


def _author_then_discuss(state, system, summarizer, band, direction, *, gate,
                         preserve="", owner=""):
    """Run an author job (with optional [y/N] gate) and let Quill hand it over."""
    if gate and not _approve(band, direction):
        feedback = (f"[note] The user declined to author a coach note for '{band}'. "
                    "Acknowledge and keep talking.")
    else:
        print("   …authoring the coach note…")
        try:
            result = run_author(band, direction, preserve, owner)
        except Exception as exc:
            print(f"   (couldn't author it: {exc})")
            return
        feedback = (f"[coach note authored for {band!r}]\n{format_addendum_brief(result)}\n"
                    "Hand this to the user in your own voice — name the band it targets "
                    "and the move it asks for; don't dump it raw.")

    try:
        reply = _send(state, system, summarizer, feedback, quill=None)
    except Exception as exc:
        print(f"\n(Quill couldn't hand over the note: {exc})")
        return
    if reply:
        print(f"\nQuill: {reply}")
        chat_state.append_turn(state, "user", feedback)
        chat_state.append_turn(state, "quill", reply)


# ----------------------------------------------------------------------
# Slash commands
# ----------------------------------------------------------------------
HELP = """Commands:
  /author <band> | <direction>   author a coach note (the rubric gives band + direction)
  /summary        distill the session so far, then show what Quill remembers
  /new            distill + start a fresh thread (keeps what Quill knows about you)
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
        print("\n[What Quill remembers about you]\n" + body)
        if not ok:
            print("(I couldn't fully update just now — kept what I had; your chat "
                  "is safe and I'll fold it in next launch.)")
    elif cmd == "/new":
        distill_and_save(state, distiller,
                         status="💾 Saving what matters before clearing the thread…")
        state["transcript"] = []  # guarantee a fresh thread even if distill failed
        print("Fresh thread. I've folded this chat into what I remember about you "
              "— your channel, your taste, what's landed; the back-and-forth is cleared.")
    elif cmd == "/author":
        if "|" not in arg:
            print("Usage: /author <band> | <direction>")
        else:
            band, direction = arg.split("|", 1)
            band, direction = band.strip(), direction.strip()
            if not band or not direction:
                print("Usage: /author <band> | <direction>")
            else:
                # Explicit command: typing it IS the approval. The [y/N] gate lives on
                # the model-initiated author_addendum tool.
                _author_then_discuss(state, system, summarizer, band, direction,
                                     gate=False)
    else:
        print(f"Unknown command {cmd!r}. /help for the list.")
    return True


# ----------------------------------------------------------------------
# Graceful Ctrl+C (SIGINT) handling
# ----------------------------------------------------------------------
_SESSION: dict = {"state": None, "distiller": None, "interrupting": False}


def _flush_pending_and_die(state) -> None:
    """Second Ctrl+C during a distill: park the raw chat and hard-exit NOW."""
    try:
        backlog = (state.get("pending") or []) + state["transcript"]
        if backlog:
            chat_state.save_summary(STATE_PATH, state["summary"], pending=backlog)
    finally:
        os._exit(130)


def _sigint_handler(signum, frame):
    """Trap Ctrl+C: run the same graceful distill+save, then exit.

    A SECOND Ctrl+C arriving during the distill force-exits immediately, but flushes
    the raw transcript to "pending" first so nothing is lost.
    """
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
    print("Talk to Quill.  /help for commands, /exit to leave.")
    if state["summary"].strip():
        print("(Quill remembers what matters about your work from before — pick up "
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
