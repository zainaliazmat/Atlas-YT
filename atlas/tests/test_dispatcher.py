"""Unit tests for the assembly-line dispatcher (the belt).

A FAKE produce_fn exercises the real `pipeline._station` lock + `should_cancel` hook the
dispatcher passes in — so these prove the scheduling guarantees (station=stage single
occupancy, in-flight cap, cancel, transient-vs-deterministic retry) with no LLM and no
real stages. The spine's own behaviour is guarded by the existing pipeline suite.
"""
from __future__ import annotations

import pathlib
import threading
import time

import chat_state
import pipeline
from dispatcher import Dispatcher


# ---------------------------------------------------------------- a fake spine
def make_fake_produce(stages=("research", "script", "render"), hold=0.02,
                      outcomes=None, transient_fails=1):
    """Return (fake_produce, probe). The fake walks `stages`, honouring station_locks +
    should_cancel exactly like the real produce(). `probe["seen"][stage]` records the MAX
    concurrent occupants per station (must stay <= 1 under single-occupancy).

    outcomes: {stage_key: 'transient'|'deterministic'} injects a failure at that stage.
      'deterministic' always fails; 'transient' fails the first `transient_fails`
      attempts at that stage (per slug) then succeeds — to test retry-then-recover.
    """
    outcomes = outcomes or {}
    probe = {"seen": {}, "cur": {}, "ran": set()}
    m = threading.Lock()
    fail_counts: dict[str, int] = {}

    def fake(slug=None, approve=None, root=None, progress=None,
             station_locks=None, should_cancel=None):
        pdir = pathlib.Path(root) / slug
        proj = chat_state.load_json(pdir / "project.json", {})
        proj["status"] = "running"
        chat_state.atomic_write_json(pdir / "project.json", proj)
        for key in stages:
            if should_cancel is not None and should_cancel():
                proj["status"] = "cancelled"
                chat_state.atomic_write_json(pdir / "project.json", proj)
                return {"status": "cancelled", "stage": key}
            with pipeline._station(station_locks, key):
                with m:
                    probe["cur"][key] = probe["cur"].get(key, 0) + 1
                    probe["seen"][key] = max(probe["seen"].get(key, 0), probe["cur"][key])
                    probe["ran"].add((slug, key))
                if progress is not None:
                    progress.emit(f"{key} running")
                time.sleep(hold)
                with m:
                    probe["cur"][key] -= 1
            if key in outcomes:
                kind = outcomes[key]
                ck = f"{slug}:{key}"
                with m:
                    prior = fail_counts.get(ck, 0)
                    fail_counts[ck] = prior + 1
                fail = kind == "deterministic" or prior < transient_fails
                if fail:
                    proj["status"] = "failed"
                    chat_state.atomic_write_json(pdir / "project.json", proj)
                    return {"status": "failed", "stage": key, "failure_kind": kind,
                            "errors": [f"boom at {key}"]}
        proj["status"] = "done"
        chat_state.atomic_write_json(pdir / "project.json", proj)
        return {"status": "done", "video": "video.mp4"}

    return fake, probe


def _status(projects_dir, slug):
    proj = chat_state.load_json(pathlib.Path(projects_dir) / slug / "project.json", {})
    return proj.get("status")


def _wait_status(projects_dir, slug, target, timeout=8.0):
    targets = target if isinstance(target, (set, tuple, list)) else {target}
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _status(projects_dir, slug) in targets:
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------- tests
def test_trigger_creates_then_runs_to_done(tmp_path):
    fake, probe = make_fake_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2)
    slug = d.trigger(topic="noise cancelling headphones")["slug"]
    assert (tmp_path / slug / "project.json").exists()
    assert _wait_status(tmp_path, slug, "done")
    kinds = [e["kind"] for e in d.events.since(0)]
    assert "triggered" in kinds and "done" in kinds


def test_station_single_occupancy(tmp_path):
    """Three videos on the belt — never two in the SAME station at once (§6.3)."""
    fake, probe = make_fake_produce(hold=0.05)
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=3)
    slugs = [d.trigger(topic=f"v{i}")["slug"] for i in range(3)]
    for s in slugs:
        assert _wait_status(tmp_path, s, "done", timeout=12)
    assert probe["seen"], "no stations ran"
    assert all(v <= 1 for v in probe["seen"].values()), probe["seen"]


