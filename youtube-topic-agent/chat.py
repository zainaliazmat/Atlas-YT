"""Talk to Viral Scout — a conversational REPL over the same soul/identity.

Launch:  python run.py chat

Scout talks like a person (persona from SOUL.md only — NOT the research output
contract), remembers you across sessions via a single distilled SUMMARY, knows
your research history (memory.json), and can run a REAL research job
mid-conversation via an in-process tool that you approve before it runs.

Memory model (this REPLACES the old transcript-replay approach):
- Across sessions, Scout's only long-term memory is a distilled summary in
  chat_state.json. The raw transcript is NOT persisted between sessions.
- DURING a session the full transcript lives in RAM (state["transcript"]) so
  Scout has normal working memory of the live conversation.
- On every session boundary (/exit, Ctrl+C, /new, /summary) we run ONE helper,
  distill(existing_summary, transcript) -> new_summary, fold the session into the
  summary, drop the junk, clear the raw transcript, and persist only the summary.
- No data loss: if distill fails/times out we park the raw transcript under
  "pending" in chat_state.json and fold it in on the next launch.

Design notes:
- Durable state is OUR chat_state.json, not a Claude session id (provider-portable).
- distill() uses the provider-agnostic llm.chat() seam (NOT the Claude converse()
  seam), so distillation is cheap and portable.
- Research mid-chat uses the SDK's native tool + can_use_tool approval gate. A
  strict text marker ("SCOUT_REQUEST: <niche>") is kept as a provider-agnostic
  fallback for brains without tools.
- The research engine (agent.run) is synchronous and spins its own event loop,
  so we call it via asyncio.to_thread to avoid clashing with the SDK's loop.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import signal
import sys
import threading

from claude_agent_sdk import (
    tool,
    create_sdk_mcp_server,
    PermissionResultAllow,
    PermissionResultDeny,
)

import agent
import chat_state
import compaction
import llm

HERE = pathlib.Path(__file__).parent
STATE_PATH = HERE / "chat_state.json"

# ----------------------------------------------------------------------
# Persona bundle (soul.md framework): SOUL = identity, STYLE = voice,
# examples/ = calibration. The research ENGINE (agent.py) reads ONLY SOUL.md;
# STYLE + examples are loaded HERE, into chat, so the voice never leaks into the
# engine's structured JSON output. SKILL.md (the engine method) is never loaded
# into chat — that separation is what keeps Scout human here and terse in jobs.
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
# compaction can never shrink — it only folds conversation history. So the budget
# is persona size + a conversation allowance, computed AFTER build_system_prompt()
# below. Enriching the persona then auto-raises the ceiling instead of silently
# making every turn "too large to fit" (which a flat 6k budget did once the soul.md
# bundle pushed the system prompt past 6k tokens on its own).
CONVERSATION_BUDGET_TOKENS = 8000  # headroom for summary + recent turns + new msg
SCOUT_TOOL_NAME = "mcp__scout__scout_research"     # how the model references the tool
SCOUT_MARKER = "SCOUT_REQUEST:"                    # strict fallback trigger

# Reference snapshot caps — keep Scout's memory awareness small and bounded.
MAX_SNAPSHOT_RUNS = 5
MAX_SNAPSHOT_WINS = 10

# How long we'll wait for a session-end distill before falling back to "pending".
# Kept short so exit feels instant; the no-data-loss path catches the timeout.
DISTILL_TIMEOUT_SEC = 25


# ----------------------------------------------------------------------
# Persona system prompt — built from SOUL only (no SKILL output contract)
# ----------------------------------------------------------------------
CHAT_ADDENDUM = """
## Right now: a live conversation
You're talking with the user directly, in real time — not producing a research
report. Speak like a real person with your expertise: natural, brief, in your own
voice. Do NOT emit numbered idea lists or structured report formatting here;
that's for jobs, not conversation.

