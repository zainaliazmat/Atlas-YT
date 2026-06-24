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


def test_resume_wait_returns_disk_outcome_and_shares_locks(tmp_path):
    """A T2 gate approval resumes through the belt: resume(wait=True) joins the worker and
    returns the spine's on-disk outcome, and the resumed run acquires the SAME station
    locks (it ran through pipeline._station like any belt run)."""
    fake, probe = make_fake_produce(stages=("research", "render"))
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2)
    # mint a project on disk to resume (a parked render-gate stand-in)
    slug = d.trigger(topic="resume-me")["slug"]
    assert _wait_status(tmp_path, slug, "done")
    out = d.resume(slug, "final_render", wait=True, timeout=8)
    assert out["resumed"] is True and out["status"] == "done"
    # the resume emitted a CEO-initiated gate_approved event (never the chat plane)
    ga = [e for e in d.events.since(0) if e["kind"] == "gate_approved"]
    assert ga and ga[-1]["initiator"] == "ceo" and ga[-1]["message"] == "final_render"
    # it went through the real station lock (probe records the render station ran)
    assert (slug, "render") in probe["ran"]


def test_resume_default_is_fire_and_forget(tmp_path):
    fake, probe = make_fake_produce(stages=("research", "render"))
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2)
    slug = d.trigger(topic="resume-async")["slug"]
    assert _wait_status(tmp_path, slug, "done")
    out = d.resume(slug, "final_render")
    assert out == {"slug": slug, "resumed": True}  # no blocking, no disk outcome inline


# ---------------------------------------------------------------- rerun (Re-run button)
def _mint_project(projects_dir, slug, stage_status, status="failed", gates_status=None):
    """Write a project.json on disk with the given per-stage + per-gate statuses (no real
    stages run). Lets the rerun tests set up an arbitrary 'already partly run' video."""
    gates_status = gates_status or {}
    proj = {
        "slug": slug, "status": status, "updated": 0.0,
        "config": {"gates": {pipeline.GATE_FACTCHECK: True,
                             pipeline.GATE_FINAL_RENDER: True}},
        "stages": {s.key: {"status": stage_status.get(s.key, "pending"),
                           "artifact": None, "validated": False}
                   for s in pipeline.STAGES},
        "gates": {pipeline.GATE_FACTCHECK:
                  {"status": gates_status.get(pipeline.GATE_FACTCHECK, "pending"),
                   "details": None},
                  pipeline.GATE_FINAL_RENDER:
                  {"status": gates_status.get(pipeline.GATE_FINAL_RENDER, "pending"),
                   "details": None}},
        "artifacts": {}, "history": [],
    }
    pdir = pathlib.Path(projects_dir) / slug
    pdir.mkdir(parents=True, exist_ok=True)
    chat_state.atomic_write_json(pdir / "project.json", proj)
    return slug


def make_recording_produce():
    """A fake spine that snapshots the per-stage + per-gate statuses AS IT FINDS THEM at
    entry (i.e. exactly what rerun() set up), then marks the project done."""
    rec = {"stages": None, "gates": None}
    ev = threading.Event()

    def fake(slug=None, approve=None, root=None, progress=None,
             station_locks=None, should_cancel=None):
        pdir = pathlib.Path(root) / slug
        proj = chat_state.load_json(pdir / "project.json", {})
        rec["stages"] = {k: (v or {}).get("status")
                         for k, v in (proj.get("stages", {}) or {}).items()}
        rec["gates"] = {g: (gd or {}).get("status")
                        for g, gd in (proj.get("gates", {}) or {}).items()}
        proj["status"] = "done"
        chat_state.atomic_write_json(pdir / "project.json", proj)
        ev.set()
        return {"status": "done"}

    return fake, rec, ev


def test_rerun_all_resets_every_stage_and_reearns_gates(tmp_path):
    fake, rec, ev = make_recording_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2)
    slug = _mint_project(tmp_path, "vid-done",
                         {s.key: "done" for s in pipeline.STAGES}, status="done",
                         gates_status={pipeline.GATE_FACTCHECK: "approved",
                                       pipeline.GATE_FINAL_RENDER: "approved"})
    out = d.rerun(slug)
    assert out["slug"] == slug and out["rerunning"] is True and out["from_stage"] is None
    assert ev.wait(8)
    # every stage reset to pending, both gates re-earned
    assert all(v == "pending" for v in rec["stages"].values()), rec["stages"]
    assert all(v == "pending" for v in rec["gates"].values()), rec["gates"]
    assert "rerun" in [e["kind"] for e in d.events.since(0)]