def test_max_in_flight_caps_concurrency(tmp_path):
    """cap=1 → one-active-plus-a-queue (§6.6): the 2nd video stays queued until a slot frees."""
    fake, probe = make_fake_produce(hold=0.08)
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=1)
    a = d.trigger(topic="A")["slug"]
    b = d.trigger(topic="B")["slug"]
    time.sleep(0.05)
    assert len(d.live_state()["running"]) <= 1, d.live_state()
    for s in (a, b):
        assert _wait_status(tmp_path, s, "done", timeout=12)


def test_cancel_running_video_stops(tmp_path):
    fake, probe = make_fake_produce(stages=("research", "script", "render"), hold=0.12)
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2)
    slug = d.trigger(topic="cancel me")["slug"]
    assert _wait_status(tmp_path, slug, "running", timeout=5)
    d.cancel(slug)
    assert _wait_status(tmp_path, slug, "cancelled", timeout=5)
    kinds = [e["kind"] for e in d.events.since(0)]
    assert "cancel_requested" in kinds and "cancelled" in kinds


def test_cancel_queued_video_marks_disk_without_running(tmp_path):
    fake, probe = make_fake_produce(hold=0.1)
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=1)
    a = d.trigger(topic="A-long")["slug"]
    b = d.trigger(topic="B-queued")["slug"]
    time.sleep(0.03)
    d.cancel(b)
    assert _wait_status(tmp_path, b, "cancelled", timeout=12)
    assert _wait_status(tmp_path, a, "done", timeout=12)
    assert not any(s == b for (s, _k) in probe["ran"]), probe["ran"]


def test_transient_failure_retries_then_recovers(tmp_path):
    fake, probe = make_fake_produce(outcomes={"script": "transient"}, transient_fails=1)
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2, max_retries=1)
    slug = d.trigger(topic="flaky")["slug"]
    assert _wait_status(tmp_path, slug, "done", timeout=12), _status(tmp_path, slug)
    assert "retry" in [e["kind"] for e in d.events.since(0)]


def test_deterministic_failure_is_not_retried(tmp_path):
    fake, probe = make_fake_produce(outcomes={"script": "deterministic"})
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2, max_retries=3)
    slug = d.trigger(topic="bad-contract")["slug"]
    assert _wait_status(tmp_path, slug, "failed", timeout=12)
    kinds = [e["kind"] for e in d.events.since(0)]
    assert "failed" in kinds and "retry" not in kinds


def test_event_ring_backfill(tmp_path):
    fake, probe = make_fake_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2)
    slug = d.trigger(topic="events")["slug"]
    assert _wait_status(tmp_path, slug, "done")
    last = d.events.last_id
    assert last >= 2
    mid = last // 2
    newer = d.events.since(mid)
    assert all(e["id"] > mid for e in newer)
    assert len(newer) == last - mid


def test_retry_restarts_a_parked_failed_video(tmp_path):
    """An explicit operator retry (the UI's RETRY) resets the parked failed stage and
    drives the video forward — even when the dispatcher's OWN auto-retry is exhausted."""
    fake, probe = make_fake_produce(outcomes={"script": "transient"}, transient_fails=1)
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2, max_retries=0)
    slug = d.trigger(topic="park-then-retry")["slug"]
    # max_retries=0 → the first transient failure parks it as failed
    assert _wait_status(tmp_path, slug, "failed", timeout=12), _status(tmp_path, slug)
    out = d.retry(slug)
    assert out["slug"] == slug and out.get("retrying")
    # the retry clears the one transient failure → it now reaches done
    assert _wait_status(tmp_path, slug, "done", timeout=12), _status(tmp_path, slug)
    assert "retry" in [e["kind"] for e in d.events.since(0)]


def test_retry_unknown_slug_is_a_safe_noop(tmp_path):
    fake, probe = make_fake_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2)
    out = d.retry("no-such-slug")
    assert out["slug"] == "no-such-slug" and out.get("retrying") is False