## What you remember (be accurate about this)
You keep a distilled summary of what matters about this user across sessions —
their channel, niche, audience, preferences, the decisions you've made together,
and what your research has found — but NOT the word-for-word history of past
chats. So you are NOT meeting them for the first time and you do NOT start fresh
every session: use the remembered context you're given and sound like someone who
genuinely knows them and their channel. If asked what you remember, describe it
honestly: a running summary of the important stuff, not a transcript.

## Running research mid-chat
When real data would genuinely help the conversation, you can run a YouTube
research job: call the `scout_research` tool with a `niche`. The user is asked to
approve before it runs, so only call it when you mean it. When results come back,
talk through them in your own words — don't dump them raw.
"""


def build_system_prompt(soul_text: str = SOUL, style_text: str = STYLE,
                        examples_text: str = EXAMPLES) -> str:
    """Scout's chat identity: SOUL (who he is) + STYLE (how he talks) +
    examples/ (calibration) + live-conversation guidance.

    This is the soul.md persona bundle. It deliberately excludes SKILL.md (the
    research output contract) — that formula makes Scout terse and robotic in
    chat. STYLE and the examples are what make him sound human here.
    """
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


# Whole-prompt ceiling = fixed persona overhead + conversation allowance. Computed
# from the real built prompt so any persona enrichment self-adjusts the budget.
BUDGET_TOKENS = (compaction.estimate_tokens(build_system_prompt())
                 + CONVERSATION_BUDGET_TOKENS)


# ----------------------------------------------------------------------
# Distillation — the ONE memory helper, used on /exit, SIGINT, /new, /summary
# ----------------------------------------------------------------------
# Runs through the provider-agnostic llm.chat() seam (free/portable), NOT the
# Claude converse() seam, so it stays cheap and brain-swappable.
DISTILL_SYSTEM = (
    "You maintain the long-term memory of Viral Scout, a YouTube research analyst, "
    "about ONE creator he works with. That memory is a single distilled summary he "
    "reloads at the start of every session — so it must hold only what makes his "
    "help smarter, in as few words as possible."
)


def _distill_prompt(existing_summary: str, transcript: list[dict[str, str]]) -> str:
    convo = compaction.transcript_text(transcript)
    return (
        "Here is the memory you already hold about the creator:\n"
        f"{existing_summary.strip() or '(nothing yet)'}\n\n"
        "Here is the full transcript of the session that just happened:\n"
        f"{convo}\n\n"
        "Rewrite the memory as a single clean, consolidated summary.\n\n"
        "KEEP only durable, intelligence-improving signal:\n"
        "- the creator's channel name / identity\n"
        "- their niche and sub-angles\n"
        "- their audience\n"
        "- upload cadence\n"
        "- style and preferences (e.g. 'hates clickbait')\n"
        "- decisions made, research wins, and established facts about the channel\n\n"
        "DROP the junk: greetings and small talk ('nice weather', 'lol'), "
        "off-topic questions and his deflections, jailbreak / identity-test "
        "exchanges, and anything transient.\n\n"
        "MERGE with the memory you already hold — do not replace it; knowledge "
        "accumulates across sessions. Resolve contradictions in favor of the MOST "
        "RECENT information (if the niche changed, update it; don't keep both).\n\n"
        "Keep it BOUNDED and consolidated: a few tight bullet groups, well under "
        "600 words. Output ONLY the updated summary — no preamble, no commentary. "
        "If the session contained nothing worth keeping, return the existing "
        "memory unchanged."
    )


def make_distiller(chat_fn=llm.chat):
    """Build distill(existing_summary, transcript) -> new_summary from a chat seam.

    Injectable so tests can pass a fake chat function (no API). An empty
    transcript is a no-op that returns the existing summary verbatim.
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

    The backlog to fold = any previously-stranded "pending" turns + the live
    in-RAM transcript. On success: summary updated, transcript + pending cleared,
    summary persisted; returns True. On failure/timeout (NO DATA LOSS): the whole
    backlog is parked under "pending" in chat_state.json and the existing summary
    is kept; the in-RAM transcript is left intact (so a mid-session command like
    /summary can keep working) and returns False.
    """
    backlog = (state.get("pending") or []) + state["transcript"]
    if not backlog:
        # Nothing to distill — still persist the current summary cleanly.
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

    Retries the distill; on success the summary absorbs it and "pending" is
    cleared. On failure we keep "pending" untouched for a future launch — never
    dropping it. The in-RAM transcript stays empty either way (no replay).
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
# Memory awareness — a small, capped snapshot of research history
# ----------------------------------------------------------------------
def memory_snapshot(mem: dict) -> str:
    """A compact, clearly-labeled view of past runs + wins for Scout's context."""
    wins = list(mem.get("wins", []))[-MAX_SNAPSHOT_WINS:]
    runs = list(mem.get("runs", []))[-MAX_SNAPSHOT_RUNS:]
    lines = []
    if wins:
        lines.append("Topics the user recorded as wins: " + "; ".join(wins))
    if runs:
        lines.append("Recent research runs: "
                     + "; ".join(r.get("niche", "?") for r in runs))
    if not lines:
        return ""
    return "[Your research memory]\n" + "\n".join(lines)


