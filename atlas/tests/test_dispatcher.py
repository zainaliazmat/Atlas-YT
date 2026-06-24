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
                      outcomes=None, transient_fails=1, final_runtime_sec=120):
    """Return (fake_produce, probe). The fake walks `stages`, honouring station_locks +
    should_cancel exactly like the real produce(). `probe["seen"][stage]` records the MAX
    concurrent occupants per station (must stay <= 1 under single-occupancy).

    outcomes: {stage_key: 'transient'|'deterministic'|'blocked_final'} injects a failure
      at that stage.
      'deterministic' always fails; 'transient' fails the first `transient_fails`
      attempts at that stage (per slug) then succeeds — to test retry-then-recover.
      'blocked_final' writes a blocked_at_final_render project with a render plan
      carrying est_runtime_sec=final_runtime_sec and returns the gate-blocked result.
    """
    outcomes = outcomes or {}
    probe = {"seen": {}, "cur": {}, "ran": []}
    m = threading.Lock()
    fail_counts: dict[str, int] = {}

    def fake(slug=None, approve=None, root=None, progress=None,
             station_locks=None, should_cancel=None):
        pdir = pathlib.Path(root) / slug
        proj = chat_state.load_json(pdir / "project.json", {})
        proj["status"] = "running"
        chat_state.atomic_write_json(pdir / "project.json", proj)
        # track which stages completed successfully so we can persist them on failure
        # (rerun(from_stage=X) needs X to be non-pending on disk)
        completed: dict[str, dict] = {}
        for key in stages:
            if should_cancel is not None and should_cancel():
                proj["status"] = "cancelled"
                chat_state.atomic_write_json(pdir / "project.json", proj)
                return {"status": "cancelled", "stage": key}
            with pipeline._station(station_locks, key):
                with m:
                    probe["cur"][key] = probe["cur"].get(key, 0) + 1
                    probe["seen"][key] = max(probe["seen"].get(key, 0), probe["cur"][key])
                    probe["ran"].append((slug, key))
                if progress is not None:
                    progress.emit(f"{key} running")
                time.sleep(hold)
                with m:
                    probe["cur"][key] -= 1
            if key in outcomes:
                kind = outcomes[key]
                if kind == "blocked_final":
                    # Re-read to preserve supervisor data the dispatcher wrote; flush
                    # upstream stage completions so the project is consistent on disk.
                    proj = chat_state.load_json(pdir / "project.json", {})
                    proj["status"] = "blocked_at_final_render"
                    for ck2, cv in completed.items():
                        proj.setdefault("stages", {})[ck2] = cv
                    proj.setdefault("gates", {})["final_render"] = {
                        "status": "blocked",
                        "details": {
                            "working_title": "T", "scenes": 3,
                            "est_runtime_sec": final_runtime_sec,
                            "audio_duration_sec": final_runtime_sec,
                        },
                    }
                    chat_state.atomic_write_json(pdir / "project.json", proj)
                    return {"status": "blocked", "gate": "final_render", "stage": key,
                            "reason": "awaiting render sign-off", "slug": slug}
                ck = f"{slug}:{key}"
                with m:
                    prior = fail_counts.get(ck, 0)
                    fail_counts[ck] = prior + 1
                fail = kind == "deterministic" or prior < transient_fails
                if fail:
                    # Re-read to preserve supervisor/revision data the dispatcher wrote;
                    # also flush any upstream stages completed in this run.
                    proj = chat_state.load_json(pdir / "project.json", {})
                    proj["status"] = "failed"
                    for ck2, cv in completed.items():
                        proj.setdefault("stages", {})[ck2] = cv
                    proj.setdefault("stages", {})[key] = {
                        "status": "failed", "artifact": None, "validated": False}
                    chat_state.atomic_write_json(pdir / "project.json", proj)
                    return {"status": "failed", "stage": key, "failure_kind": kind,
                            "errors": [f"boom at {key}"]}
            # record in-memory; written to disk only when a downstream stage fails or
            # when the run ends — avoids an extra atomic_write between stages that would
            # break timing-sensitive cancel tests.
            completed[key] = {"status": "done", "artifact": f"{key}.artifact",
                              "validated": True}
        # clean run — persist all stage statuses + final "done"
        proj = chat_state.load_json(pdir / "project.json", {})
        for ck2, cv in completed.items():
            proj.setdefault("stages", {})[ck2] = cv
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
    assert _wait_status(tmp_path, slug, "done", timeout=15)  # load-robust under the full suite
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
    # wait for the "failed" event too — _on_result runs after produce() returns (disk is
    # already "failed" from the fake), so the event may arrive slightly after disk status
    _wait_proj_cond(tmp_path, slug, lambda _p, k: "failed" in k, timeout=5, dispatcher=d)
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
    # wait for the "failed" event too — _on_result runs after produce() returns (disk is
    # already "failed" from the fake), so the event may arrive slightly after disk status
    _wait_proj_cond(tmp_path, slug, lambda _p, k: "failed" in k, timeout=5, dispatcher=d)
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


