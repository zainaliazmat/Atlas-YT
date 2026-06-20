"""Atlas — the web operator UI (Phase A streaming + Phase B gates).

A SECOND frontend over the exact same session core (session.AtlasSession) the
terminal REPL drives — no second service, no duplicated orchestration. This file
owns only browser I/O; all orchestration/memory logic lives in session.py, and the
contracts / pipeline / gates / agent engines are untouched.

Phase B adds the two human gates as inline artifact preview + Approve/Revise buttons.
Approve is a DIRECT, deterministic pipeline.produce(slug, approve=[gate]) call (the
gate code runs unchanged); its result is recorded into Atlas's transcript so the next
turn narrates coherently. Revise is just a conversational turn back to Atlas.

Run it (from the atlas/ directory, with the shared venv active):

    chainlit run web/app.py -w        # -> http://localhost:8000

Phase A scope: stream a meeting turn live — Atlas's words (including the
"🧠 I'm going with …" decision lines, which are part of Atlas's streamed text) and
the deterministic 🔎/✅ status lines as they happen. The same summary-only memory
lifecycle as the REPL: /new, /summary, /help, /agents work here too, and closing the
tab distills (or parks) the meeting so nothing is lost.

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

import project_view  # noqa: E402
import registry  # noqa: E402
import session as _session  # noqa: E402
import tools  # noqa: E402
from llm import effective_provider  # noqa: E402

tools.configure_logging()  # produce_video arg-logs -> atlas/atlas.log (never stdout)

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
    cl.user_session.set("gate_shown", None)  # fresh connection -> re-surface a gate

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
        # Surface any project paused at a gate (disk-backed -> survives switches).
        await _maybe_show_gate(sess)
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

    # Only Atlas drives the pipeline -> only Atlas can leave a project at a gate.
    if is_atlas:
        await _maybe_show_gate(sess)


# ----------------------------------------------------------------------
# Gates — inline artifact preview + Approve / Revise (Phase B)
# ----------------------------------------------------------------------
async def _maybe_show_gate(sess) -> None:
    try:
        blk = sess.latest_blocked_project()
    except Exception:  # noqa: BLE001 — gate detection must never break the chat
        return
    if not blk:
        cl.user_session.set("gate_shown", None)
        return
    key = (blk["slug"], blk["gate"], blk.get("updated"))
    if cl.user_session.get("gate_shown") == key:
        return  # already presented this exact blocked state
    cl.user_session.set("gate_shown", key)
    try:
        if blk["gate"] == "factcheck":
            await _show_factcheck_gate(blk)
        elif blk["gate"] == "final_render":
            await _show_render_gate(blk)
    except Exception as exc:  # noqa: BLE001 — preview failure is non-fatal
        await cl.Message(f"_(Couldn't render the {blk['gate']} gate preview: {exc}. "
                         f"The project `{blk['slug']}` is paused at that gate.)_",
                         author="Gate").send()


def _gate_actions(slug: str, gate: str, *, include_approve: bool = True) -> list:
    actions = []
    if include_approve:
        actions.append(cl.Action(name="approve_gate", payload={"slug": slug, "gate": gate},
                                 label="✅ Approve",
                                 tooltip="Sign off and resume the pipeline"))
    actions.append(cl.Action(name="revise_gate", payload={"slug": slug, "gate": gate},
                             label="✍️ Revise", tooltip="Send it back to Atlas to revise"))
    return actions


async def _show_factcheck_gate(blk) -> None:
    pv = await cl.make_async(project_view.gate1_preview)(blk["project_dir"])
    v = pv.get("summary", {}) or {}
    is_block = pv.get("verdict") == "block"
    lines = [f"### ⚖️ Fact-check gate — *{blk['label']}*",
             f"**Verdict: `{pv.get('verdict')}`**  ·  "
             f"verified {v.get('verified', 0)}, flagged {v.get('flagged', 0)}, "
             f"unverifiable {v.get('unverifiable', 0)}"]
    if is_block:
        # A `block` can't be approved away (the pipeline re-earns it), so we DON'T offer
        # Approve here — it would only re-block. Lead with Revise. (UI-only; the gate
        # logic in pipeline.py is untouched and still enforces this server-side.)
        lines.append("> ⛔ This is a **BLOCK** — it can't be approved away. The flagged "
                     "claims must be revised first, so only **Revise** is offered.")
    flagged = pv.get("flagged") or []
    if flagged:
        lines.append("\n**Flagged / unverifiable claims**")
        for c in flagged:
            note = f" — _{c['note']}_" if c.get("note") else ""
            lines.append(f"- `{c.get('claim_id')}` (scene {c.get('scene_no')}, "
                         f"{c.get('status')}): \"{c.get('claim_text', '')}\"{note}")
    else:
        lines.append("\nNo flagged or unverifiable claims — the report is clean.")
    sc = pv.get("script", {})
    lines.append(f"\n**Script:** *{sc.get('working_title', '')}* · "
                 f"{sc.get('total_scenes', 0)} scenes · ~{sc.get('est_runtime_sec', 0)}s")
    await cl.Message("\n".join(lines), author="Gate · Fact-check",
                     actions=_gate_actions(blk["slug"], "factcheck",
                                           include_approve=not is_block)).send()


async def _show_render_gate(blk) -> None:
    pv = await cl.make_async(project_view.gate2_preview)(blk["project_dir"])
    plan = pv.get("plan", {})
    lines = [f"### 🎬 Final-render gate — *{plan.get('working_title') or blk['label']}*",
             f"{plan.get('scenes', 0)} scenes · ~{plan.get('est_runtime_sec', 0)}s · "
             f"audio {plan.get('audio_duration_sec', 0)}s",
             f"_{plan.get('plan', '')}_"]
    drafts = pv.get("draft_renders") or []
    lines.append(f"\nReview the **{len(drafts)} draft scene render(s)** below before "
                 "spending on the final render.")
    elements = [cl.Video(name=p.parent.parent.name, path=str(p), display="inline")
                for p in drafts]
    await cl.Message("\n".join(lines), author="Gate · Final render", elements=elements,
                     actions=_gate_actions(blk["slug"], "final_render")).send()


@cl.action_callback("approve_gate")
async def on_approve(action: cl.Action):
    sess = cl.user_session.get("sess")
    if not isinstance(sess, _session.AtlasSession):
        return  # gates belong to Atlas; ignore a stale action on another profile
    slug = action.payload.get("slug")
    gate = action.payload.get("gate")
    try:  # prevent a double-click re-approving the same gate
        await action.remove()
    except Exception:  # noqa: BLE001
        pass

    msg = cl.Message(content=f"**Signing off the {gate} gate — resuming the pipeline…**",
                     author="Pipeline")
    await msg.send()
    error, result = await _stream_into(
        msg,
        # Approve is a DIRECT, deterministic pipeline call (NOT routed through Atlas);
        # the gate code runs unchanged. Sync -> worker thread; status streams live.
        lambda on_text, on_status: cl.make_async(sess.approve_gate)(
            slug, gate, on_status=on_status))
    if error:
        await msg.stream_token(
            f"\n\n_(The pipeline hit a problem resuming: {error}. Nothing was advanced; "
            "try again or ask Atlas.)_")
        await msg.update()
        return

    await msg.stream_token("\n\n" + _approve_summary(gate, result or {}))
    await msg.update()
    # The new state was recorded into Atlas's transcript by approve_gate, so Atlas's
    # next turn narrates coherently. Surface the NEXT gate (or completion) right away.
    await _maybe_show_gate(sess)


def _approve_summary(gate: str, result: dict) -> str:
    status = result.get("status")
    if status == "done":
        return f"🎬 **Done** — the pipeline finished. Video at `{result.get('video')}`."
    if status == "blocked":
        nxt = result.get("gate")
        if nxt == gate:
            return (f"⛔ The **{gate}** gate still blocks — {result.get('reason', '')} "
                    "Use **Revise** to fix it.")
        return (f"✅ **{gate}** signed off — the pipeline advanced and is now paused at "
                f"the **{nxt}** gate (see below).")
    if status == "failed":
        return f"❌ The pipeline could not advance: {result.get('errors')}."
    return f"Pipeline state: {status}."


@cl.action_callback("revise_gate")
async def on_revise(action: cl.Action):
    # Revise is OPEN-ENDED agent work, NOT a deterministic transition — so it's just a
    # normal conversational turn back to Atlas. We prompt; the CEO's next message flows
    # to Atlas (which can re-run the scriptwriter, re-research, etc.).
    gate = action.payload.get("gate")
    try:
        await action.remove()
    except Exception:  # noqa: BLE001
        pass
    where = "fact-check" if gate == "factcheck" else "render"
    await cl.Message(
        f"Tell me what to change about the {where}, and I'll have the team revise it — "
        "for example *“soften the claim in scene 2”* or *“re-research the pricing.”* "
        "Just type it as a normal message.",
        author="Atlas").send()
