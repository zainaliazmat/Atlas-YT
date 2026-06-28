"""The UI-neutral session core — the one driver both frontends share.

The terminal REPL (chat.py) and the web operator UI are two thin frontends over
THIS module. Everything here is I/O-agnostic: it takes callbacks (on_text /
on_status) and returns values; it never reads stdin or writes stdout. The terminal
frontend supplies print-based callbacks; the web frontend supplies browser-push
callbacks. Nothing about orchestration, the pipeline, the contracts, the gates, or
any agent's engine/persona/memory lives here — this is purely the seam that was
previously entangled with the terminal in chat.py.

Memory model is unchanged (summary-only): the durable summary lives in
chat_state.json; the raw transcript lives only in RAM and is distilled on every
session boundary (close / new_thread / summarize). A failed distill parks the raw
turns under "pending" with NO data loss, folded in on the next start().
"""
from __future__ import annotations

import pathlib
import threading
from typing import Any, Callable

import chat_state
import llm
import registry
from orchestrator import Orchestrator
from progress import Progress

HERE = pathlib.Path(__file__).parent
STATE_PATH = HERE / "chat_state.json"
PROJECTS_DIR = HERE / "projects"

# How many recent turns to feed the orchestrator each turn (the rest lives in the
# durable summary). Keeps the prompt bounded without an in-session compactor.
RECENT_WINDOW = 16
DISTILL_TIMEOUT_SEC = 25
MAX_SNAPSHOT = 5   # recent fleet items to surface as Atlas's memory snapshot


# ----------------------------------------------------------------------
# Distillation — the ONE memory helper every session boundary funnels through.
# (Moved verbatim from chat.py so the terminal and the web UI share it exactly.)
# ----------------------------------------------------------------------
DISTILL_SYSTEM = (
    "You maintain the long-term memory of Atlas, a calm chief-of-staff who runs a "
    "team of YouTube agents for ONE CEO. That memory is a single distilled summary "
    "Atlas reloads at the start of every meeting — so it must hold only what makes "
    "Atlas's coordination smarter, in as few words as possible."
)


def _render_turns(transcript: list[dict[str, str]]) -> str:
    label = {"user": "CEO", "atlas": "Atlas"}
    return "\n".join(f"{label.get(t['role'], t['role'])}: {t['content']}"
                     for t in transcript)


def _distill_prompt(existing_summary: str, transcript: list[dict[str, str]]) -> str:
    convo = _render_turns(transcript)
    return (
        "Here is the memory you already hold about the CEO and the studio:\n"
        f"{existing_summary.strip() or '(nothing yet)'}\n\n"
        "Here is the full transcript of the meeting that just happened:\n"
        f"{convo}\n\n"
        "Rewrite the memory as a single clean, consolidated summary.\n\n"
        "KEEP only durable, coordination-improving signal:\n"
        "- the niches / channels / topics the CEO works on\n"
        "- decisions made (which topics were chosen and why, which were rejected)\n"
        "- the CEO's standards and preferences (tone, what they want from the team)\n"
        "- useful findings and conclusions reached, and open threads to revisit\n"
        "- anything about how the CEO likes the team run\n\n"
        "DROP the junk: greetings and small talk, off-topic asides, identity-test "
        "exchanges, and anything transient.\n\n"
        "MERGE with the memory you already hold — knowledge accumulates across "
        "meetings. Resolve contradictions in favor of the MOST RECENT information.\n\n"
        "Keep it BOUNDED and consolidated: a few tight bullet groups, well under 600 "
        "words. Output ONLY the updated summary — no preamble. If the meeting "
        "contained nothing worth keeping, return the existing memory unchanged."
    )


def make_distiller(chat_fn=llm.chat):
    """Build distill(existing_summary, transcript) -> new_summary from a chat seam.

    Injectable so tests can pass a fake chat function (no API). An empty transcript
    is a no-op returning the existing summary verbatim.
    """
    def distill(existing_summary: str, transcript: list[dict[str, str]]) -> str:
        existing = (existing_summary or "").strip()
        if not transcript:
            return existing
        new = chat_fn(DISTILL_SYSTEM, _distill_prompt(existing, transcript)).strip()
        return new or existing
    return distill