def test_slow_decision_does_not_hold_an_inflight_slot(tmp_path):
    """A slow decider for video A must NOT throttle the belt: with max_in_flight=1, video B
    still runs its stage and reaches its terminal state while A's decision sleeps — proving
    _on_result (the decision) runs AFTER the in-flight slot is released (spec §1, Slice 2).
    Under the old code A would hold the only slot through its slow decision and B could not
    even start."""
    import threading, time as _t
    from supervisor import Decision

    release_a = threading.Event()

    def slow_decider(slug, result, context):
        if "aaa" in slug:
            release_a.wait(timeout=10)          # A's decision blocks, holding NO in-flight slot
        return Decision("ESCALATE", stage=result.get("stage"),
                        payload={"failure_kind": "deterministic"})

    # Both videos fail at script (deterministic); only A's DECISION is slow.
    fake, _ = make_fake_produce(outcomes={"script": "deterministic"})
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, decide_fn=slow_decider,
                   max_in_flight=1, max_retries=0)
    a = d.trigger(topic="aaa-slow")["slug"]
    _t.sleep(0.3)                                # let A reach its (sleeping) decision
    b = d.trigger(topic="bbb-fast")["slug"]
    # B must reach its terminal 'failed' state even though A's decision still sleeps (and would,
    # under the OLD in-slot decision, still hold the only in-flight slot so B could not run).
    assert _wait_status(tmp_path, b, "failed", timeout=12), _status(tmp_path, b)
    release_a.set()
    assert _wait_status(tmp_path, a, "failed", timeout=12), _status(tmp_path, a)


# ---------------------------------------------------------------- helpers (Task 5)
def _proj(projects_dir, slug):
    """Load and return the full project dict from disk."""
    return chat_state.load_json(pathlib.Path(projects_dir) / slug / "project.json", {})