# ----------------------------------------------------------------------
# Niche validation — don't burn API quota on empty / smashed input
# ----------------------------------------------------------------------
def validate_niche(niche: str) -> tuple[bool, str]:
    """Return (ok, reason). Rejects empty, too-short, and keyboard-smash niches."""
    n = (niche or "").strip()
    if len(n) < 3:
        return False, "That niche is too short — give me a few words to work with."
    letters = [c for c in n.lower() if c.isalpha()]
    if not letters:
        return False, "I need an actual topic, not symbols or numbers."
    # Best-effort keyboard-smash check: only judge single-word input (real niches
    # with a space are almost never smashes). A run of 5+ consecutive consonants
    # flags gibberish like "asdfkjh"/"asdfgh" while leaving real one-word niches
    # ("chess", "crypto", "fitness") alone. 'y' counts as a vowel here.
    if " " not in n:
        run = best = 0
        for c in n.lower():
            if c.isalpha() and c not in "aeiouy":
                run += 1
                best = max(best, run)
            else:
                run = 0
        if best >= 5:
            return False, "That looks like a keyboard smash — give me a real niche."
    return True, ""


# ----------------------------------------------------------------------
# Idea formatting for in-chat handoff (compact, not the raw CLI wall)
# ----------------------------------------------------------------------
def format_ideas(ideas: list, limit: int = 10) -> str:
    out = []
    for i, idea in enumerate(ideas[:limit], 1):
        titles = idea.get("titles") or []
        title = titles[0] if titles else "(untitled)"
        out.append(f"{i}. [{idea.get('confidence', '?')}] {title} — "
                   f"{idea.get('why', '')}")
    return "\n".join(out)


# ----------------------------------------------------------------------
# Strict marker parsing (provider-agnostic fallback trigger)
# ----------------------------------------------------------------------
def parse_scout_request(text: str) -> str | None:
    """Return the niche iff `text` ends with a single, exact marker line.

    Strict on purpose so a mid-text MENTION of the marker can't false-trigger:
    the marker line must (a) appear exactly once, (b) be the last non-empty line,
    (c) start the line, and (d) carry a non-empty niche.
    """
    lines = text.splitlines()
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty:
        return None
    marker_lines = [ln for ln in lines if ln.strip().startswith(SCOUT_MARKER)]
    if len(marker_lines) != 1:
        return None
    if marker_lines[0].strip() != nonempty[-1].strip():
        return None
    niche = marker_lines[0].strip()[len(SCOUT_MARKER):].strip()
    return niche or None


def strip_scout_request(text: str) -> str:
    """Remove any marker line so it isn't shown to the user."""
    kept = [ln for ln in text.splitlines() if not ln.strip().startswith(SCOUT_MARKER)]
    return "\n".join(kept).strip()