def _distill_with_timeout(distiller, summary, transcript, timeout):
    """Run the distiller in a daemon thread with a hard timeout (can't block exit)."""
    box: dict = {}

    def work():
        try:
            box["value"] = distiller(summary, transcript)
        except BaseException as exc:  # noqa: BLE001 — surfaced to the caller
            box["error"] = exc

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError("distill timed out")
    if "error" in box:
        raise box["error"]
    return box["value"]


def distill_and_save(state: dict, distiller, state_path: str | pathlib.Path, *,
                     status_cb: Callable[[], None] | None = None,
                     timeout: float = DISTILL_TIMEOUT_SEC) -> bool:
    """Distill the meeting into the summary and persist ONLY the summary.

    Backlog = any stranded "pending" turns + the live transcript. On success: summary
    updated, transcript + pending cleared, summary persisted; returns True. On
    failure/timeout (NO DATA LOSS): the backlog is parked under "pending" and the
    existing summary kept; the in-RAM transcript is left intact; returns False.

    `status_cb` (optional) is invoked once, right before a non-empty distill runs, so
    a frontend can show "saving…". Path is explicit so both frontends share one core.
    """
    backlog = (state.get("pending") or []) + state["transcript"]
    if not backlog:
        state["pending"] = None
        chat_state.save_summary(state_path, state["summary"])
        return True

    if status_cb:
        status_cb()
    try:
        new_summary = _distill_with_timeout(distiller, state["summary"], backlog,
                                            timeout)
    except BaseException:  # noqa: BLE001 — any failure must not lose the meeting
        state["pending"] = backlog
        chat_state.save_summary(state_path, state["summary"], pending=backlog)
        return False

    state["summary"] = new_summary
    state["pending"] = None
    state["transcript"] = []
    chat_state.save_summary(state_path, new_summary)
    return True


def recover_pending(state: dict, distiller, state_path: str | pathlib.Path, *,
                    note_cb: Callable[[str], None] | None = None) -> None:
    """On launch, fold any 'pending' raw transcript (a failed prior distill) in."""
    pending = state.get("pending")
    if not pending:
        return
    if note_cb:
        note_cb("recovering")
    try:
        state["summary"] = _distill_with_timeout(distiller, state["summary"],
                                                 pending, DISTILL_TIMEOUT_SEC)
    except BaseException:  # noqa: BLE001 — keep pending for next time, don't crash
        if note_cb:
            note_cb("recover-failed")
        return
    state["pending"] = None
    chat_state.save_summary(state_path, state["summary"])


# ----------------------------------------------------------------------
# Cross-fleet memory snapshot + bounded context assembly (moved from chat.py).
# ----------------------------------------------------------------------
def memory_snapshot(adapters: dict) -> str:
    """Cross-fleet awareness: recent topics each managed agent has worked on.

    Read-only, best-effort, bounded. A teammate whose memory can't be read is simply
    skipped (never fatal).
    """
    lines = []
    for name, adapter in adapters.items():
        try:
            mem = adapter.engine().load_memory()
        except Exception:  # noqa: BLE001 — memory is optional context
            continue
        runs = list(mem.get("runs", []))[-MAX_SNAPSHOT:]
        if not runs:
            continue
        labels = [r.get("topic") or r.get("niche") or "?" for r in runs]
        lines.append(f"{adapter.entry.display} recently: " + "; ".join(labels))
    if not lines:
        return ""
    return "[Fleet memory — recent work]\n" + "\n".join(lines)


def build_context(state: dict, snapshot: str,
                  recent_window: int = RECENT_WINDOW) -> str:
    """Bounded context for the orchestrator: summary + snapshot + recent window."""
    parts = []
    if state["summary"].strip():
        parts.append("[What you remember about the CEO / studio]\n"
                     + state["summary"].strip())
    if snapshot.strip():
        parts.append(snapshot.strip())
    recent = state["transcript"][-recent_window:]
    if recent:
        parts.append("[The meeting so far]\n" + _render_turns(recent))
    return "\n\n".join(parts)