def _wait_proj_cond(projects_dir, slug, cond, timeout=20.0, dispatcher=None):
    """Poll until `cond(proj, events) is True` or timeout. Returns the final project dict.
    `dispatcher` is optional — when given, events are passed to `cond` as a list of kinds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        proj = _proj(projects_dir, slug)
        kinds = [e["kind"] for e in dispatcher.events.since(0)] if dispatcher else []
        if cond(proj, kinds):
            return proj
        time.sleep(0.05)
    proj = _proj(projects_dir, slug)
    return proj


# ---------------------------------------------------------------- Task 5: FIX_AND_RERUN + caps
def test_fix_and_rerun_persists_hint_and_reruns_from_stage(tmp_path):
    """FIX_AND_RERUN persists a revision hint and re-runs from the named stage."""
    from supervisor import Decision
    fake, probe = make_fake_produce(outcomes={"script": "deterministic"})

    calls = {"n": 0}
    def fixer(slug, result, context):
        calls["n"] += 1
        if calls["n"] == 1:
            return Decision("FIX_AND_RERUN", stage="script", gate="factcheck",
                            instructions="drop the unsourced stat", reason="unsupported")
        return Decision("ESCALATE", stage="script", payload={"failure_kind": "deterministic"})

    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, decide_fn=fixer,
                   max_in_flight=2, max_retries=0)
    slug = d.trigger(topic="needs-a-fix")["slug"]
    # Wait for STABLE final: "failed" + "fixing" emitted (FIX_AND_RERUN ran) + fix_attempts==1.
    # The second ESCALATE parks it permanently as "failed".
    proj = _wait_proj_cond(tmp_path, slug,
        lambda p, k: (p.get("status") == "failed"
                      and p.get("supervisor", {}).get("fix_attempts", {}).get("factcheck") == 1
                      and "revision" in p
                      and "fixing" in k),
        timeout=12, dispatcher=d)
    assert proj.get("status") == "failed", _status(tmp_path, slug)
    assert proj["revision"]["hint"] == "drop the unsourced stat"
    assert proj["revision"]["stage"] == "script"
    assert proj["supervisor"]["fix_attempts"]["factcheck"] == 1
    kinds = [e["kind"] for e in d.events.since(0)]
    assert "fixing" in kinds


def test_factcheck_fix_capped_then_escalates(tmp_path):
    """The 3rd factcheck FIX_AND_RERUN is forced to ESCALATE regardless of the decider —
    the bounded auto-fix never loops and never approves the block."""
    from supervisor import Decision
    # Always blocks at factcheck; decider always wants to keep fixing.
    fake, probe = make_fake_produce(stages=("research", "script", "factcheck"),
                                    outcomes={"factcheck": "deterministic"})

    def always_fix(slug, result, context):
        return Decision("FIX_AND_RERUN", stage="script", gate="factcheck",
                        instructions="try again", reason="still flagged")

    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, decide_fn=always_fix,
                   max_in_flight=2, max_retries=0, max_fix_attempts=2)
    slug = d.trigger(topic="unfixable")["slug"]
    # Wait for the fully-settled state: fix_attempts == 2 (both fix runs done) AND
    # "blocked" in events (the 3rd call hit the cap and _escalate emitted "blocked").
    proj = _wait_proj_cond(tmp_path, slug,
        lambda p, k: (p.get("supervisor", {}).get("fix_attempts", {}).get("factcheck") == 2
                      and "blocked" in k),
        timeout=20, dispatcher=d)
    assert proj.get("supervisor", {}).get("fix_attempts", {}).get("factcheck") == 2, \
        f"fix_attempts not 2: {proj.get('supervisor')}"
    kinds = [e["kind"] for e in d.events.since(0)]
    assert kinds.count("fixing") == 2 and "blocked" in kinds      # 2 fixes, then escalate


def test_decision_budget_forces_escalation(tmp_path):
    """A per-video decision budget caps belt-re-running actions; over budget → escalate."""
    from supervisor import Decision
    fake, probe = make_fake_produce(outcomes={"script": "transient"}, transient_fails=999)

    def always_retry(slug, result, context):
        return Decision("RETRY_STAGE", stage="script", reason="keep trying")

    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, decide_fn=always_retry,
                   max_in_flight=2, max_retries=999, max_decisions=3)
    slug = d.trigger(topic="loopy")["slug"]
    # Wait for the final failed state: decisions >= max_decisions (budget exhausted).
    proj = _wait_proj_cond(tmp_path, slug,
        lambda p, k: (p.get("status") == "failed"
                      and p.get("supervisor", {}).get("decisions", 0) >= 3),
        timeout=20, dispatcher=d)
    assert _wait_status(tmp_path, slug, "failed", timeout=5), _status(tmp_path, slug)
    assert proj["supervisor"]["decisions"] >= 3


def test_decision_is_logged_to_project_and_event_ring(tmp_path):
    """Every Atlas decision lands in project['supervisor']['log'] + history with initiator
    'atlas' (the audit plane the live feed + digest read in Slice 4)."""
    from supervisor import Decision
    fake, probe = make_fake_produce(outcomes={"script": "deterministic"})

    def decider(slug, result, context):
        return Decision("ESCALATE", stage="script", reason="needs a human",
                        payload={"failure_kind": "deterministic"})

    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, decide_fn=decider, max_retries=0)
    slug = d.trigger(topic="log-me")["slug"]
    # wait for the supervisor block to be written to disk (record_decision runs inside
    # _on_result, which runs AFTER produce() returns — disk may show "failed" before it)
    proj = _wait_proj_cond(tmp_path, slug,
        lambda p, _k: bool(p.get("supervisor", {}).get("log")),
        timeout=12, dispatcher=d)
    log = proj["supervisor"]["log"]
    assert log and log[-1]["kind"] == "ESCALATE" and log[-1]["reason"] == "needs a human"
    assert any(h.get("initiator") == "atlas" for h in proj["history"])


# ---------------------------------------------------------------- Task 6: APPROVE_GATE / RERUN_FROM / KILL
def test_approve_gate_factcheck_is_illegal_and_escalates(tmp_path):
    """HARD GUARANTEE: APPROVE_GATE(factcheck) is rejected by the EXECUTOR — a video that
    fails fact-check is never approved away."""
    from supervisor import Decision
    fake, probe = make_fake_produce(stages=("research", "script", "factcheck"),
                                    outcomes={"factcheck": "deterministic"})

    def approve_it(slug, result, context):
        return Decision("APPROVE_GATE", gate="factcheck", reason="looks fine to me")

    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, decide_fn=approve_it,
                   max_in_flight=2, max_retries=0)
    slug = d.trigger(topic="cannot-approve")["slug"]
    assert _wait_status(tmp_path, slug, "failed", timeout=12), _status(tmp_path, slug)
    # wait for the "blocked" event — _execute_decision runs after record_decision in
    # _on_result, which runs after produce() returns (disk may show "failed" first)
    _wait_proj_cond(tmp_path, slug,
        lambda _p, k: "blocked" in k, timeout=5, dispatcher=d)
    evs = [e for e in d.events.since(0) if e["kind"] == "blocked"]
    assert evs and evs[-1]["gate"] == "factcheck"     # escalated as a gate, NOT approved
    # the video never advanced past the gate
    assert _proj(tmp_path, slug)["status"] != "done"


def test_rerun_from_sends_video_back_to_earlier_stage(tmp_path):
    from supervisor import Decision
    fake, probe = make_fake_produce(outcomes={"render": "deterministic"})

    calls = {"n": 0}
    def back_to_research(slug, result, context):
        calls["n"] += 1
        if calls["n"] == 1:
            return Decision("RERUN_FROM", stage="research", reason="bad source upstream")
        return Decision("ESCALATE", stage=result.get("stage"),
                        payload={"failure_kind": "deterministic"})

    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, decide_fn=back_to_research,
                   max_in_flight=2, max_retries=0)
    slug = d.trigger(topic="rewind-me")["slug"]
    # Wait until research ran >=2 times AND video is finally parked as failed (second run done).
    proj = _wait_proj_cond(tmp_path, slug,
        lambda p, k: (p.get("status") == "failed"
                      and sum(1 for s, _k in probe["ran"] if s == slug and _k == "research") >= 2),
        timeout=15, dispatcher=d)
    assert proj.get("status") == "failed", _status(tmp_path, slug)
    # research ran at least twice (initial + the RERUN_FROM)
    assert sum(1 for s, _k in probe["ran"] if s == slug and _k == "research") >= 2


def test_kill_abandons_the_video(tmp_path):
    from supervisor import Decision
    fake, probe = make_fake_produce(outcomes={"script": "deterministic"})

    def kill_it(slug, result, context):
        return Decision("KILL", reason="topic is unworkable")

    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, decide_fn=kill_it,
                   max_in_flight=2, max_retries=0)
    slug = d.trigger(topic="doomed")["slug"]
    assert _wait_status(tmp_path, slug, "cancelled", timeout=12), _status(tmp_path, slug)
    kinds = [e["kind"] for e in d.events.since(0)]
    assert "killed" in kinds


# ---------------------------------------------------------------- Task 7: rich decision context
def _wait_for(pred, timeout=5.0):
    import time as _t
    end = _t.time() + timeout
    while _t.time() < end:
        if pred():
            return True
        _t.sleep(0.02)
    return False


def test_decider_receives_flagged_claims_in_context(tmp_path):
    """On a factcheck block, the context handed to the decider carries the flagged claims
    read off the factcheck report — so Atlas can name them in its fix instructions."""
    import json as _json
    from supervisor import Decision
    seen = {}

    def capture(slug, result, context):
        seen.update(context)
        return Decision("ESCALATE", gate="factcheck", payload={"blocked": True})

    # produce a factcheck block AND write a factcheck_report.json the dispatcher can read
    def fake(slug=None, approve=None, root=None, progress=None, station_locks=None,
             should_cancel=None):
        pdir = root / slug
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "factcheck_report.json").write_text(_json.dumps({
            "verdict": "block",
            "claims": [{"claim_id": "s5c2", "status": "flagged", "claim_text": "42% of X",
                        "note": "no source"}]}))
        proj = {"slug": slug, "status": "blocked_at_factcheck", "stages": {}, "history": []}
        (pdir / "project.json").write_text(_json.dumps(proj))
        return {"status": "blocked", "gate": "factcheck", "stage": "factcheck",
                "reason": "unverified", "details": {"flagged": [{"claim_id": "s5c2"}]}}

    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, decide_fn=capture, max_in_flight=2)
    slug = d.trigger(topic="claims-please")["slug"]
    # wait unconditionally for the decider to run — disk may already show blocked_at_factcheck
    # (set by the fake before produce() returns) while _on_result is still in flight
    assert _wait_for(lambda: "flagged_claims" in seen, timeout=12)
    assert any(c.get("claim_id") == "s5c2" for c in seen.get("flagged_claims", []))


# ---------------------------------------------------------------- Task 2: render-budget helpers
def _write_final_render_project(tmp_path, slug, est_runtime_sec, n_drafts=0):
    import json as _json
    pdir = tmp_path / slug
    pdir.mkdir(parents=True, exist_ok=True)
    proj = {"slug": slug, "status": "blocked_at_final_render", "stages": {},
            "history": [], "gates": {"final_render": {"status": "blocked", "details": {
                "working_title": "T", "scenes": 5, "est_runtime_sec": est_runtime_sec,
                "audio_duration_sec": est_runtime_sec}}}}
    (pdir / "project.json").write_text(_json.dumps(proj))
    for i in range(1, n_drafts + 1):
        d = pdir / "scenes" / f"scene-{i:02d}" / "renders"
        d.mkdir(parents=True, exist_ok=True)
        (d / "draft.mp4").write_text("x")
    return pdir


def test_render_under_budget_true_when_runtime_below_ceiling(tmp_path):
    fake, _ = make_fake_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, render_budget_sec=600.0)
    _write_final_render_project(tmp_path, "vid", est_runtime_sec=120)
    assert d._render_under_budget("vid") is True


def test_render_over_budget_when_runtime_above_ceiling(tmp_path):
    fake, _ = make_fake_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, render_budget_sec=300.0)
    _write_final_render_project(tmp_path, "vid", est_runtime_sec=900)
    assert d._render_under_budget("vid") is False


def test_render_missing_runtime_is_over_budget(tmp_path):
    import json as _json
    fake, _ = make_fake_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, render_budget_sec=600.0)
    pdir = tmp_path / "vid"; pdir.mkdir()
    (pdir / "project.json").write_text(_json.dumps(
        {"slug": "vid", "gates": {"final_render": {"details": {}}}}))
    assert d._render_under_budget("vid") is False     # cannot size → escalate


def test_render_plan_payload_includes_drafts(tmp_path):
    fake, _ = make_fake_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, render_budget_sec=600.0)
    _write_final_render_project(tmp_path, "vid", est_runtime_sec=120, n_drafts=3)
    payload = d._render_plan_payload("vid")
    assert payload["render_plan"]["scenes"] == 5
    assert payload["budget_sec"] == 600.0
    assert "scenes/scene-01/renders/draft.mp4" in payload["draft_renders"]
    assert len(payload["draft_renders"]) == 3


# ---------------------------------------------------------------- Task 3: render gate autonomous
def test_approve_render_under_budget_self_approves(tmp_path):
    """Under budget, APPROVE_GATE(render) is honored — Atlas resumes the gate itself."""
    from supervisor import Decision
    resumed = {}

    fake, probe = make_fake_produce(stages=("research", "render"),
                                    outcomes={"render": "blocked_final"})
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, render_budget_sec=600.0,
                   decide_fn=lambda s, r, c: Decision("APPROVE_GATE", gate="final_render",
                                                      reason="cheap render"))
    orig_resume = d.resume
    d.resume = lambda slug, gate, **kw: resumed.update({"slug": slug, "gate": gate}) or \
        orig_resume(slug, gate, **kw)
    slug = d.trigger(topic="cheap-render")["slug"]
    assert _wait_for(lambda: resumed.get("gate") == "final_render", timeout=12), resumed


def test_approve_render_over_budget_escalates_with_card(tmp_path):
    """Over budget, APPROVE_GATE(render) is converted to an escalation carrying the render
    plan + draft frames — the executor enforces the budget, not the LLM."""
    from supervisor import Decision
    fake, probe = make_fake_produce(stages=("research", "render"),
                                    outcomes={"render": "blocked_final"},
                                    final_runtime_sec=900)
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, render_budget_sec=300.0,
                   decide_fn=lambda s, r, c: Decision("APPROVE_GATE", gate="final_render",
                                                      reason="ship it"))
    slug = d.trigger(topic="expensive-render")["slug"]
    assert _wait_status(tmp_path, slug, "blocked_at_final_render", timeout=12) or \
        _wait_for(lambda: any(e["kind"] == "blocked" and e.get("gate") == "final_render"
                              for e in d.events.since(0)), timeout=8)
    # wait for the blocked event itself — _on_result runs after produce() returns
    assert _wait_for(lambda: any(e["kind"] == "blocked" and e.get("gate") == "final_render"
                                 for e in d.events.since(0)), timeout=8), \
        "blocked event with gate=final_render not emitted"
    ev = [e for e in d.events.since(0) if e["kind"] == "blocked"][-1]
    assert ev["gate"] == "final_render"
    assert "render_plan" in (ev.get("payload") or {})


def test_approve_factcheck_still_escalates_unchanged(tmp_path):
    """The render budget path must NOT weaken the factcheck prohibition."""
    from supervisor import Decision
    fake, probe = make_fake_produce(stages=("research", "script", "factcheck"),
                                    outcomes={"factcheck": "deterministic"})
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, render_budget_sec=600.0,
                   decide_fn=lambda s, r, c: Decision("APPROVE_GATE", gate="factcheck"),
                   max_retries=0)
    slug = d.trigger(topic="no-approve")["slug"]
    assert _wait_status(tmp_path, slug, "failed", timeout=12), _status(tmp_path, slug)
    # wait for the blocked event — _on_result runs after produce() returns in the worker
    _wait_proj_cond(tmp_path, slug, lambda _p, k: "blocked" in k, timeout=5, dispatcher=d)
    blocked = [e for e in d.events.since(0) if e["kind"] == "blocked"]
    assert blocked and blocked[-1]["gate"] == "factcheck"


# ---------------------------------------------------------------- Task 4: render-gate decision context
def test_build_context_includes_render_plan_for_render_gate(tmp_path):
    fake, _ = make_fake_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, render_budget_sec=450.0)
    _write_final_render_project(tmp_path, "vid", est_runtime_sec=200)
    ctx = d._build_context("vid", {"status": "blocked", "gate": "final_render"})
    assert ctx["render_plan"]["est_runtime_sec"] == 200
    assert ctx["render_budget_sec"] == 450.0


# ---------------------------------------------------------------- Task 2 (Slice 4): snapshots + guide + kill + atlas_activity
def test_fix_and_rerun_records_a_snapshot(tmp_path):
    """A fact-check FIX_AND_RERUN snapshots the flagged claims + instructions before re-run."""
    import json as _json
    from supervisor import Decision, fix_history

    def fake(slug=None, approve=None, root=None, progress=None, station_locks=None,
             should_cancel=None):
        pdir = root / slug; pdir.mkdir(parents=True, exist_ok=True)
        chat_state.atomic_write_json(pdir / "factcheck_report.json",
            {"verdict": "block", "claims": [
                {"claim_id": "s5c2", "status": "flagged", "claim_text": "42%"}]})
        # Preserve any existing supervisor block written by the dispatcher; only update status.
        existing = chat_state.load_json(pdir / "project.json", {})
        existing.update({"slug": slug, "status": "blocked_at_factcheck",
                         "stages": {}, "history": existing.get("history", [])})
        chat_state.atomic_write_json(pdir / "project.json", existing)
        return {"status": "blocked", "gate": "factcheck", "stage": "factcheck",
                "reason": "unverified"}

    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake,
                   decide_fn=lambda s, r, c: Decision("FIX_AND_RERUN", stage="script",
                       gate="factcheck", instructions="drop s5c2"), max_retries=0)
    slug = d.trigger(topic="snap-me")["slug"]
    assert _wait_for(lambda: bool(fix_history(_proj(tmp_path, slug), "factcheck")),
                     timeout=12), _proj(tmp_path, slug)
    hist = fix_history(_proj(tmp_path, slug), "factcheck")
    assert hist[0]["instructions"] == "drop s5c2"
    assert hist[0]["flagged_before"][0]["claim_id"] == "s5c2"


def test_guide_persists_hint_and_reruns(tmp_path):
    fake, probe = make_fake_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake)
    # seed a parked project
    pdir = tmp_path / "vid"; pdir.mkdir()
    import json as _json
    (pdir / "project.json").write_text(_json.dumps(
        {"slug": "vid", "status": "blocked_at_factcheck",
         "stages": {"script": {"status": "done"}, "factcheck": {"status": "blocked"}},
         "gates": {"factcheck": {"status": "blocked"}}, "history": []}))
    out = d.guide("vid", "tighten the stat in scene 5")
    assert out["guided"] is True
    proj = _proj(tmp_path, "vid")
    assert proj["revision"]["hint"] == "tighten the stat in scene 5"
    assert any(h.get("decision", "").startswith("guide") for h in proj["history"])


def test_kill_marks_cancelled_and_emits(tmp_path):
    fake, probe = make_fake_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake)
    import json as _json
    pdir = tmp_path / "vid"; pdir.mkdir()
    (pdir / "project.json").write_text(_json.dumps(
        {"slug": "vid", "status": "blocked_at_factcheck", "stages": {}, "history": []}))
    out = d.kill("vid", "unworkable topic")
    assert out["killed"] is True
    assert _proj(tmp_path, "vid")["status"] == "cancelled"
    assert any(e["kind"] == "killed" for e in d.events.since(0))


def test_atlas_activity_returns_latest_supervisor_line(tmp_path):
    from supervisor import record_decision
    fake, probe = make_fake_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake)
    import json as _json
    pdir = tmp_path / "vid"; pdir.mkdir()
    proj = {"slug": "vid", "history": []}
    record_decision(proj, trigger="blocked", stage="script", kind="FIX_AND_RERUN",
                    reason="fix 1/2")
    (pdir / "project.json").write_text(_json.dumps(proj))
    act = d._atlas_activity("vid")
    # The live line is humanized: the engine enum never leaks; the stage + reason do.
    assert act and "FIX_AND_RERUN" not in act["text"]
    assert "script" in act["text"] and "fix 1/2" in act["text"]
