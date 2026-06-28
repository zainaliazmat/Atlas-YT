"""The meeting room — talk to Atlas, who runs the whole team.

Launch:  python run.py chat

This is the PRIMARY *terminal* interface. It is now a THIN FRONTEND over the shared
session core (session.py): all the orchestration/memory logic lives there so the web
operator UI can drive the exact same primitives. This module owns only the terminal
I/O — the banner, input()/print(), slash commands, and the SIGINT/EOF handling.

Memory model (summary-only — unchanged): across sessions Atlas's only long-term
memory is ONE distilled summary in chat_state.json; the raw transcript lives in RAM
and is distilled on every boundary (/exit, Ctrl+C, /new, /summary). A failed distill
parks the raw turns under "pending" (no data loss), folded in on the next launch.

Routing:
- A normal message goes to the ORCHESTRATOR (via session.send), which autonomously
  decides whether to delegate (Scout/Sage), route a direct address, or answer itself.
- `/ask <agent> <question>` is DETERMINISTIC direct routing: it bypasses the
  orchestrator LLM and asks that agent's persona straight away.
"""
from __future__ import annotations

import os
import pathlib
import signal
import sys

import chat_state
import llm
import registry
import session as _session
from ceo import cycle as ceo_cycle
from session import AtlasSession, make_distiller  # noqa: F401  (make_distiller re-exported for the test surface)

HERE = pathlib.Path(__file__).parent
STATE_PATH = HERE / "chat_state.json"

DISTILL_TIMEOUT_SEC = _session.DISTILL_TIMEOUT_SEC


# ----------------------------------------------------------------------
# Distillation — terminal/test-facing wrapper over the session core.
# Kept so the existing test surface (chat.distill_and_save / chat.make_distiller /
# chat.STATE_PATH) is unchanged. `status` is printed (only on a non-empty distill);
# the path is the module-level STATE_PATH so tests can monkeypatch it.
# ----------------------------------------------------------------------
def distill_and_save(state, distiller, *, status: str | None = None,
                     timeout: float = DISTILL_TIMEOUT_SEC) -> bool:
    status_cb = (lambda: print(status)) if status else None
    return _session.distill_and_save(state, distiller, STATE_PATH,
                                     status_cb=status_cb, timeout=timeout)


# ----------------------------------------------------------------------
# Direct routing — /ask forces a question straight to one agent's persona.
# Terminal frontend (prints); operates on the live orchestrator + state so it shares
# the same adapters and transcript the session uses.
# ----------------------------------------------------------------------
def ask_agent(orch, state, agent_name: str, question: str) -> None:
    entry = registry.get_entry(agent_name)
    if entry is None:
        names = ", ".join(e.name for e in registry.REGISTRY)
        print(f"I don't have an agent named {agent_name!r}. On the team: {names}.")
        return
    adapter = orch.adapters[entry.name]
    print(f"\n{entry.emoji} Asking {entry.display} directly…")
    try:
        reply = adapter.ask(question, context=state["summary"])
    except Exception as exc:  # noqa: BLE001
        print(f"(Couldn't reach {entry.display}: {exc})")
        return
    print(f"\n{entry.display}: {reply}")
    chat_state.append_turn(state, "user", f"[CEO asked {entry.display}] {question}")
    chat_state.append_turn(state, "atlas", f"[{entry.display} replied] {reply}")


# ----------------------------------------------------------------------
# Terminal callbacks — these reproduce the previous default sinks EXACTLY so the
# on-screen output is byte-for-byte unchanged from before the extraction.
# ----------------------------------------------------------------------
def _on_text(t: str) -> None:
    print(t, end="", flush=True)


def _on_status(m: str) -> None:
    print("\n" + m, flush=True)


def advance_business(sess: AtlasSession) -> None:
    """CEO mode: run one business work cycle and print its digest + ask."""
    print("\nAtlas (CEO): running a business cycle…")
    try:
        result = sess.advance_business()
    except Exception as exc:  # the meeting never crashes
        print(f"(The CEO cycle hit a problem: {exc}\n Try again, or /new if it persists.)")
        return
    print(result.get("digest", "(no digest)"))
    if result.get("ask"):
        print("   (Filed to ceo/requests.jsonl — your move when you can.)")


def handle_message(sess: AtlasSession, user_msg: str) -> None:
    """One CEO message -> Atlas runs the room (streamed). Terminal-side of session.send."""
    # "advance the business" routes into the deterministic CEO cycle, not a chat turn.
    if ceo_cycle.is_advance_command(user_msg):
        advance_business(sess)
        return
    print("\nAtlas: ", end="", flush=True)
    try:
        sess.send(user_msg, on_text=_on_text, on_status=_on_status)
    except Exception as exc:  # the meeting never crashes
        print(f"\n(Atlas hit a problem: {exc}\n Try again, or /new if it persists.)")
        return
    print()  # newline after the streamed reply