# ----------------------------------------------------------------------
# Per-agent persona chat (Phase C) — a direct conversation with ONE managed agent
# via the shared `adapter.ask` seam. Its own summary-only memory, web-local, distinct
# from the agent's sibling terminal state (zero sibling changes).
# ----------------------------------------------------------------------
def _render_agent_turns(transcript: list[dict[str, str]], display: str) -> str:
    label = {"user": "You", "agent": display}
    return "\n".join(f"{label.get(t['role'], t['role'])}: {t['content']}"
                     for t in transcript)


def agent_context(state: dict, display: str,
                  recent_window: int = RECENT_WINDOW) -> str:
    """Bounded context for a persona reply: what the agent remembers + recent turns."""
    parts = []
    if state["summary"].strip():
        parts.append(f"[What you ({display}) remember about the CEO / your work]\n"
                     + state["summary"].strip())
    recent = state["transcript"][-recent_window:]
    if recent:
        parts.append("[The conversation so far]\n"
                     + _render_agent_turns(recent, display))
    return "\n\n".join(parts)


def make_agent_distiller(display: str, chat_fn=llm.chat):
    """A summary-only distiller for ONE agent's persona chat. Generic (not Atlas's
    CEO-memory prompt, not the sibling's engine prompt) — just durable signal from
    this conversation. Injectable chat_fn for tests."""
    system = (f"You maintain the long-term memory of {display}, a member of a YouTube "
              "studio's team, across casual chats with the CEO. Keep only what makes "
              f"{display} more useful next time, in as few words as possible.")

    def distill(existing_summary: str, transcript: list[dict[str, str]]) -> str:
        existing = (existing_summary or "").strip()
        if not transcript:
            return existing
        convo = _render_agent_turns(transcript, display)
        prompt = (
            f"Here is the memory you ({display}) already hold:\n"
            f"{existing or '(nothing yet)'}\n\n"
            f"Here is the conversation that just happened:\n{convo}\n\n"
            "Rewrite the memory as one short, consolidated summary: keep durable "
            "preferences, decisions, and useful threads; drop greetings and small "
            "talk. Merge with what you already hold, newest wins. Output ONLY the "
            "updated memory, well under 300 words. If nothing's worth keeping, return "
            "the existing memory unchanged.")
        return chat_fn(system, prompt).strip() or existing
    return distill


