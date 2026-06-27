"""Atlas — the web meeting room (the single chat UI).

The primary frontend over the shared session core (session.AtlasSession). The terminal
REPL (chat.py) is a thin dev fallback over the SAME core. This file owns only browser
I/O; all orchestration/memory logic lives in session.py + the orchestrator, and the
agent engines are untouched.

Atlas is the SOLE orchestrator: there is no pipeline and no gate-button machinery.
Approvals are CONVERSATIONAL — Atlas runs the production playbook by calling its team's
tools, stops at the fact-check checkpoint to tell you the verdict, and asks in plain
chat before the final render. You just talk to it.

Run it (from the atlas/ directory, with the shared venv active):

    chainlit run web/app.py -w        # -> http://localhost:8000

Streams a meeting turn live — Atlas's words (including the "🧠 I'm going with …"
decision lines) and the deterministic 🔎/✅ status lines as they happen. The same
summary-only memory lifecycle as the REPL: /new, /summary, /help, /agents work here
too, and closing the tab distills (or parks) the meeting so nothing is lost.

NOTE (single-operator local v1): the web UI and the terminal REPL share ONE
chat_state.json. Atomic writes prevent corruption, but don't run BOTH against the
same memory at the same time (last writer wins). See web/README.md.

The streaming bridge (verified against chainlit 2.11.1): session.send() is
synchronous and the orchestrator calls asyncio.run() inside it, so it must run in a
worker thread (cl.make_async). Its on_text/on_status callbacks fire on that worker
thread; we marshal each event back to the main event loop with
loop.call_soon_threadsafe onto an asyncio.Queue, and drain that queue on the main
loop so every cl.Message update happens in Chainlit's own context. This keeps the
loop responsive through multi-minute production runs.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

# Atlas's modules import each other by bare name (like the sibling agents), so put
# the atlas/ dir on sys.path no matter where chainlit is launched from.
ATLAS_DIR = str(pathlib.Path(__file__).resolve().parent.parent)
if ATLAS_DIR not in sys.path:
    sys.path.insert(0, ATLAS_DIR)

import chainlit as cl  # noqa: E402

import registry  # noqa: E402
import session as _session  # noqa: E402
import tools  # noqa: E402
from llm import effective_provider  # noqa: E402

tools.configure_logging()  # start_project arg-logs -> atlas/atlas.log (never stdout)

ATLAS_PROFILE = "Atlas — Showrunner"
# Per-agent persona chats keep their own summary-only memory web-locally, SEPARATE
# from each sibling's own terminal chat_state.json (zero sibling changes).
WEB_STATE_DIR = pathlib.Path(_session.HERE) / "web_sessions"

_ADAPTERS = None


def _adapters():
    global _ADAPTERS
    if _ADAPTERS is None:
        _ADAPTERS = registry.build_adapters()
    return _ADAPTERS


def _entry_for_profile(profile: str):
    for e in registry.REGISTRY:
        if e.display == profile:
            return e
    return None


def _build_session(profile: str):
    """Construct the session for a profile (called once per profile by the registry;
    later returns are cached -> RESUME). Atlas gets the orchestrator session; an agent
    gets a persona session over adapter.ask with its own web-local memory."""
    entry = _entry_for_profile(profile)
    if entry is None:  # Atlas (or an unknown profile) -> the Showrunner session
        return _session.AtlasSession.start()
    WEB_STATE_DIR.mkdir(parents=True, exist_ok=True)
    return _session.AgentSession.start(
        entry, _adapters()[entry.name],
        state_path=WEB_STATE_DIR / f"{entry.name}.json")


# Process-level cache so switching ChatProfiles RESUMES a session (transcript intact)
# instead of cold-starting it — `cl.user_session` is wiped on a profile switch.
_REGISTRY = _session.SessionRegistry(build=_build_session)


# ----------------------------------------------------------------------
# Phase C v2 — Marlow's job-gate as a BUTTON (reference for the other specialists).
#
# Marlow's co-worker REPL gates a model-initiated script write behind a [y/N] prompt.
# scriptwriter/chat.py now routes that gate through an INJECTABLE approver (default =
# terminal input(), unchanged). Here the web injects a button approver: a sync
# (prompt)->bool that, from a worker thread, shows a Chainlit approve/deny button on
# the main loop and blocks for the click. Same seam, button instead of input().
# scriptwriter/chat.py is loaded via the isolating loader (its `llm`/`chat_state` don't
# collide with Atlas's), so this needs ZERO further sibling changes.
# ----------------------------------------------------------------------
_MARLOW_CHAT = None


def _marlow_chat():
    global _MARLOW_CHAT
    if _MARLOW_CHAT is None:
        from adapters.loader import load_engine
        sw = pathlib.Path(_session.HERE).parent / "scriptwriter"
        _MARLOW_CHAT = load_engine(sw, "chat")
    return _MARLOW_CHAT


def _resolve_brief_path(arg: str):
    """Resolve a /write argument to a project dir or research_brief.json on disk."""
    arg = (arg or "").strip()
    if not arg:
        return None
    p = pathlib.Path(arg)
    if p.exists():
        return str(p)
    cand = pathlib.Path(_session.PROJECTS_DIR) / arg
    return str(cand) if cand.exists() else None


def _make_button_approver(loop):
    """A sync (prompt)->bool — called from a worker thread by the gate seam — that
    shows a Chainlit approve/deny button on the main loop and blocks for the click."""
    def approver(prompt: str) -> bool:
        async def ask():
            res = await cl.AskActionMessage(
                content=prompt.strip(),
                actions=[cl.Action(name="yes", payload={}, label="✅ Write it"),
                         cl.Action(name="no", payload={}, label="✖ Not now")],
                timeout=300).send()
            return bool(res and res.get("name") == "yes")
        try:
            return asyncio.run_coroutine_threadsafe(ask(), loop).result()
        except Exception:  # noqa: BLE001 — any bridge failure = decline (safe default)
            return False
    return approver


def _run_gated_write(mod, path: str):
    """Worker-thread body: route through the SAME injectable seam the terminal uses
    (`_approve` -> the injected button), and only write on approval."""
    if not mod._approve(path):          # shows the button, blocks for the click
        return ("declined", None)
    try:
        script, jpath = mod.run_write(path)
    except Exception as exc:  # noqa: BLE001
        return ("error", str(exc))
    return ("written", (script, str(jpath)))


async def _marlow_write(sess, arg: str) -> None:
    display = sess.entry.display
    mod = await cl.make_async(_marlow_chat)()
    path = _resolve_brief_path(arg)
    if path is None:
        await cl.Message(
            f"_(Couldn't find a project or brief at `{arg}`. Try `/write <project-slug>` "
            "— a folder under `projects/` with a `research_brief.json`.)_",
            author=display).send()
        return
    loop = asyncio.get_running_loop()
    mod.set_approver(_make_button_approver(loop))
    try:
        outcome, payload = await cl.make_async(_run_gated_write)(mod, path)
    finally:
        mod.reset_approver()  # always restore (the terminal default is untouched anyway)

    if outcome == "declined":
        await cl.Message("Stood down — no script written. Say the word when you're ready.",
                         author=display).send()
    elif outcome == "error":
        await cl.Message(f"_(Couldn't write it: {payload}.)_", author=display).send()
    else:
        script, jpath = payload
        brief = mod.format_script_brief(script)
        await cl.Message(f"✍️ **Script written** → `{jpath}`\n\n{brief}",
                         author=display).send()


@cl.set_chat_profiles
async def chat_profiles(current_user=None):
    profiles = [cl.ChatProfile(
        name=ATLAS_PROFILE, default=True,
        markdown_description="🧭 Run the whole team — delegate, hold the gates, drive "
                             "production end to end.")]
    for e in registry.REGISTRY:
        profiles.append(cl.ChatProfile(
            name=e.display,
            markdown_description=f"{e.emoji} **{e.role}** — {e.blurb}"))
    return profiles


# ----------------------------------------------------------------------
# Slash commands — parity with the REPL's memory lifecycle + roster.
# ----------------------------------------------------------------------
def _agents_text() -> str:
    ready = sum(1 for e in registry.REGISTRY if not e.stub)
    stub = sum(1 for e in registry.REGISTRY if e.stub)
    lines = [f"**The team — {len(registry.REGISTRY)} roles "
             f"({ready} ready, {stub} stub slots):**", ""]
    for e in registry.REGISTRY:
        caps = ", ".join(j.tool for j in e.jobs) + (", ask" if e.persona else "")
        lines.append(f"- {e.emoji} **{e.display}** ({e.name}) — {e.role}  ·  "
                     f"{'ready' if not e.stub else 'stub'}  \n  {e.blurb}  \n  `[{caps}]`")
    lines.append("")
    lines.append(f"_Atlas's own brain: {effective_provider()} "
                 "(jobs run on each agent's own engine/provider)._")
    return "\n".join(lines)


HELP_TEXT = (
    "**Commands**\n"
    "- `/agents` — who's on the team and what each does\n"
    "- `/summary` — distill the meeting so far, then show what Atlas remembers\n"
    "- `/new` — distill + start a fresh thread (keeps what Atlas knows)\n"
    "- `/help` — show this\n\n"
    "Anything else is a message to Atlas — it'll delegate, route, or answer. "
    "Closing the tab saves (distills) the meeting automatically."
)


async def _handle_command(sess, display: str, raw: str) -> bool:
    """Return True if the input was a command (already handled), else False. Works for
    Atlas and for any per-agent session (both share the summary-only lifecycle)."""
    cmd = raw.strip().split(maxsplit=1)[0].lower()
    if cmd == "/help":
        await cl.Message(HELP_TEXT, author=display).send()
    elif cmd == "/agents":
        await cl.Message(_agents_text(), author=display).send()
    elif cmd == "/summary":
        ok, body = await cl.make_async(sess.summarize)()
        body = body or "_(nothing worth remembering yet)_"
        note = ("" if ok else "\n\n_(Couldn't fully update just now — kept what I had; "
                "it's safe and I'll fold it in next launch.)_")
        await cl.Message(f"**What {display} remembers**\n\n{body}{note}",
                         author=display).send()
    elif cmd == "/new":
        await cl.make_async(sess.new_thread)()
        await cl.Message("Fresh thread. I've folded this into what I remember — the "
                         "decisions and preferences stay; the back-and-forth is cleared.",
                         author=display).send()
    else:
        return False
    return True


# ----------------------------------------------------------------------
# Session lifecycle
# ----------------------------------------------------------------------
@cl.on_chat_start
async def on_chat_start():
    # Which profile? (None on first load -> Atlas.) RESUME its cached session if we've
    # seen it this process; else build it. Off the loop so a first-time Atlas build /
    # recovery-distill never blocks the first paint.
    profile = cl.user_session.get("chat_profile") or ATLAS_PROFILE
    resuming = _REGISTRY.has(profile)
    sess = await cl.make_async(_REGISTRY.get)(profile)
    cl.user_session.set("sess", sess)
    cl.user_session.set("profile", profile)

    if isinstance(sess, _session.AtlasSession):
        present = "  ".join(f"{e.emoji} {e.display}" for e in registry.REGISTRY)
        hello = ["**Atlas — the meeting room.**  `/help` · pick a teammate from the "
                 "profile menu (top-left) to talk to them directly.",
                 f"In the room: {present}"]
        if resuming and sess.state["transcript"]:
            hello.append("\n_(Resuming — I still have our conversation in mind.)_")
        elif sess.summary.strip():
            hello.append("\n_(Atlas remembers what matters from before — pick up "
                         "wherever you like.)_")
        await cl.Message("\n\n".join(hello), author="Atlas").send()
    else:
        e = sess.entry
        hello = [f"**{e.emoji} {e.display} — {e.role}.**  {e.blurb}",
                 "_Chatting directly with this teammate. `/help` for commands; switch "
                 "profiles (top-left) to talk to someone else or back to Atlas._"]
        if e.name == "scriptwriter":
            hello.append("_Ask me to write, or run `/write <project-slug>` — I'll ask "
                         "you to approve before I write the script._")
        if resuming and sess.state["transcript"]:
            hello.append(f"\n_(Resuming — {e.display} still has our chat in mind.)_")
        elif sess.summary.strip():
            hello.append(f"\n_({e.display} remembers your earlier chats.)_")
        await cl.Message("\n\n".join(hello), author=e.display).send()


@cl.on_chat_end
async def on_chat_end():
    """Disconnect (tab close OR a profile switch — both reconnect the websocket). PARK
    every cached session's backlog: no data loss, and crucially the live transcript is
    PRESERVED in the cached session so switching back resumes intact. Real distillation
    happens on an explicit /new /summary or on the next app launch (recover_pending)."""
    try:
        await cl.make_async(_REGISTRY.park_all)()
    except Exception:  # noqa: BLE001 — teardown must never raise
        pass


# ----------------------------------------------------------------------
# The streaming bridge (worker thread -> main-loop queue drain), shared by a meeting
# turn and a gate approval. `make_awaitable(on_text, on_status)` must return an
# awaitable that runs the sync work in a worker thread (cl.make_async). Returns
# (error_or_none, work_result). Status lines render as distinct blockquote lines so
# the deterministic 🔎/✅ channel stays visually separate from Atlas's voice.
# ----------------------------------------------------------------------
async def _stream_into(msg: cl.Message, make_awaitable) -> tuple:
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def on_text(t: str) -> None:
        loop.call_soon_threadsafe(q.put_nowait, ("text", t))

    def on_status(m: str) -> None:
        loop.call_soon_threadsafe(q.put_nowait, ("status", m))

    value = {"result": None}

    async def run() -> None:
        try:
            value["result"] = await make_awaitable(on_text, on_status)
        except Exception as exc:  # noqa: BLE001 — containment, mirror the REPL
            loop.call_soon_threadsafe(q.put_nowait, ("error", str(exc)))
        finally:
            loop.call_soon_threadsafe(q.put_nowait, ("done", None))

    worker = asyncio.create_task(run())
    error = None
    while True:
        kind, payload = await q.get()
        if kind == "done":
            break
        if kind == "error":
            error = payload
        elif kind == "text":
            await msg.stream_token(payload)
        elif kind == "status":
            await msg.stream_token(f"\n\n> {payload}\n\n")
    await worker
    return error, value["result"]


# ----------------------------------------------------------------------
# A meeting turn
# ----------------------------------------------------------------------
@cl.on_message
async def on_message(message: cl.Message):
    sess = cl.user_session.get("sess")
    if sess is None:  # defensive: a message before on_chat_start finished
        await cl.Message("(One moment — still opening the room. Try again.)",
                         author="Atlas").send()
        return

    is_atlas = isinstance(sess, _session.AtlasSession)
    display = "Atlas" if is_atlas else sess.entry.display
    text = (message.content or "").strip()
    # Marlow's job-gate button: /write <project> runs the gated script write, with the
    # [y/N] gate surfaced as an Approve/Deny button (the injected approver).
    if (not is_atlas and sess.entry.name == "scriptwriter"
            and text.lower().split(maxsplit=1)[0:1] == ["/write"]):
        await _marlow_write(sess, text[len("/write"):])
        return
    if text.startswith("/") and await _handle_command(sess, display, text):
        return

    msg = cl.Message(content="", author=display)
    await msg.send()
    if is_atlas:
        error, _ = await _stream_into(
            msg, lambda on_text, on_status: cl.make_async(sess.send)(
                text, on_text=on_text, on_status=on_status))
    else:
        # Persona chat: adapter.ask is single-turn (not streamed); the reply arrives via
        # on_text once. Same worker-thread bridge keeps the loop responsive.
        error, _ = await _stream_into(
            msg, lambda on_text, on_status: cl.make_async(sess.send)(text, on_text))
    if error:
        await msg.stream_token(
            f"\n\n_({display} hit a problem: {error}. Try again, or `/new` if it "
            "persists.)_")
    await msg.update()