# ----------------------------------------------------------------------
# Approval gate (shared by the native tool and the marker fallback)
# ----------------------------------------------------------------------
def ask_yes_no(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# ----------------------------------------------------------------------
# The native research tool + approval callback (Claude path)
# ----------------------------------------------------------------------
@tool("scout_research", "Run a real YouTube research job for a niche and return "
      "ranked topic ideas with their data signals.", {"niche": str})
async def scout_research(args):
    niche = (args.get("niche") or "").strip()
    ok, reason = validate_niche(niche)
    if not ok:
        return {"content": [{"type": "text", "text": reason}], "is_error": True}
    try:
        # agent.run is sync and spins its own event loop -> run it off-thread so
        # it doesn't collide with the SDK's running loop.
        ideas = await asyncio.to_thread(agent.run, niche, True)  # quiet=True
    except Exception as exc:  # keep the conversation alive on any failure
        return {"content": [{"type": "text",
                             "text": f"Research failed: {exc}"}], "is_error": True}
    if not ideas:
        return {"content": [{"type": "text",
                             "text": f"No usable videos found for '{niche}'."}]}
    return {"content": [{"type": "text",
                         "text": f"Research results for '{niche}':\n"
                                 + format_ideas(ideas)}]}


async def can_use_tool(name, inp, ctx):
    """Intercept the tool call and ask the user before anything runs."""
    if name == SCOUT_TOOL_NAME:
        niche = (inp.get("niche") or "").strip()
        ok, reason = validate_niche(niche)
        if not ok:
            return PermissionResultDeny(behavior="deny", message=reason,
                                        interrupt=False)
        approved = await asyncio.to_thread(
            ask_yes_no, f"\n🔍 Scout wants to research '{niche}'. Run it? [y/N] ")
        if approved:
            print("   …running research (this can take a minute)…")
            return PermissionResultAllow(behavior="allow", updated_input=inp)
        return PermissionResultDeny(
            behavior="deny",
            message=f"The user declined to run research on '{niche}' right now.",
            interrupt=False)
    return PermissionResultDeny(behavior="deny",
                                message="That tool isn't allowed here.",
                                interrupt=False)


_SCOUT_SERVER = create_sdk_mcp_server("scout", tools=[scout_research])
SCOUT_WIRING = {"server": _SCOUT_SERVER, "can_use_tool": can_use_tool}


# ----------------------------------------------------------------------
# Context assembly + a single conversational turn
# ----------------------------------------------------------------------
def _context_summary(state: dict, snapshot: str) -> str:
    """Combine the distilled summary with the capped memory snapshot."""
    parts = [p for p in (state["summary"].strip(), snapshot.strip()) if p]
    return "\n\n".join(parts)


def _send(state, system, summarizer, snapshot, user_msg, *, scout):
    """Compact if needed, call the model, return Scout's reply text (or None).

    Compaction here is only an in-session budget guard: if the live transcript
    grows past BUDGET_TOKENS it folds the oldest turns into the in-RAM summary so
    the prompt stays bounded. The durable cross-session summary is still written
    only at session boundaries by distill_and_save.
    """
    info = compaction.compact(
        state, summarizer=summarizer, system=system, extra=snapshot,
        pending_user_msg=user_msg, budget=BUDGET_TOKENS)
    if not info["fits"]:
        print("⚠️  " + info["reason"])
        return None
    summary = _context_summary(state, snapshot)
    return llm.converse(system, summary, state["transcript"], user_msg, scout=scout)


def handle_message(state, system, summarizer, user_msg):
    """One user message -> Scout's reply, kept in the in-RAM transcript only.

    No per-turn persistence: the transcript lives in RAM for the session and is
    distilled into the durable summary on a session boundary.
    """
    mem = agent.load_memory()
    snapshot = memory_snapshot(mem)
    try:
        reply = _send(state, system, summarizer, snapshot, user_msg,
                      scout=SCOUT_WIRING)
    except Exception as exc:
        print(f"\n(Scout hit a problem: {exc}\n Try again, or /new if it persists.)")
        return
    if reply is None:
        return  # budget warning already printed

    niche = parse_scout_request(reply)
    display = strip_scout_request(reply) if niche else reply
    print(f"\nScout: {display}")

    # Keep the turn in working memory (store the cleaned reply so markers don't
    # pollute the eventual summary).
    chat_state.append_turn(state, "user", user_msg)
    chat_state.append_turn(state, "scout", display or reply)

    # Fallback path: the model emitted a marker instead of calling the tool.
    if niche:
        _research_then_discuss(state, system, summarizer, niche, gate=True)


def _research_then_discuss(state, system, summarizer, niche, *, gate):
    """Run research (with optional [y/N] gate) and let Scout discuss it in voice."""
    ok, reason = validate_niche(niche)
    if not ok:
        print(reason)
        return
    if gate and not ask_yes_no(
            f"\n🔍 Scout wants to research '{niche}'. Run it? [y/N] "):
        feedback = (f"[note] The user declined to run research on '{niche}'. "
                    "Acknowledge and keep talking.")
    else:
        print("   …running research…")
        try:
            ideas = agent.run(niche, quiet=True)
        except Exception as exc:
            print(f"   (research failed: {exc})")
            return
        if ideas:
            feedback = (f"[research results for '{niche}']\n{format_ideas(ideas)}\n"
                        "Present these to the user in your own voice.")
        else:
            feedback = f"[research results] No usable videos found for '{niche}'."

    # Feed results back as a USER-role turn (never as Scout) so roles stay clean.
    mem = agent.load_memory()
    snapshot = memory_snapshot(mem)
    try:
        reply = _send(state, system, summarizer, snapshot, feedback, scout=None)
    except Exception as exc:
        print(f"\n(Scout couldn't discuss the results: {exc})")
        return
    if reply:
        print(f"\nScout: {reply}")
        chat_state.append_turn(state, "user", feedback)
        chat_state.append_turn(state, "scout", reply)


# ----------------------------------------------------------------------
# Slash commands
# ----------------------------------------------------------------------
HELP = """Commands:
  /scout <niche>   run a research job now and talk it through
  /summary         distill the session so far, then show what Scout remembers
  /new             distill + start a fresh thread (keeps what Scout knows about you)
  /help            show this
  /exit            save (distill) and quit
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
        # Distill (capturing this session's facts) AND show the real result, so
        # /summary is an accurate, manually-triggerable checkpoint.
        ok = distill_and_save(state, distiller, status="💾 Updating what I remember…")
        body = state["summary"].strip() or "(nothing worth remembering yet)"
        print("\n[What Scout remembers about you]\n" + body)
        if not ok:
            print("(I couldn't fully update just now — kept what I had; your chat "
                  "is safe and I'll fold it in next launch.)")
    elif cmd == "/new":
        # Distill FIRST so this session's facts are captured, THEN clear the
        # in-RAM transcript while keeping the summary.
        distill_and_save(state, distiller,
                         status="💾 Saving what matters before clearing the thread…")
        state["transcript"] = []  # guarantee a fresh thread even if distill failed
        print("Fresh thread. I've folded this chat into what I remember about you "
              "— your channel, niche, preferences and our findings stay; the "
              "back-and-forth is cleared.")
    elif cmd == "/scout":
        if not arg:
            print("Usage: /scout <niche>")
        else:
            okn, reason = validate_niche(arg)
            if not okn:
                print(reason)
            else:
                _research_then_discuss(state, system, summarizer, arg, gate=False)
    else:
        print(f"Unknown command {cmd!r}. /help for the list.")
    return True


# ----------------------------------------------------------------------
# Graceful Ctrl+C (SIGINT) handling
# ----------------------------------------------------------------------
# The running session is shared with the signal handler through this dict so a
# Ctrl+C anywhere (at the prompt, mid-turn, mid-research) can save before exit.
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

    A SECOND Ctrl+C arriving during the distill (impatient double-tap) force-exits
    immediately, but flushes the raw transcript to "pending" first so nothing is
    lost.
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

    # Wire the SIGINT handler to this session, then fold in any recovery backlog.
    _SESSION.update(state=state, distiller=distiller, interrupting=False)
    signal.signal(signal.SIGINT, _sigint_handler)
    _recover_pending(state, distiller)

    print("=" * 64)
    print("Talk to Viral Scout.  /help for commands, /exit to leave.")
    if state["summary"].strip():
        print("(Scout remembers what matters about you from before — pick up "
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