# ----------------------------------------------------------------------
# The session — owns state + orchestrator + distiller, exposes a UI-neutral API.
# ----------------------------------------------------------------------
class AtlasSession:
    """One operator session. Both the terminal REPL and the web UI drive this."""

    def __init__(self, *, state: dict, distiller, state_path: str | pathlib.Path,
                 build_orch: Callable[[Progress], Any] | None = None,
                 projects_dir: str | pathlib.Path | None = None):
        self.state = state
        self.distiller = distiller
        self.state_path = pathlib.Path(state_path)
        self.projects_dir = pathlib.Path(projects_dir) if projects_dir else PROJECTS_DIR
        self._status_cb: Callable[[str], None] | None = None
        # The orchestrator's deterministic status lines (🔎/✅) are emitted on this
        # one Progress; its sink dispatches to whatever on_status the current turn
        # supplied. This is how the SAME orchestrator routes status to the terminal
        # on one turn and the browser on another — with orchestrator.py untouched.
        self.progress = Progress(sink=self._dispatch_status)
        factory = build_orch or (lambda progress: Orchestrator(progress=progress))
        self.orch = factory(self.progress)

    # ---- construction ----
    @classmethod
    def start(cls, *, state_path: str | pathlib.Path = STATE_PATH, distiller=None,
              build_orch=None, projects_dir=None,
              note_cb: Callable[[str], None] | None = None) -> "AtlasSession":
        """Load durable state (summary + fresh transcript), fold in any pending."""
        state = chat_state.load_state(state_path)
        distiller = distiller or make_distiller()
        self = cls(state=state, distiller=distiller, state_path=state_path,
                   build_orch=build_orch, projects_dir=projects_dir)
        recover_pending(state, distiller, state_path, note_cb=note_cb)
        return self

    # ---- status routing ----
    def _dispatch_status(self, msg: str) -> None:
        cb = self._status_cb
        if cb is not None:
            cb(msg)

    # ---- a meeting turn ----
    def send(self, user_msg: str, *, on_text: Callable[[str], None] | None = None,
             on_status: Callable[[str], None] | None = None) -> str:
        """One CEO message -> Atlas runs the room. Streams text via on_text and the
        deterministic 🔎/✅ status lines via on_status; records both turns; returns
        Atlas's final text. Kept in the in-RAM transcript only (distilled on a boundary).
        """
        snapshot = memory_snapshot(self.orch.adapters)
        context = build_context(self.state, snapshot)
        self._status_cb = on_status
        try:
            reply = self.orch.ask(user_msg, context=context, on_text=on_text)
        finally:
            self._status_cb = None
        chat_state.append_turn(self.state, "user", user_msg)
        chat_state.append_turn(self.state, "atlas", reply or "")
        return reply

    # ---- CEO mode (deterministic spine; delegates heavy work to the orchestrator) ----
    def advance_business(self) -> dict:
        """Run ONE CEO work cycle and record its digest as a turn. Returns the cycle
        result {digest, ask, kind, ...} for the frontend to render."""
        result = self.orch.advance_business()
        chat_state.append_turn(self.state, "user", "advance the business")
        chat_state.append_turn(self.state, "atlas", result.get("digest", ""))
        return result

    # ---- direct address (deterministic; bypasses the orchestrator LLM) ----
    def ask_agent(self, agent_name: str, question: str):
        """Ask one agent's persona directly. Returns (entry, reply), or (None, None)
        if the name doesn't resolve (nothing recorded in that case)."""
        entry = registry.get_entry(agent_name)
        if entry is None:
            return None, None
        adapter = self.orch.adapters[entry.name]
        reply = adapter.ask(question, context=self.state["summary"])
        chat_state.append_turn(self.state, "user",
                               f"[CEO asked {entry.display}] {question}")
        chat_state.append_turn(self.state, "atlas",
                               f"[{entry.display} replied] {reply}")
        return entry, reply

    # ---- memory boundaries (the same summary-only distill the REPL uses) ----
    def _distill(self, *, status_cb=None) -> bool:
        return distill_and_save(self.state, self.distiller, self.state_path,
                                status_cb=status_cb)

    def close(self, *, status_cb=None) -> bool:
        """End the session: distill the meeting into the summary and persist it.
        (= the REPL's /exit, Ctrl+D, and the graceful SIGINT save.)"""
        return self._distill(status_cb=status_cb)

    def new_thread(self, *, status_cb=None) -> bool:
        """Distill, then clear the thread regardless (= the REPL's /new). On a failed
        distill the backlog is parked under 'pending' (no data loss) AND the thread is
        still cleared — exactly the terminal behavior."""
        ok = self._distill(status_cb=status_cb)
        self.state["transcript"] = []
        return ok

    def summarize(self, *, status_cb=None) -> tuple[bool, str]:
        """Distill now and return (ok, the-current-summary-body) (= the REPL's
        /summary). Empty transcript is a no-op returning the existing summary."""
        ok = self._distill(status_cb=status_cb)
        return ok, self.state["summary"].strip()

    def park_pending(self) -> None:
        """Abrupt end (browser tab closed, hard SIGINT): persist the un-distilled
        backlog under 'pending' so the next start() folds it in. NO distill, NO data
        loss. Mirrors the REPL's SIGINT-twice flush (minus the process exit)."""
        backlog = (self.state.get("pending") or []) + self.state["transcript"]
        if backlog:
            chat_state.save_summary(self.state_path, self.state["summary"],
                                    pending=backlog)

    # ---- read-only views ----
    @property
    def summary(self) -> str:
        return self.state["summary"]

    def snapshot(self) -> str:
        """The cross-fleet 'recent work' snapshot (read-only, bounded)."""
        return memory_snapshot(self.orch.adapters)


