"""API tests for the belt endpoints — trigger (T1), cancel (T1), belt view, SSE.

A fast fake produce_fn is injected so the dispatcher never runs a real engine; the worker
marks the project done immediately, so the belt reflects it within a poll.
"""
from __future__ import annotations

import pathlib
import time

import chat_state
from fastapi.testclient import TestClient

from dashboard.app import create_app


def _fast_produce(slug=None, approve=None, root=None, progress=None,
                  station_locks=None, should_cancel=None):
    pdir = pathlib.Path(root) / slug
    proj = chat_state.load_json(pdir / "project.json", {})
    proj["status"] = "done"
    proj["stages"] = {k: {"status": "done", "artifact": None, "validated": True}
                      for k in proj.get("stages", {})}
    if progress is not None:
        progress.emit("done")
    chat_state.atomic_write_json(pdir / "project.json", proj)
    return {"status": "done", "video": "video.mp4"}


def _client(tmp_path) -> TestClient:
    app = create_app(projects_dir=tmp_path)
    app.state.produce_fn = _fast_produce       # injected BEFORE the dispatcher builds
    app.state.max_in_flight = 2
    return TestClient(app)


def _belt_video(client, slug):
    for _ in range(250):
        belt = client.get("/api/belt").json()
        v = next((x for x in belt["videos"] if x["slug"] == slug), None)
        if v and v["belt_state"] == "done":
            return v, belt
        time.sleep(0.02)
    return v, belt


def test_belt_empty_has_ten_stations(tmp_path):
    belt = _client(tmp_path).get("/api/belt").json()
    assert belt["videos"] == []
    assert len(belt["stations"]) == 10
    assert "live" in belt and "occupancy" in belt


def test_trigger_then_belt_shows_done_video(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/trigger", json={"topic": "noise cancelling headphones",
                                     "length": "short", "niche": "audio gear"})
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    v, belt = _belt_video(c, slug)
    assert v is not None and v["belt_state"] == "done", belt
    assert set(v["stages"]) and len(v["stages"]) == 10  # per-stage map for the spine row


def test_trigger_requires_a_topic_or_brief(tmp_path):
    r = _client(tmp_path).post("/api/trigger", json={})
    assert r.status_code == 400


def test_cancel_on_real_slug_ok_and_unknown_404(tmp_path):
    c = _client(tmp_path)
    slug = c.post("/api/trigger", json={"topic": "to cancel"}).json()["slug"]
    assert c.post(f"/api/cancel/{slug}").status_code == 200
    assert c.post("/api/cancel/no-such-project").status_code == 404


def test_event_stream_backfills_then_stops_on_disconnect(tmp_path):
    """Drive the SSE generator directly (an infinite stream can't be read cleanly via the
    test client): it backfills events since Last-Event-ID, formats valid `id:`/`data:`
    frames, and stops when the client disconnects (spec §10)."""
    import asyncio
    import json as _json

    from dashboard.app import _event_stream

    app = create_app(projects_dir=tmp_path)
    app.state.produce_fn = _fast_produce
    disp = app.state  # build the dispatcher and seed an event
    from dashboard.app import _get_dispatcher
    d = _get_dispatcher(app)
    d.events.emit("triggered", slug="abc", message="hello", initiator="ceo")

    class FakeRequest:
        def __init__(self):
            self._n = 0

        async def is_disconnected(self):
            # connected for the first poll (backfill), disconnected after
            self._n += 1
            return self._n > 1

    async def drive():
        frames = []
        async for chunk in _event_stream(d, 0, FakeRequest()):
            frames.append(chunk)
        return frames

    frames = asyncio.run(drive())
    body = "".join(frames)
    assert "data:" in body and "id:" in body
    # the emitted event is present and is valid JSON in its data: line
    data_lines = [ln[len("data: "):] for ln in body.splitlines()
                  if ln.startswith("data: ")]
    assert any(_json.loads(dl).get("slug") == "abc" for dl in data_lines)