def test_rerun_from_stage_resets_downstream_keeps_upstream(tmp_path):
    fake, rec, ev = make_recording_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2)
    slug = _mint_project(tmp_path, "vid-mid",
                         {s.key: "done" for s in pipeline.STAGES}, status="done")
    out = d.rerun(slug, from_stage="script")
    assert out["rerunning"] is True and out["from_stage"] == "script"
    assert ev.wait(8)
    keys = [s.key for s in pipeline.STAGES]
    idx = keys.index("script")
    for k in keys[:idx]:
        assert rec["stages"][k] == "done", (k, rec["stages"])
    for k in keys[idx:]:
        assert rec["stages"][k] == "pending", (k, rec["stages"])


def test_rerun_reearns_only_gates_at_or_after_reset_point(tmp_path):
    fake, rec, ev = make_recording_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2)
    slug = _mint_project(tmp_path, "vid-render",
                         {s.key: "done" for s in pipeline.STAGES}, status="done",
                         gates_status={pipeline.GATE_FACTCHECK: "approved",
                                       pipeline.GATE_FINAL_RENDER: "approved"})
    # re-run only from render → factcheck gate (upstream) stays approved, final_render re-earned
    d.rerun(slug, from_stage="render")
    assert ev.wait(8)
    assert rec["gates"][pipeline.GATE_FACTCHECK] == "approved", rec["gates"]
    assert rec["gates"][pipeline.GATE_FINAL_RENDER] == "pending", rec["gates"]


def test_rerun_rejects_a_stage_that_never_ran(tmp_path):
    fake, rec, ev = make_recording_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2)
    # only research ran; render is still pending → cannot re-run from render
    slug = _mint_project(tmp_path, "vid-early", {"research": "done"}, status="failed")
    out = d.rerun(slug, from_stage="render")
    assert out["rerunning"] is False
    assert not ev.is_set()  # no worker started


def test_rerun_rejects_unknown_stage_and_slug(tmp_path):
    fake, rec, ev = make_recording_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2)
    slug = _mint_project(tmp_path, "vid-x",
                         {s.key: "done" for s in pipeline.STAGES}, status="done")
    assert d.rerun(slug, from_stage="not-a-stage")["rerunning"] is False
    assert d.rerun("no-such-slug")["rerunning"] is False


def test_reconcile_interrupted_parks_orphaned_running_and_queued(tmp_path):
    fake, rec, ev = make_recording_produce()
    # two zombies left behind by a killed session: a 'running' mid-research video and a
    # stranded 'queued' one. A 'done' video must be left untouched.
    _mint_project(tmp_path, "zombie-run", {"research": "running"}, status="running")
    _mint_project(tmp_path, "zombie-q", {}, status="queued")
    _mint_project(tmp_path, "finished",
                  {s.key: "done" for s in pipeline.STAGES}, status="done")
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2)
    out = d.reconcile_interrupted()
    assert set(out) == {"zombie-run", "zombie-q"}
    zr = chat_state.load_json(tmp_path / "zombie-run" / "project.json", {})
    assert zr["status"] == "interrupted"
    assert zr["stages"]["research"]["status"] == "pending"  # in-progress stage reset
    zq = chat_state.load_json(tmp_path / "zombie-q" / "project.json", {})
    assert zq["status"] == "interrupted"
    fin = chat_state.load_json(tmp_path / "finished" / "project.json", {})
    assert fin["status"] == "done"                          # settled video untouched
    assert "interrupted" in [e["kind"] for e in d.events.since(0)]


