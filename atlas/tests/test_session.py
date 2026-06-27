"""The UI-neutral session core (session.py) that BOTH the terminal REPL and the web
UI drive. No LLM, no network — fake orchestrator/adapters, list-backed callbacks, a
tmp state file. Mirrors the repo's injectable style (make_distiller(chat_fn=...),
list_progress()).
"""
import json

import registry
import session


# ----------------------------------------------------------------------
# Fakes — a fake orchestrator that streams text + emits status on the SAME
# Progress the real one is built with, and fake adapters for direct-address.
# ----------------------------------------------------------------------
class _FakeAdapter:
    def __init__(self, entry):
        self.entry = entry
        self.asked = []

    def ask(self, question, context=""):
        self.asked.append((question, context))
        return f"{self.entry.name} says hi"


class _FakeOrch:
    """Stands in for orchestrator.Orchestrator: streams two text chunks and emits
    one deterministic status line through the Progress it was built with."""

    def __init__(self, progress):
        self.progress = progress
        self.adapters = {e.name: _FakeAdapter(e) for e in registry.REGISTRY}
        self.calls = []

    def ask(self, user_msg, *, context="", on_text=None):
        self.calls.append({"msg": user_msg, "context": context})
        if on_text:
            on_text("thinking… ")
        self.progress.emit("🔎 Scout is scanning…")
        if on_text:
            on_text("done.")
        return "Atlas reply"


def _new_session(tmp_path, summary="", transcript=None, pending=None):
    state = {"summary": summary, "transcript": list(transcript or []), "pending": pending}
    distiller = session.make_distiller(lambda system, user: "DISTILLED")
    return session.AtlasSession(
        state=state, distiller=distiller, state_path=tmp_path / "cs.json",
        build_orch=lambda progress: _FakeOrch(progress))


# ----------------------------------------------------------------------
# send() — the heart of a meeting turn
# ----------------------------------------------------------------------
def test_send_streams_text_routes_status_and_records_both_turns(tmp_path):
    s = _new_session(tmp_path)
    text_chunks, status_lines = [], []

    reply = s.send("find me a topic", on_text=text_chunks.append,
                   on_status=status_lines.append)

    assert reply == "Atlas reply"
    assert text_chunks == ["thinking… ", "done."]          # streamed live
    assert status_lines == ["🔎 Scout is scanning…"]        # deterministic status routed
    assert s.state["transcript"] == [
        {"role": "user", "content": "find me a topic"},
        {"role": "atlas", "content": "Atlas reply"},
    ]


def test_send_passes_bounded_context_summary_and_recent_window(tmp_path):
    s = _new_session(tmp_path, summary="CEO likes terse updates",
                     transcript=[{"role": "user", "content": "earlier turn"}])
    s.send("now", on_text=lambda t: None, on_status=lambda m: None)

    ctx = s.orch.calls[0]["context"]
    assert "CEO likes terse updates" in ctx          # durable summary folded in
    assert "earlier turn" in ctx                       # recent window folded in
    assert "now" not in ctx                            # the new msg is NOT in context


def test_status_sink_is_cleared_after_turn(tmp_path):
    """The status sink is per-turn: a stray emit between turns is a harmless no-op."""
    s = _new_session(tmp_path)
    s.send("hi", on_text=lambda t: None, on_status=lambda m: None)
    assert s._status_cb is None                         # per-turn sink cleared
    s.progress.emit("orphan status")                   # nowhere to route -> no crash


# ----------------------------------------------------------------------
# ask_agent() — deterministic direct address (bypasses the orchestrator LLM)
# ----------------------------------------------------------------------
def test_ask_agent_routes_to_named_agent_and_records_exchange(tmp_path):
    s = _new_session(tmp_path, summary="ctx")
    entry, reply = s.ask_agent("scout", "is faceless dead?")

    assert entry.name == "scout"
    assert reply == "scout says hi"
    assert s.orch.adapters["scout"].asked == [("is faceless dead?", "ctx")]
    assert s.orch.adapters["sage"].asked == []         # only the named agent
    assert s.state["transcript"]                        # exchange recorded


def test_ask_agent_unknown_returns_none_and_records_nothing(tmp_path):
    s = _new_session(tmp_path)
    entry, reply = s.ask_agent("nobody", "hi")
    assert entry is None and reply is None
    assert s.state["transcript"] == []


# ----------------------------------------------------------------------
# Memory lifecycle — close / new_thread / summarize / park_pending / start.
# The same summary-only distill the REPL uses, on the same boundaries.
# ----------------------------------------------------------------------
def test_close_distills_clears_transcript_and_persists(tmp_path):
    s = _new_session(tmp_path, summary="old",
                     transcript=[{"role": "user", "content": "we chose topic A"}])
    ok = s.close()
    assert ok is True
    assert s.state["summary"] == "DISTILLED"
    assert s.state["transcript"] == []
    data = json.loads((tmp_path / "cs.json").read_text())
    assert data["summary"] == "DISTILLED" and "pending" not in data