# ----------------------------------------------------------------------
# Slash commands
# ----------------------------------------------------------------------
HELP = """Commands:
  /agents              who's on the team and what each does
  /advance             run one CEO business cycle ("advance the business")
  /ask <agent> <q>     ask one agent directly (e.g. /ask scout is faceless dead?)
  /summary             distill the meeting so far, then show what Atlas remembers
  /new                 distill + start a fresh thread (keeps what Atlas knows)
  /help                show this
  /exit  (/quit)       save (distill) and leave
Anything else is a message to Atlas — it'll delegate, route, or answer."""


def handle_command(sess: AtlasSession, raw: str) -> bool:
    """Return True to keep looping, False to exit."""
    parts = raw.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit"):
        sess.close(status_cb=lambda: print("💾 Saving the meeting summary…"))
        print("Saved. Talk soon.")
        return False
    if cmd == "/help":
        print(HELP)
    elif cmd == "/agents":
        ready = sum(1 for e in registry.REGISTRY if not e.stub)
        stub = sum(1 for e in registry.REGISTRY if e.stub)
        print(f"The team — {len(registry.REGISTRY)} roles "
              f"({ready} ready, {stub} stub slots):\n")
        print(registry.roster())
        print("\nAtlas runs the full video playbook by delegating to these tools in "
              "order (start_project → research → script → fact-check → … → render).")
        print(f"\nAtlas's own brain: {llm.effective_provider()}  "
              "(jobs run on each agent's own engine/provider).")
    elif cmd == "/advance":
        advance_business(sess)
    elif cmd == "/ask":
        bits = arg.split(maxsplit=1)
        if len(bits) < 2:
            print("Usage: /ask <agent> <question>   e.g. /ask sage is ozempic safe?")
        else:
            ask_agent(sess.orch, sess.state, bits[0], bits[1])
    elif cmd == "/summary":
        ok, body = sess.summarize(
            status_cb=lambda: print("💾 Updating what I remember…"))
        print("\n[What Atlas remembers]\n" + (body or "(nothing worth remembering yet)"))
        if not ok:
            print("(Couldn't fully update just now — kept what I had; your meeting is "
                  "safe and I'll fold it in next launch.)")
    elif cmd == "/new":
        sess.new_thread(
            status_cb=lambda: print("💾 Saving what matters before clearing the thread…"))
        print("Fresh thread. I've folded this meeting into what I remember — the "
              "decisions and your preferences stay; the back-and-forth is cleared.")
    else:
        print(f"Unknown command {cmd!r}. /help for the list.")
    return True


# ----------------------------------------------------------------------
# Graceful Ctrl+C (SIGINT)
# ----------------------------------------------------------------------
_SESSION: dict = {"sess": None, "interrupting": False}


def _sigint_handler(signum, frame):
    ctx = _SESSION
    sess: AtlasSession = ctx["sess"]
    if ctx.get("interrupting"):
        try:
            sess.park_pending()
        finally:
            os._exit(130)
    ctx["interrupting"] = True
    sess.close(status_cb=lambda: print(
        "\n💾 Saving the meeting summary…  (Ctrl+C again to skip)"))
    print("Saved. Talk soon.")
    sys.exit(0)


# ----------------------------------------------------------------------
# Recovery notices (printed by the session core via a callback, terminal-side text)
# ----------------------------------------------------------------------
def _recover_note(event: str) -> None:
    if event == "recovering":
        print("💾 Recovering an unsaved meeting from last time…")
    elif event == "recover-failed":
        print("   (couldn't fold it in just now — I'll retry next launch.)")


# ----------------------------------------------------------------------
# REPL
# ----------------------------------------------------------------------
def start():
    import tools
    tools.configure_logging()  # surface the start_project arg-logs to atlas/atlas.log

    # Construct the session (no recovery yet), wire the SIGINT handler, THEN recover —
    # preserving the original ordering (signal handler armed before the recovery distill).
    sess = AtlasSession(state=chat_state.load_state(STATE_PATH),
                        distiller=make_distiller(), state_path=STATE_PATH)
    _SESSION.update(sess=sess, interrupting=False)
    signal.signal(signal.SIGINT, _sigint_handler)
    _session.recover_pending(sess.state, sess.distiller, STATE_PATH,
                             note_cb=_recover_note)

    present = ", ".join(f"{e.emoji} {e.display}" for e in registry.REGISTRY)
    print("=" * 64)
    print("Atlas — the meeting room.   /help for commands, /exit to leave.")
    print(f"In the room: {present}")
    if sess.summary.strip():
        print("(Atlas remembers what matters from before — pick up wherever you like.)")
    print("=" * 64)

    while True:
        try:
            user = input("\nYou: ").strip()
        except EOFError:  # Ctrl+D — save and leave gracefully
            print()
            sess.close(status_cb=lambda: print("💾 Saving the meeting summary…"))
            print("Saved. Talk soon.")
            break
        if not user:
            continue
        if user.startswith("/"):
            if not handle_command(sess, user):
                break
            continue
        handle_message(sess, user)