def test_reconcile_skips_a_video_with_a_live_worker(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def blocking_fake(slug=None, approve=None, root=None, progress=None,
                      station_locks=None, should_cancel=None):
        pdir = pathlib.Path(root) / slug
        proj = chat_state.load_json(pdir / "project.json", {})
        proj["status"] = "running"
        chat_state.atomic_write_json(pdir / "project.json", proj)
        started.set()
        release.wait(5)
        proj["status"] = "done"
        chat_state.atomic_write_json(pdir / "project.json", proj)
        return {"status": "done"}

    d = Dispatcher(projects_dir=tmp_path, produce_fn=blocking_fake, max_in_flight=2)
    slug = d.trigger(topic="genuinely running")["slug"]
    assert started.wait(5)
    # a LIVE running video is not a zombie — reconcile must leave it alone
    assert d.reconcile_interrupted() == []
    assert _status(tmp_path, slug) == "running"
    release.set()
    assert _wait_status(tmp_path, slug, "done", timeout=8)


def test_interrupted_video_can_be_rerun(tmp_path):
    fake, rec, ev = make_recording_produce()
    _mint_project(tmp_path, "zombie", {"research": "running"}, status="running")
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2)
    d.reconcile_interrupted()
    out = d.rerun("zombie")                 # the parked video re-runs on demand
    assert out["rerunning"] is True
    assert ev.wait(8)


def test_rerun_is_guarded_while_running(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def blocking_fake(slug=None, approve=None, root=None, progress=None,
                      station_locks=None, should_cancel=None):
        pdir = pathlib.Path(root) / slug
        proj = chat_state.load_json(pdir / "project.json", {})
        proj["status"] = "running"
        chat_state.atomic_write_json(pdir / "project.json", proj)
        started.set()
        release.wait(5)
        proj["status"] = "done"
        chat_state.atomic_write_json(pdir / "project.json", proj)
        return {"status": "done"}

    d = Dispatcher(projects_dir=tmp_path, produce_fn=blocking_fake, max_in_flight=2)
    slug = d.trigger(topic="busy")["slug"]
    assert started.wait(5)
    out = d.rerun(slug)  # a live worker is mid-run → refuse
    assert out["rerunning"] is False
    release.set()
    assert _wait_status(tmp_path, slug, "done", timeout=8)


def test_injected_decider_overrides_default_policy(tmp_path):
    """The seam is live: an injected decider that ESCALATES every failure parks a
    TRANSIENT failure immediately — even with retry budget left — proving the decider,
    not the hard-coded max_retries, now rules the outcome."""
    from supervisor import Decision

    def always_escalate(slug, result, context):
        return Decision("ESCALATE", stage=result.get("stage"),
                        reason="no retries by policy",
                        payload={"failure_kind": "transient"})

    fake, probe = make_fake_produce(outcomes={"script": "transient"}, transient_fails=1)
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, decide_fn=always_escalate,
                   max_in_flight=2, max_retries=3)
    slug = d.trigger(topic="no-retry-please")["slug"]
    assert _wait_status(tmp_path, slug, "failed", timeout=12), _status(tmp_path, slug)
    kinds = [e["kind"] for e in d.events.since(0)]
    assert "failed" in kinds and "retry" not in kinds


def test_execute_decision_proceed_emits_nothing(tmp_path):
    """A PROCEED decision is a pure no-op — NOT a spurious 'failed' event (review fix #1).
    Tested directly so it's deterministic (no threading)."""
    from supervisor import Decision
    fake, probe = make_fake_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake)
    before = d.events.last_id
    d._execute_decision("any-slug", {"status": "failed", "stage": "script"},
                        Decision("PROCEED"))
    assert d.events.last_id == before          # nothing emitted


def test_decider_is_not_called_for_terminal_outcomes(tmp_path):
    """Exceptions-only seam (D1): the decider must NEVER see a done/cancelled result —
    those are emitted directly by _on_result. A spy decider proves it."""
    from supervisor import safe_default_decider
    seen = []

    def spy(slug, result, context):
        seen.append(result.get("status"))
        return safe_default_decider(slug, result, context)

    fake, probe = make_fake_produce()          # clean run to done
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, decide_fn=spy, max_in_flight=2)
    slug = d.trigger(topic="clean-run")["slug"]
    assert _wait_status(tmp_path, slug, "done", timeout=12), _status(tmp_path, slug)
    assert "done" not in seen and "cancelled" not in seen
