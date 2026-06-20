"""Talk to Sage — a conversational REPL over the same soul/identity.

Launch:  python run.py chat

Sage talks like a person (persona from SOUL.md only — NOT the SKILL research
output contract), remembers you across sessions via a single distilled SUMMARY,
knows your research history (memory.json), and can run a REAL research job
mid-conversation via an in-process tool that you approve before it runs.

Memory model (summary-only — no transcript replay across sessions):
- Across sessions, Sage's only long-term memory is a distilled summary in
  chat_state.json. The raw transcript is NOT persisted between sessions.
- DURING a session the full transcript lives in RAM (state["transcript"]) so Sage
  has normal working memory of the live conversation.
- On every session boundary (/exit, Ctrl+C, /new, /summary) we run ONE helper,
  distill(existing_summary, transcript) -> new_summary, fold the session into the
  summary, drop the junk, clear the raw transcript, and persist only the summary.
- No data loss: if distill fails/times out we park the raw transcript under
  "pending" in chat_state.json and fold it in on the next launch.

Design notes:
- Durable state is OUR chat_state.json, not a Claude session id (provider-portable).
- distill() uses the provider-agnostic llm.chat() seam, so distillation stays
  cheap and brain-swappable.
- Research mid-chat uses the SDK's native tool + can_use_tool approval gate. A
  strict text marker ("SAGE_REQUEST: <topic>") is kept as a provider-agnostic
  fallback for brains without tools.
- The research engine (researcher.run) is synchronous and spins its own event
  loop, so we call it via asyncio.to_thread to avoid clashing with the SDK's loop.
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
import factcheck as factcheck_engine
import llm
import researcher

HERE = pathlib.Path(__file__).parent
STATE_PATH = HERE / "chat_state.json"

# ----------------------------------------------------------------------
# Persona bundle (soul.md framework): SOUL = identity, STYLE = voice,
# examples/ = calibration. The research ENGINE (researcher.py) reads ONLY
# SOUL.md; STYLE + examples are loaded HERE, into chat, so the voice never leaks
# into the engine's structured research pack. SKILL.md (the engine method) is
# never loaded into chat — that separation keeps Sage human here, rigorous in jobs.
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
SAGE_TOOL_NAME = "mcp__sage__research_topic"        # how the model references the tool
SAGE_FACTCHECK_TOOL = "mcp__sage__factcheck_script"  # pass-2 tool (model-initiated)
SAGE_MARKER = "SAGE_REQUEST:"                       # strict fallback trigger

# Reference snapshot cap — keep Sage's memory awareness small and bounded.
MAX_SNAPSHOT_RUNS = 5

# How long we'll wait for a session-end distill before falling back to "pending".
# Kept short so exit feels instant; the no-data-loss path catches the timeout.
DISTILL_TIMEOUT_SEC = 25


# ----------------------------------------------------------------------
# Persona system prompt — built from SOUL only (NO SKILL output contract)
# ----------------------------------------------------------------------
CHAT_ADDENDUM = """
## Right now: a live conversation
You're talking with the user directly, in real time — not producing a research
pack. Speak like a real person with your expertise: natural, measured, in your own
voice. Do NOT emit the structured research-pack format (verified facts / myths /
sources sections) here; that's for jobs, not conversation. You can still be
rigorous and cite what you know — just talk like a person.

## What you remember (be accurate about this)
You keep a distilled summary of what matters about this user across sessions — the
topics they work on, their interests and standards (how rigorous/skeptical they
want sourcing to be), the decisions you've made together, and useful findings —
but NOT the word-for-word history of past chats. So you are NOT meeting them for
the first time and you do NOT start fresh every session: use the remembered
context you're given and sound like someone who genuinely knows them. If asked
what you remember, describe it honestly: a running summary of the important stuff,
not a transcript.