class AgentSession:
    """A persona chat with ONE managed agent, via the shared `adapter.ask` seam.

    Same summary-only memory contract as AtlasSession (distill on a boundary; park on
    abrupt end), but its state file is web-local (passed in by the caller) and SEPARATE
    from the agent's own sibling terminal state — so this touches no sibling. The
    persona reply is single-turn (adapter.ask returns the whole string), so send()
    isn't token-streamed; on_text, if given, receives the full reply once.
    """

    def __init__(self, *, entry, adapter, state: dict, distiller,
                 state_path: str | pathlib.Path):
        self.entry = entry
        self.adapter = adapter
        self.state = state
        self.distiller = distiller
        self.state_path = pathlib.Path(state_path)

    @classmethod
    def start(cls, entry, adapter, *, state_path, distiller=None,
              note_cb: Callable[[str], None] | None = None) -> "AgentSession":
        state = chat_state.load_state(state_path)
        distiller = distiller or make_agent_distiller(entry.display)
        self = cls(entry=entry, adapter=adapter, state=state, distiller=distiller,
                   state_path=state_path)
        recover_pending(state, distiller, state_path, note_cb=note_cb)
        return self

    def send(self, user_msg: str,
             on_text: Callable[[str], None] | None = None) -> str:
        """One turn with this agent's persona. Returns the reply (also handed to
        on_text once, since the persona seam is single-turn, not streaming)."""
        ctx = agent_context(self.state, self.entry.display)
        reply = self.adapter.ask(user_msg, context=ctx)
        if on_text is not None:
            on_text(reply or "")
        chat_state.append_turn(self.state, "user", user_msg)
        chat_state.append_turn(self.state, "agent", reply or "")
        return reply

    def _distill(self, *, status_cb=None) -> bool:
        return distill_and_save(self.state, self.distiller, self.state_path,
                                status_cb=status_cb)

    def close(self, *, status_cb=None) -> bool:
        return self._distill(status_cb=status_cb)

    def new_thread(self, *, status_cb=None) -> bool:
        ok = self._distill(status_cb=status_cb)
        self.state["transcript"] = []
        return ok

    def summarize(self, *, status_cb=None) -> tuple[bool, str]:
        ok = self._distill(status_cb=status_cb)
        return ok, self.state["summary"].strip()

    def park_pending(self) -> None:
        backlog = (self.state.get("pending") or []) + self.state["transcript"]
        if backlog:
            chat_state.save_summary(self.state_path, self.state["summary"],
                                    pending=backlog)

    @property
    def summary(self) -> str:
        return self.state["summary"]


class SessionRegistry:
    """Process-level cache of per-profile sessions.

    The web UI switches Chainlit ChatProfiles by reconnecting the websocket, which
    wipes `cl.user_session` and fires on_chat_end. To make returning to a profile
    RESUME its session (live transcript intact) rather than cold-start it, the sessions
    live HERE — outside any single websocket session — keyed by profile. `build(key)`
    constructs a session on first use; later get() calls return the same object.

    Single-operator local scope: keyed by profile name only (one operator, one tab).
    """

    def __init__(self, *, build: Callable[[str], Any]):
        self._build = build
        self._cache: dict[str, Any] = {}

    def get(self, profile_key: str):
        if profile_key not in self._cache:
            self._cache[profile_key] = self._build(profile_key)
        return self._cache[profile_key]

    def has(self, profile_key: str) -> bool:
        return profile_key in self._cache

    def park_all(self) -> None:
        """Park every cached session's backlog (no data loss) without clearing any
        live transcript — used on disconnect so a later return resumes intact."""
        for sess in self._cache.values():
            try:
                sess.park_pending()
            except Exception:  # noqa: BLE001 — one bad session must not block the rest
                pass