def test_new_thread_clears_transcript_even_when_distill_fails(tmp_path):
    state = {"summary": "keep", "pending": None,
             "transcript": [{"role": "user", "content": "precious"}]}

    def boom(summary, transcript):
        raise RuntimeError("distiller down")

    s = session.AtlasSession(state=state, distiller=boom,
                             state_path=tmp_path / "cs.json",
                             build_orch=lambda p: _FakeOrch(p))
    ok = s.new_thread()
    assert ok is False                                  # distill failed
    assert s.state["transcript"] == []                  # thread still cleared
    assert s.state["summary"] == "keep"                 # summary not lost
    data = json.loads((tmp_path / "cs.json").read_text())
    assert data["pending"] == [{"role": "user", "content": "precious"}]  # parked, safe


def test_summarize_returns_ok_and_body_and_is_noop_when_empty(tmp_path):
    s = _new_session(tmp_path, summary="remembered things", transcript=[])
    ok, body = s.summarize()
    assert ok is True
    assert body == "remembered things"                  # no transcript -> unchanged


def test_park_pending_saves_backlog_without_distilling(tmp_path):
    calls = []
    s = _new_session(tmp_path, summary="safe",
                     transcript=[{"role": "user", "content": "unsaved meeting"}])
    s.distiller = lambda summary, transcript: calls.append(1) or "SHOULD-NOT-RUN"
    s.park_pending()
    assert calls == []                                  # distiller NOT invoked
    data = json.loads((tmp_path / "cs.json").read_text())
    assert data["summary"] == "safe"
    assert data["pending"] == [{"role": "user", "content": "unsaved meeting"}]


def test_start_folds_pending_from_disk_into_summary(tmp_path):
    # A prior session crashed mid-distill and parked raw turns under "pending".
    sp = tmp_path / "cs.json"
    sp.write_text(json.dumps({
        "summary": "base",
        "pending": [{"role": "user", "content": "stranded turn"}],
    }))
    seen = {}

    def distiller(summary, transcript):
        seen["n"] = len(transcript)
        return "folded"

    s = session.AtlasSession.start(state_path=sp, distiller=distiller,
                                   build_orch=lambda p: _FakeOrch(p))
    assert seen["n"] == 1                                # the stranded turn was folded
    assert s.state["summary"] == "folded"
    assert s.state["transcript"] == []                  # fresh transcript for new session
    data = json.loads(sp.read_text())
    assert "pending" not in data                         # cleared after recovery


# ----------------------------------------------------------------------
# Phase C — SessionRegistry (resume, never cold-start) + AgentSession (persona chat).
# ----------------------------------------------------------------------
def test_session_registry_resumes_same_object_and_builds_once():
    built = []
    reg = session.SessionRegistry(build=lambda key: built.append(key) or {"key": key,
                                                                          "log": []})
    a = reg.get("scout")
    a["log"].append("hi")                                # mutate the "transcript"
    b = reg.get("scout")
    assert b is a                                        # RESUME: same object
    assert b["log"] == ["hi"]                            # transcript intact
    assert built == ["scout"]                            # built once, not cold-started
    other = reg.get("sage")
    assert other is not a and built == ["scout", "sage"]  # distinct per profile


def test_session_registry_park_all_parks_every_cached_session():
    parked = []

    class _S:
        def __init__(self, k): self.k = k
        def park_pending(self): parked.append(self.k)

    reg = session.SessionRegistry(build=lambda key: _S(key))
    reg.get("scout"); reg.get("sage")
    reg.park_all()
    assert sorted(parked) == ["sage", "scout"]


def _agent_session(tmp_path, name="scout", summary="", transcript=None):
    entry = registry.get_entry(name)
    adapter = _FakeAdapter(entry)
    state = {"summary": summary, "transcript": list(transcript or []), "pending": None}
    return session.AgentSession(
        entry=entry, adapter=adapter, state=state,
        distiller=session.make_distiller(lambda s, u: "AGENT-SUMMARY"),
        state_path=tmp_path / f"{name}.json"), adapter


def test_agent_session_send_routes_to_adapter_with_context_and_records(tmp_path):
    s, adapter = _agent_session(tmp_path, "scout", summary="Scout knows faceless niches",
                                transcript=[{"role": "user", "content": "earlier ask"}])
    out = []
    reply = s.send("is faceless dead?", on_text=out.append)

    assert reply == "scout says hi"
    assert out == ["scout says hi"]                      # non-streaming persona reply
    q, ctx = adapter.asked[0]
    assert q == "is faceless dead?"
    assert "Scout knows faceless niches" in ctx          # summary in context
    assert "earlier ask" in ctx                          # recent transcript in context
    roles = [t["role"] for t in s.state["transcript"]]
    assert roles[-2:] == ["user", "agent"]              # the new exchange appended


def test_agent_session_park_preserves_in_ram_transcript(tmp_path):
    # The Phase C guarantee: parking on disconnect/switch must NOT clear the live
    # transcript, so switching back RESUMES the conversation intact.
    s, _ = _agent_session(tmp_path, "sage",
                          transcript=[{"role": "user", "content": "keep me"}])
    s.park_pending()
    assert s.state["transcript"] == [{"role": "user", "content": "keep me"}]  # intact
    data = json.loads((tmp_path / "sage.json").read_text())
    assert data["pending"] == [{"role": "user", "content": "keep me"}]        # recovery copy


def test_agent_session_close_distills_and_clears(tmp_path):
    s, _ = _agent_session(tmp_path, "scout", summary="old",
                          transcript=[{"role": "user", "content": "a chat"}])
    ok = s.close()
    assert ok is True
    assert s.state["summary"] == "AGENT-SUMMARY"
    assert s.state["transcript"] == []