## Running research mid-chat
When real investigation would genuinely help the conversation, you can run a full
research job: call the `research_topic` tool with a `topic`. The user is asked to
approve before it runs, so only call it when you mean it. When results come back,
talk through them in your own words — lead with what's verified, flag what's
contested or a myth, and attribute — don't dump the raw pack.

## Fact-checking a drafted script (your second hat)
When there's a written script to check against a research brief, you can run a
fact-check: call the `factcheck_script` tool with a `path` (a project directory, or
a script.json). The user approves before it runs. Here you're adversarial toward the
*script*, not the world — it's guilty until sourced. When the report comes back, lead
with the verdict, then walk the flagged/unverifiable claims: for each, name exactly
why it doesn't hold and the one fix. You flag and route back; you do NOT rewrite the
script — that's the writer's job.
"""


def build_system_prompt(soul_text: str = SOUL, style_text: str = STYLE,
                        examples_text: str = EXAMPLES) -> str:
    """Sage's chat identity: SOUL (who he is) + STYLE (how he talks) +
    examples/ (calibration) + live-conversation guidance.

    This is the soul.md persona bundle. It deliberately excludes SKILL.md (the
    research output contract) — that pack format makes Sage terse and robotic in
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
# Runs through the provider-agnostic llm.chat() seam so it stays cheap and
# brain-swappable.
DISTILL_SYSTEM = (
    "You maintain the long-term memory of Sage, a rigorous investigative "
    "researcher, about ONE collaborator he works with. That memory is a single "
    "distilled summary he reloads at the start of every session — so it must hold "
    "only what makes his help smarter, in as few words as possible."
)


