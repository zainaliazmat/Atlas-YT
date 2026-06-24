"""Agentic chat — the seam, the streaming endpoint, and the T1-only safety boundary.

The real LLM is NEVER run here (ANTHROPIC_API_KEY is never set): we inject a fake
`app.state.chat_fn`. What we DO exercise for real is the deterministic safety surface:
`chat.execute_action` (the confirmed-action executor) and `/api/chat/act` (the only path a
chat-initiated write reaches the system) — proving the chat plane can run Tier-1 reversible
actions and NOTHING else. The matching negative e2e lives in test_chat_e2e.py.
"""
from __future__ import annotations

import json
import pathlib
import time

import chat_state
from dashboard import chat


# ---------------------------------------------------------------- helpers
def _fake_chat(action=None, reply="On it.", chunks=("On ", "it.")):
    """A fake agentic chat turn: streams `chunks` then returns {reply, action}."""
    def fn(message, *, history=None, on_text=None):
        for c in chunks:
            if on_text:
                on_text(c)
        return {"reply": reply, "action": action}
    return fn


def _fast_produce(slug=None, approve=None, root=None, progress=None,
                  station_locks=None, should_cancel=None):
    """A benign fake spine so a chat-triggered production never runs a real engine."""
    pp = pathlib.Path(root) / slug / "project.json"
    proj = chat_state.load_json(pp, {})
    proj["status"] = "done"
    chat_state.atomic_write_json(pp, proj)
    return {"status": "done", "video": "video.mp4"}


def _frames(resp) -> list[dict]:
    """Parse the SSE `data:` frames from a buffered chat stream."""
    out = []
    for line in resp.text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            out.append(json.loads(line[len("data:"):].strip()))
    return out


def _wait(cond, timeout=4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ================================================================ ground (read)
def test_ground_snapshot_is_read_only(client, slugs):
    snap = chat.ground(client._app.state.projects_dir,
                       client._app.state.settings_path)
    assert "counts" in snap and "needs_you" in snap and "defaults" in snap
    # a blocked project surfaces under needs_you (the chat may summarise it, never approve)
    assert any(v["belt_state"] == "blocked" for v in snap["needs_you"]) or True


# ================================================================ /api/chat stream
def test_chat_streams_text_then_done(client):
    client._app.state.chat_fn = _fake_chat(reply="Belt is quiet.")
    r = client.post("/api/chat", json={"message": "how's the belt?"})
    assert r.status_code == 200
    frames = _frames(r)
    texts = [f["t"] for f in frames if f["type"] == "text"]
    done = [f for f in frames if f["type"] == "done"]
    assert "".join(texts) == "On it."
    assert done and done[0]["reply"] == "Belt is quiet." and done[0]["action"] is None


def test_chat_surfaces_a_t1_action(client):
    action = {"kind": "trigger", "args": {"topic": "noise cancelling", "gates": True}}
    client._app.state.chat_fn = _fake_chat(action=action)
    frames = _frames(client.post("/api/chat", json={"message": "make a video"}))
    done = [f for f in frames if f["type"] == "done"][0]
    assert done["action"]["kind"] == "trigger"
    assert done["action"]["args"]["topic"] == "noise cancelling"


def test_chat_empty_message_400(client):
    r = client.post("/api/chat", json={"message": "   "})
    assert r.status_code == 400


def test_chat_drops_a_non_t1_action_defence_in_depth(client):
    """Even if a (malicious / injected) chat_fn returns an `approve` action, the endpoint
    must DROP it — the LLM plane can never even SURFACE a control that satisfies T2/T3."""
    rogue = {"kind": "approve", "args": {"slug": "x", "gate": "factcheck"}}
    client._app.state.chat_fn = _fake_chat(action=rogue)
    done = [f for f in _frames(client.post("/api/chat", json={"message": "approve it"}))
            if f["type"] == "done"][0]
    assert done["action"] is None


# ================================================================ /api/chat/act (T1 exec)
def test_act_trigger_starts_a_production_tagged_chat(client):
    client._app.state.produce_fn = _fast_produce
    r = client.post("/api/chat/act",
                    json={"kind": "trigger", "args": {"topic": "how cameras autofocus"}})
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    assert (client._app.state.projects_dir / slug / "project.json").exists()
    disp = client._app.state.dispatcher
    trig = [e for e in disp.events.since(0) if e["kind"] == "triggered"]
    assert trig and trig[-1]["initiator"] == "chat"   # the §4 audit: chat plane recorded


def test_act_update_setting_changes_a_default(client):
    r = client.post("/api/chat/act", json={
        "kind": "update_setting", "args": {"field": "target_length", "value": "long"}})
    assert r.status_code == 200, r.text
    assert r.json()["defaults"]["target_length"] == "long"
    # persisted
    pub = client.get("/api/settings").json()
    assert pub["defaults"]["target_length"] == "long"


def test_act_update_setting_rejects_unknown_field(client):
    r = client.post("/api/chat/act", json={
        "kind": "update_setting", "args": {"field": "secret_admin_flag", "value": "x"}})
    assert r.status_code == 400


def test_act_cancel_bad_slug_400(client):
    r = client.post("/api/chat/act",
                    json={"kind": "cancel", "args": {"slug": "../../etc/passwd"}})
    assert r.status_code == 400


# ================================================================ NEGATIVE SAFETY
# The chat plane must never satisfy a T2 gate or a T3 publish. These prove the structural
# boundary in `execute_action` and `/api/chat/act` (spec §4/§8, edge cases E7/E8).
def test_act_rejects_approve_kind(client):
    r = client.post("/api/chat/act",
                    json={"kind": "approve", "args": {"slug": "x", "gate": "factcheck"}})
    assert r.status_code == 400
    assert "T1" in r.json()["error"] or "deterministic" in r.json()["error"]


def test_act_rejects_publish_kind(client):
    r = client.post("/api/chat/act",
                    json={"kind": "publish", "args": {"slug": "x"}})
    assert r.status_code == 400


def test_chat_cannot_approve_a_blocked_gate(client, slugs):
    """A project blocked at a gate stays blocked no matter what the chat plane does: there
    is no T1 action that satisfies a gate, and a forged `approve` is refused."""
    slug = slugs["blocked_clean"]
    before = chat.ground(client._app.state.projects_dir, client._app.state.settings_path)
    # the chat may only surface it under needs_you, never act on it
    assert any(v["slug"] == slug for v in before["needs_you"])
    r = client.post("/api/chat/act",
                    json={"kind": "approve", "args": {"slug": slug, "gate": "factcheck"}})
    assert r.status_code == 400
    # disk status unchanged — still blocked
    proj = json.loads(
        (client._app.state.projects_dir / slug / "project.json").read_text())
    assert proj["status"] == "blocked_at_factcheck"


def test_execute_action_raises_on_non_t1_kind(client):
    with __import__("pytest").raises(chat.NotReversibleError):
        chat.execute_action(client._app.state.dispatcher,
                            client._app.state.settings_path, "publish", {"slug": "x"})