def _distill_prompt(existing_summary: str, transcript: list[dict[str, str]]) -> str:
    convo = compaction.transcript_text(transcript)
    return (
        "Here is the memory you already hold about the collaborator:\n"
        f"{existing_summary.strip() or '(nothing yet)'}\n\n"
        "Here is the full transcript of the session that just happened:\n"
        f"{convo}\n\n"
        "Rewrite the memory as a single clean, consolidated summary.\n\n"
        "KEEP only durable, intelligence-improving signal:\n"
        "- the topics / subjects they research\n"
        "- their interests and what angles they care about\n"
        "- their standards (how skeptical / how authoritative they want sources)\n"
        "- decisions made, established findings, and conclusions reached\n"
        "- anything about how they like to work\n\n"
        "DROP the junk: greetings and small talk ('thanks', 'lol'), off-topic "
        "questions and his deflections, jailbreak / identity-test exchanges, and "
        "anything transient.\n\n"
        "MERGE with the memory you already hold — do not replace it; knowledge "
        "accumulates across sessions. Resolve contradictions in favor of the MOST "
        "RECENT information (if their focus changed, update it; don't keep both).\n\n"
        "Keep it BOUNDED and consolidated: a few tight bullet groups, well under "
        "600 words. Output ONLY the updated summary — no preamble, no commentary. "
        "If the session contained nothing worth keeping, return the existing "
        "memory unchanged."
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
    in-RAM transcript is left intact (so a mid-session command like /summary keeps
    working) and returns False.
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

    Retries the distill; on success the summary absorbs it and "pending" is cleared.
    On failure we keep "pending" untouched for a future launch — never dropping it.
    The in-RAM transcript stays empty either way (no replay).
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
    """A compact, clearly-labeled view of past research runs for Sage's context."""
    runs = list(mem.get("runs", []))[-MAX_SNAPSHOT_RUNS:]
    if not runs:
        return ""
    items = []
    for r in runs:
        topic = r.get("topic", "?")
        angle = r.get("angle", "")
        items.append(f"{topic} ({angle})" if angle else topic)
    return "[Your research memory]\nRecent research runs: " + "; ".join(items)


# ----------------------------------------------------------------------
# Compact pack presentation for in-chat handoff (not the raw wall)
# ----------------------------------------------------------------------
def format_pack_brief(pack: dict, limit: int = 5) -> str:
    """A short, in-character-friendly digest of a research pack for Sage to discuss.

    NOT the full pack — just enough signal for him to talk it through in his own
    voice. The full pack is already saved to research_packs/ by the engine.
    """
    out = []
    if pack.get("overview"):
        out.append(f"Overview: {pack['overview']}")

    vf = pack.get("verified_facts") or []
    if vf:
        out.append("\nVerified (multiple credible sources):")
        for f in vf[:limit]:
            out.append(f"  - [{f.get('confidence', '?')}] {f.get('claim', '')}")

    myths = pack.get("myths_and_corrections") or []
    if myths:
        out.append("\nMyths / corrections:")
        for m in myths[:limit]:
            out.append(f"  - MYTH: {m.get('myth', '')}  →  {m.get('correction', '')}")

    contested = pack.get("contested_or_uncertain") or []
    if contested:
        out.append("\nContested / uncertain:")
        for c in contested[:limit]:
            out.append(f"  - {c.get('claim', '')}  (why: {c.get('why', '')})")

    oq = pack.get("open_questions") or []
    if oq:
        out.append("\nOpen questions: " + "; ".join(str(q) for q in oq[:limit]))

    n_src = len(pack.get("sources") or [])
    out.append(f"\n({n_src} sources gathered; full pack saved to research_packs/.)")
    return "\n".join(out)


# ----------------------------------------------------------------------
# Pass-2 fact-check: load the inputs, run the engine, format a digest
# ----------------------------------------------------------------------
def load_factcheck_inputs(path: str) -> tuple[dict, dict]:
    """Resolve `path` to (script, brief) dicts.

    `path` may be a project directory (holding script.json + research_brief.json) or
    a script.json file (the brief is read from its sibling). Returns ({}, {}) for the
    side that's missing so the caller can report a clean error.
    """
    p = pathlib.Path(path).expanduser()
    if p.is_dir():
        script_p, brief_p = p / "script.json", p / "research_brief.json"
    else:
        script_p, brief_p = p, p.parent / "research_brief.json"
    return (chat_state.load_json(script_p, {}), chat_state.load_json(brief_p, {}))


def run_factcheck(path: str) -> dict:
    """Read script + brief at `path` and return the factcheck report dict.

    Raises ValueError with a plain message when the inputs aren't usable, so the REPL
    apologises instead of dumping a traceback.
    """
    script, brief = load_factcheck_inputs(path)
    if not script.get("scenes"):
        raise ValueError(f"No usable script.json at {path!r} (need a script with scenes).")
    if not (brief.get("verified_facts") or brief.get("sources")):
        raise ValueError(f"No usable research_brief.json next to {path!r} — the brief "
                         "is the ground truth I check against.")
    return factcheck_engine.factcheck(script, brief, quiet=True)


def format_factcheck_brief(report: dict, limit: int = 8) -> str:
    """A compact, in-character-friendly digest of a factcheck report for Sage."""
    summary = report.get("summary", {})
    verdict = report.get("verdict", "?")
    out = [f"Verdict: {verdict.upper()}  "
           f"(verified {summary.get('verified', 0)}, "
           f"flagged {summary.get('flagged', 0)}, "
           f"unverifiable {summary.get('unverifiable', 0)})"]
    problems = [c for c in report.get("claims", [])
                if c.get("status") in ("flagged", "unverifiable")]
    if problems:
        out.append("\nClaims that don't hold:")
        for c in problems[:limit]:
            out.append(f"  - [{c.get('status')}] {c.get('claim_id')} "
                       f"(scene {c.get('scene_no')}): {c.get('claim_text','')}\n"
                       f"      fix: {c.get('note','')}")
    else:
        out.append("\nEvery claim checks out against the brief.")
    return "\n".join(out)


# ----------------------------------------------------------------------
# Strict marker parsing (provider-agnostic fallback trigger)
# ----------------------------------------------------------------------
def parse_sage_request(text: str) -> str | None:
    """Return the topic iff `text` ends with a single, exact marker line.

    Strict on purpose so a mid-text MENTION of the marker can't false-trigger: the
    marker line must (a) appear exactly once, (b) be the last non-empty line,
    (c) start the line, and (d) carry a non-empty topic.
    """
    lines = text.splitlines()
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty:
        return None
    marker_lines = [ln for ln in lines if ln.strip().startswith(SAGE_MARKER)]
    if len(marker_lines) != 1:
        return None
    if marker_lines[0].strip() != nonempty[-1].strip():
        return None
    topic = marker_lines[0].strip()[len(SAGE_MARKER):].strip()
    return topic or None


def strip_sage_request(text: str) -> str:
    """Remove any marker line so it isn't shown to the user."""
    kept = [ln for ln in text.splitlines() if not ln.strip().startswith(SAGE_MARKER)]
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
# "Thinking…" indicator — model calls block for several seconds (first reply
# also cold-starts the SDK subprocess), so without this the REPL looks frozen.
# ----------------------------------------------------------------------
_spinner: tuple | None = None  # (stop_event, thread) of the active spinner, or None


def _start_thinking(label: str = "Sage is thinking") -> None:
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
# The native research tool + approval callback (Claude path)
# ----------------------------------------------------------------------
@tool("research_topic", "Run a real research job on a topic and return a structured "
      "digest: verified facts, myths, contested claims, and sources.",
      {"topic": str})
async def research_topic(args):
    topic = (args.get("topic") or "").strip()
    ok, reason = researcher.validate_topic(topic)
    if not ok:
        return {"content": [{"type": "text", "text": reason}], "is_error": True}
    try:
        # researcher.run is sync and spins its own event loop -> run it off-thread
        # so it doesn't collide with the SDK's running loop.
        pack, _json_path, _md_path = await asyncio.to_thread(
            researcher.run, topic, None, True)  # angle=None, quiet=True
    except Exception as exc:  # keep the conversation alive on any failure
        return {"content": [{"type": "text",
                             "text": f"Research failed: {exc}"}], "is_error": True}
    return {"content": [{"type": "text",
                         "text": f"Research results for '{topic}':\n"
                                 + format_pack_brief(pack)}]}


@tool("factcheck_script", "Fact-check a drafted script's claims against its research "
      "brief and return a verdict (pass/block) with per-claim status. Pass a project "
      "directory or a script.json path.",
      {"path": str})
async def factcheck_script(args):
    path = (args.get("path") or "").strip()
    try:
        # run_factcheck is sync (and the engine may spin its own loop) -> off-thread.
        report = await asyncio.to_thread(run_factcheck, path)
    except Exception as exc:  # keep the conversation alive on any failure
        return {"content": [{"type": "text",
                             "text": f"Fact-check couldn't run: {exc}"}], "is_error": True}
    return {"content": [{"type": "text",
                         "text": f"Fact-check report for {path!r}:\n"
                                 + format_factcheck_brief(report)}]}


async def can_use_tool(name, inp, ctx):
    """Intercept the tool call and ask the user before anything runs."""
    if name == SAGE_TOOL_NAME:
        _stop_thinking()  # clear the spinner before we prompt the user
        topic = (inp.get("topic") or "").strip()
        ok, reason = researcher.validate_topic(topic)
        if not ok:
            return PermissionResultDeny(behavior="deny", message=reason,
                                        interrupt=False)
        approved = await asyncio.to_thread(
            ask_yes_no, f"\n🔍 Sage wants to research '{topic}'. Run it? [y/N] ")
        if approved:
            print("   …running research (this can take a minute)…")
            return PermissionResultAllow(behavior="allow", updated_input=inp)
        return PermissionResultDeny(
            behavior="deny",
            message=f"The user declined to run research on '{topic}' right now.",
            interrupt=False)
    if name == SAGE_FACTCHECK_TOOL:
        _stop_thinking()
        path = (inp.get("path") or "").strip()
        if not path:
            return PermissionResultDeny(behavior="deny",
                                        message="No script path was given to fact-check.",
                                        interrupt=False)
        approved = await asyncio.to_thread(
            ask_yes_no, f"\n🔎 Sage wants to fact-check '{path}'. Run it? [y/N] ")
        if approved:
            print("   …fact-checking the script against the brief…")
            return PermissionResultAllow(behavior="allow", updated_input=inp)
        return PermissionResultDeny(
            behavior="deny",
            message=f"The user declined to fact-check '{path}' right now.",
            interrupt=False)
    return PermissionResultDeny(behavior="deny",
                                message="That tool isn't allowed here.",
                                interrupt=False)


_SAGE_SERVER = create_sdk_mcp_server("sage", tools=[research_topic, factcheck_script])
SAGE_WIRING = {"server": _SAGE_SERVER, "can_use_tool": can_use_tool}


# ----------------------------------------------------------------------
# Context assembly + a single conversational turn
# ----------------------------------------------------------------------
def _context_summary(state: dict, snapshot: str) -> str:
    """Combine the distilled summary with the capped memory snapshot."""
    parts = [p for p in (state["summary"].strip(), snapshot.strip()) if p]
    return "\n\n".join(parts)


def _send(state, system, summarizer, snapshot, user_msg, *, sage):
    """Compact if needed, call the model, return Sage's reply text (or None).

    Compaction here is only an in-session budget guard: if the live transcript
    grows past BUDGET_TOKENS it folds the oldest turns into the in-RAM summary so
    the prompt stays bounded. The durable cross-session summary is still written
    only at session boundaries by distill_and_save.
    """
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
        return llm.converse(system, summary, state["transcript"], user_msg, sage=sage)
    finally:
        _stop_thinking()


def handle_message(state, system, summarizer, user_msg):
    """One user message -> Sage's reply, kept in the in-RAM transcript only.

    No per-turn persistence: the transcript lives in RAM for the session and is
    distilled into the durable summary on a session boundary.
    """
    mem = researcher.load_memory()
    snapshot = memory_snapshot(mem)
    try:
        reply = _send(state, system, summarizer, snapshot, user_msg, sage=SAGE_WIRING)
    except Exception as exc:
        print(f"\n(Sage hit a problem: {exc}\n Try again, or /new if it persists.)")
        return
    if reply is None:
        return  # budget warning already printed

    topic = parse_sage_request(reply)
    display = strip_sage_request(reply) if topic else reply
    print(f"\nSage: {display}")

    # Keep the turn in working memory (store the cleaned reply so markers don't
    # pollute the eventual summary).
    chat_state.append_turn(state, "user", user_msg)
    chat_state.append_turn(state, "sage", display or reply)

    # Fallback path: the model emitted a marker instead of calling the tool.
    if topic:
        _research_then_discuss(state, system, summarizer, topic, gate=True)


def _research_then_discuss(state, system, summarizer, topic, *, gate):
    """Run research (with optional [y/N] gate) and let Sage discuss it in voice."""
    ok, reason = researcher.validate_topic(topic)
    if not ok:
        print(reason)
        return
    if gate and not ask_yes_no(
            f"\n🔍 Sage wants to research '{topic}'. Run it? [y/N] "):
        feedback = (f"[note] The user declined to run research on '{topic}'. "
                    "Acknowledge and keep talking.")
    else:
        print("   …running research…")
        try:
            pack, _json_path, _md_path = researcher.run(topic, quiet=True)
        except Exception as exc:
            print(f"   (research failed: {exc})")
            return
        feedback = (f"[research results for '{topic}']\n{format_pack_brief(pack)}\n"
                    "Present these to the user in your own voice — lead with what's "
                    "verified, flag myths and contested claims, and attribute.")

    # Feed results back as a USER-role turn (never as Sage) so roles stay clean.
    mem = researcher.load_memory()
    snapshot = memory_snapshot(mem)
    try:
        reply = _send(state, system, summarizer, snapshot, feedback, sage=None)
    except Exception as exc:
        print(f"\n(Sage couldn't discuss the results: {exc})")
        return
    if reply:
        print(f"\nSage: {reply}")
        chat_state.append_turn(state, "user", feedback)
        chat_state.append_turn(state, "sage", reply)


def _factcheck_then_discuss(state, system, summarizer, path, *, gate):
    """Run a fact-check (optional [y/N] gate) and let Sage discuss the verdict in voice."""
    if gate and not ask_yes_no(
            f"\n🔎 Sage wants to fact-check '{path}'. Run it? [y/N] "):
        feedback = (f"[note] The user declined to fact-check '{path}'. "
                    "Acknowledge and keep talking.")
    else:
        print("   …fact-checking the script against the brief…")
        try:
            report = run_factcheck(path)
        except Exception as exc:
            print(f"   (fact-check failed: {exc})")
            return
        feedback = (f"[factcheck report for {path!r}]\n{format_factcheck_brief(report)}\n"
                    "Present this to the user in your own voice — lead with the verdict, "
                    "then for each flagged/unverifiable claim name why it doesn't hold "
                    "and the one fix. You flag and route back; you don't rewrite the script.")

    mem = researcher.load_memory()
    snapshot = memory_snapshot(mem)
    try:
        reply = _send(state, system, summarizer, snapshot, feedback, sage=None)
    except Exception as exc:
        print(f"\n(Sage couldn't discuss the fact-check: {exc})")
        return
    if reply:
        print(f"\nSage: {reply}")
        chat_state.append_turn(state, "user", feedback)
        chat_state.append_turn(state, "sage", reply)


# ----------------------------------------------------------------------
# Slash commands
# ----------------------------------------------------------------------
HELP = """Commands:
  /research <topic>   run a research job now and talk it through
  /factcheck <path>   fact-check a drafted script (project dir or script.json) vs its brief
  /summary            distill the session so far, then show what Sage remembers
  /new                distill + start a fresh thread (keeps what Sage knows about you)
  /help               show this
  /exit               save (distill) and quit
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
        print("\n[What Sage remembers about you]\n" + body)
        if not ok:
            print("(I couldn't fully update just now — kept what I had; your chat "
                  "is safe and I'll fold it in next launch.)")
    elif cmd == "/new":
        # Distill FIRST so this session's facts are captured, THEN clear the in-RAM
        # transcript while keeping the summary.
        distill_and_save(state, distiller,
                         status="💾 Saving what matters before clearing the thread…")
        state["transcript"] = []  # guarantee a fresh thread even if distill failed
        print("Fresh thread. I've folded this chat into what I remember about you "
              "— the topics, your standards and our findings stay; the back-and-"
              "forth is cleared.")
    elif cmd == "/research":
        if not arg:
            print("Usage: /research <topic>")
        else:
            okn, reason = researcher.validate_topic(arg)
            if not okn:
                print(reason)
            else:
                _research_then_discuss(state, system, summarizer, arg, gate=False)
    elif cmd == "/factcheck":
        if not arg:
            print("Usage: /factcheck <project_dir or script.json>")
        else:
            # Explicit command: typing it IS the approval (mirrors /research). The
            # [y/N] gate lives on the model-initiated factcheck_script tool.
            _factcheck_then_discuss(state, system, summarizer, arg, gate=False)
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
    immediately, but flushes the raw transcript to "pending" first so nothing is lost.
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
    print("Talk to Sage.  /help for commands, /exit to leave.")
    if state["summary"].strip():
        print("(Sage remembers what matters about you from before — pick up "
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
