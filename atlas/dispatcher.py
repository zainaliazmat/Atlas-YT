"""The assembly-line dispatcher — the belt.

Drives MANY videos down the 10-stage line concurrently with TRUE station=stage
single-occupancy (one video per stage at a time), built ON TOP of `pipeline.produce()`
via its opt-in `station_locks` + `should_cancel` hooks. The spine's guarantees (order,
contract validation, the two gates, the un-approvable fact-check block, resume) are
untouched — this module only schedules.

Authoritative state is ALWAYS the on-disk `project.json` files: the belt is rebuildable by
scanning `projects/` (see `dashboard.data.belt`). This module holds only EPHEMERAL control
state — worker threads, per-slug cancel flags, the bounded event ring — all safe to lose on
a restart (spec §6.2). On restart the belt reconstructs from disk and any interrupted stage
simply re-runs (stages are idempotent / skipped when `done`).

Design (spec §6):
- §6.1 state machine: queued → running → (blocked@gate | failed@stage | cancelled | done).
  A blocked/parked video holds NO station (produce() returns at the gate, releasing locks).
- §6.3 station=stage: one `threading.Semaphore(1)` per stage = single-occupancy. A global
  `max_in_flight` semaphore caps concurrency; an over-cap video waits as `queued` (§6.6,
  max_in_flight=1 degenerates to "one active + a queue").
- §6.4 failure policy: a TRANSIENT failure (producer raised) is retried with bounded
  backoff; a DETERMINISTIC failure (contract / auto-gate) is NOT retried (it would loop) —
  classification comes from produce()'s `failure_kind`.
- §6.5 cancel: cooperative — a running video stops at the next station boundary; a
  parked/queued video is marked cancelled on disk immediately.
"""
from __future__ import annotations

import pathlib
import threading
import time
from collections import deque
from typing import Callable

import chat_state
import pipeline
import supervisor
from pipeline import PROJECTS_DIR, STAGES
from progress import Progress

# Belt states (mirrors project.json `status`, normalised for the UI / spec §6.1).
# `interrupted` = was mid-flight when the process stopped; parked on restart (no live
# worker) so the belt stays honest and the operator can Re-run it.
BELT_STATES = ("queued", "running", "blocked", "failed", "cancelled", "done",
               "interrupted")


def _belt_status(raw: str) -> str:
    """Normalise a project.json `status` to the ONE belt vocabulary (spec §6.1), matching
    dashboard.data._belt_state so the resumed-outcome status reads the same everywhere."""
    if raw.startswith("blocked_at_"):
        return "blocked"
    if raw == "created":
        return "queued"
    return raw or "queued"


class EventRing:
    """Bounded, monotonic-id event log for SSE with Last-Event-ID backfill (spec §10).

    Every event carries an `initiator` plane (chat / ceo / dispatcher) for the §4 audit
    property. Thread-safe; oldest events fall off past `maxlen`.
    """

    def __init__(self, maxlen: int = 1000):
        self._events: deque = deque(maxlen=maxlen)
        self._next_id = 1
        self._lock = threading.Lock()

    def emit(self, kind: str, *, slug: str | None = None, message: str = "",
             initiator: str = "dispatcher", **extra) -> dict:
        with self._lock:
            ev = {"id": self._next_id, "ts": time.time(), "kind": kind, "slug": slug,
                  "message": message, "initiator": initiator, **extra}
            self._next_id += 1
            self._events.append(ev)
            return ev

    def since(self, last_id: int) -> list[dict]:
        """Events with id > last_id (for a reconnecting tab's backfill)."""
        with self._lock:
            return [e for e in self._events if e["id"] > last_id]

    @property
    def last_id(self) -> int:
        with self._lock:
            return self._next_id - 1


class Dispatcher:
    """The belt. One instance per dashboard process; injectable for tests."""

    def __init__(self, projects_dir: pathlib.Path | str | None = None,
                 produce_fn: Callable | None = None, max_in_flight: int = 2,
                 max_retries: int = 1, decide_fn: Callable | None = None):
        self.projects_dir = pathlib.Path(projects_dir) if projects_dir else PROJECTS_DIR
        self._produce = produce_fn or pipeline.produce
        # the supervisor seam: every failure/gate decision routes through this. The default
        # reproduces the historical policy exactly (Slice 1); later slices inject the LLM.
        self._decide = decide_fn or supervisor.safe_default_decider
        self.max_in_flight = max_in_flight
        self.max_retries = max_retries
        # one single-occupancy station per stage (§6.3)
        self._station_locks = {s.key: threading.Semaphore(1) for s in STAGES}
        # global in-flight cap; over-cap videos wait as `queued` on disk (§6.6)
        self._inflight = threading.Semaphore(max_in_flight)
        self._cancel: set[str] = set()
        self._cancel_lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._running: set[str] = set()   # slugs that have ACQUIRED an in-flight slot
        self._threads_lock = threading.Lock()
        self._retries: dict[str, int] = {}
        self.events = EventRing()

    # ------------------------------------------------------------------ public API
    def trigger(self, brief: str | None = None, *, topic: str | None = None,
                length: str | None = None, niche: str | None = None,
                gates: bool = True, initiator: str = "ceo") -> dict:
        """Mint a queued project and start it down the belt. Returns {slug} IMMEDIATELY
        (reversible T1) so the UI can drop the card on the belt without waiting."""
        gate_cfg = None if gates else {pipeline.GATE_FACTCHECK: False,
                                       pipeline.GATE_FINAL_RENDER: False}
        summary = pipeline.create_project(brief, topic=topic, gates=gate_cfg,
                                          unattended=not gates, root=self.projects_dir)
        slug = summary["slug"]
        if length or niche:
            self._patch_config(slug, target_length=length, niche=niche)
        self.events.emit("triggered", slug=slug,
                         message=(topic or brief or "")[:120], initiator=initiator,
                         niche=niche, length=length)
        self._start_worker(slug)
        return {"slug": slug}

    def cancel(self, slug: str, *, initiator: str = "ceo") -> dict:
        """Request cancellation (spec §6.5). A running video stops at the next station
        boundary; a parked/queued video (no live worker) is marked cancelled at once."""
        with self._cancel_lock:
            self._cancel.add(slug)
        with self._threads_lock:
            live = slug in self._threads and self._threads[slug].is_alive()
        if not live:
            self._mark_cancelled(slug)
        self.events.emit("cancel_requested", slug=slug, initiator=initiator)
        return {"slug": slug, "cancelling": True}

    def retry(self, slug: str, *, initiator: str = "ceo") -> dict:
        """Operator-driven retry of a PARKED failed video (the Inspector's RETRY action, a
        reversible T1). Resets the parked failed/blocked stage on disk and restarts the
        worker so the spine re-runs from there. Honest about scope: this is the same
        mechanism the dispatcher uses for an auto-retry, just initiated by a human after the
        bounded auto-retries are spent. A no-op (retrying:False) for a slug with no parked
        failed stage — the UI only offers RETRY for a TRANSIENT failure (spec §6.4)."""
        proj = chat_state.load_json(self._project_path(slug), None)
        if not isinstance(proj, dict):
            return {"slug": slug, "retrying": False}
        stages = proj.get("stages", {}) or {}
        failed_key = next((k for k, st in stages.items()
                           if (st or {}).get("status") in ("failed", "blocked")), None)
        if failed_key is None and proj.get("status") not in ("failed",):
            return {"slug": slug, "retrying": False}
        with self._cancel_lock:
            self._cancel.discard(slug)        # a deliberate retry clears a stale cancel
        self._retries.pop(slug, None)         # a manual retry starts the auto-budget fresh
        self._reset_failed_stage(slug, failed_key)
        self.events.emit("retry", slug=slug, stage=failed_key,
                         message="operator retry", initiator=initiator)
        self._start_worker(slug)
        return {"slug": slug, "retrying": True, "stage": failed_key}

    def reconcile_interrupted(self, *, initiator: str = "dispatcher") -> list[str]:
        """Park videos left mid-flight by a stopped/crashed session (called once at server
        startup). The belt's in-flight state is EPHEMERAL (spec §6.2): a process that dies
        mid-stage leaves the on-disk status at `running`/`queued` with NO worker to advance
        it — a 'zombie' the belt would keep rendering as RUNNING forever, inflating the
        running count and contradicting the live in-flight semaphore.

        For each such video WITHOUT a live worker, reset any `running` stage to pending and
        demote the project to `interrupted` (a parked state the operator can Re-run). A
        settled video (done / failed / blocked / cancelled) is left untouched. Returns the
        reconciled slugs. Safe to call anytime: a video with a live worker is skipped."""
        reconciled: list[str] = []
        if not self.projects_dir.exists():
            return reconciled
        for d in sorted(self.projects_dir.iterdir()):
            proj = chat_state.load_json(d / "project.json", None)
            if not isinstance(proj, dict) or proj.get("status") not in ("running", "queued"):
                continue
            with self._threads_lock:
                if d.name in self._threads and self._threads[d.name].is_alive():
                    continue  # genuinely running — not a zombie
            for st in (proj.get("stages", {}) or {}).values():
                if isinstance(st, dict) and st.get("status") == "running":
                    st.update({"status": "pending", "artifact": None, "validated": False})
            proj["status"] = "interrupted"
            proj["updated"] = time.time()
            proj.setdefault("history", []).append(
                {"ts": time.time(), "stage": None,
                 "decision": "interrupted — session stopped mid-run; re-run when ready"})
            chat_state.atomic_write_json(d / "project.json", proj)
            slug = proj.get("slug") or d.name
            self.events.emit("interrupted", slug=slug, initiator=initiator,
                             message="parked after an interrupted session")
            reconciled.append(slug)
        return reconciled

    def rerun(self, slug: str, from_stage: str | None = None, *,
              initiator: str = "ceo") -> dict:
        """Re-run an existing video (the dashboard's Re-run button; a reversible T1).

        `from_stage=None` re-runs the WHOLE video from the start (every stage reset to
        pending). A stage key re-runs that stage AND everything downstream, keeping the
        upstream artifacts (`done`) so the spine skips them. Only a stage that has
        ALREADY RUN (status != pending) can be a `from_stage` — you cannot re-run a stage
        that never produced. Refuses to race a live worker (cancel first). Resets the
        chosen stages, re-earns the gates governed by a reset stage (so the fact-check /
        final-render gate fires again), requeues, and starts the worker down the belt.

        Returns {slug, rerunning, from_stage}; rerunning=False (with a `reason`) for an
        unknown slug, an unknown / never-run from_stage, or a still-running video."""
        proj = chat_state.load_json(self._project_path(slug), None)
        if not isinstance(proj, dict):
            return {"slug": slug, "rerunning": False, "reason": "no such project"}
        with self._threads_lock:
            live = slug in self._threads and self._threads[slug].is_alive()
        if live:
            return {"slug": slug, "rerunning": False, "reason": "still running"}

        stage_keys = [s.key for s in STAGES]
        stages = proj.get("stages", {}) or {}
        if from_stage is not None:
            if from_stage not in stage_keys:
                return {"slug": slug, "rerunning": False, "reason": "unknown stage"}
            if (stages.get(from_stage, {}) or {}).get("status", "pending") == "pending":
                return {"slug": slug, "rerunning": False, "reason": "stage has not run"}
            start = stage_keys.index(from_stage)
        else:
            start = 0
        reset_keys = stage_keys[start:]

        for k in reset_keys:
            proj.setdefault("stages", {})[k] = {"status": "pending", "artifact": None,
                                                "validated": False}
        # Re-earn the gates governed by a reset stage: the fact-check gate IS the
        # `factcheck` stage; the final-render gate fires just before the `render` stage.
        gate_stage = {pipeline.GATE_FACTCHECK: "factcheck",
                      pipeline.GATE_FINAL_RENDER: "render"}
        for gate, gov in gate_stage.items():
            if gov in reset_keys and gate in (proj.get("gates", {}) or {}):
                proj["gates"][gate] = {"status": "pending", "details": None}

        with self._cancel_lock:
            self._cancel.discard(slug)        # a re-run clears a stale cancel
        self._retries.pop(slug, None)         # a re-run starts the auto-budget fresh
        proj["status"] = "queued"
        proj["updated"] = time.time()
        proj.setdefault("history", []).append(
            {"ts": time.time(), "stage": from_stage,
             "decision": f"re-run from {from_stage or 'start'} by operator"})
        chat_state.atomic_write_json(self._project_path(slug), proj)
        self.events.emit("rerun", slug=slug, stage=from_stage,
                         message=f"re-run from {from_stage or 'start'}",
                         initiator=initiator)
        self._start_worker(slug)
        return {"slug": slug, "rerunning": True, "from_stage": from_stage}

    def resume(self, slug: str, gate: str, *, initiator: str = "ceo",
               wait: bool = False, timeout: float = 900.0) -> dict:
        """Resume a video down the belt after a T2 gate approval, so the resumed run also
        respects station single-occupancy (it acquires the same station locks + in-flight
        slot as a belt run). The gate decision itself was made on the deterministic UI —
        the LLM plane never satisfies a gate (spec §4/§8); `initiator` records that plane.

        `wait=True` joins the resumed worker and returns the spine's on-disk outcome
        (status / next gate / video), so the deterministic gate-approve surface can relay a
        synchronous result while STILL sharing the belt's locks. `wait=False` (default,
        used by chat-navigated or fire-and-forget callers) returns immediately and the UI
        learns the outcome over SSE."""
        self.events.emit("gate_approved", slug=slug, message=gate, initiator=initiator)
        self._start_worker(slug, approve=[gate])
        if not wait:
            return {"slug": slug, "resumed": True}
        with self._threads_lock:
            t = self._threads.get(slug)
        if t is not None:
            t.join(timeout)
        return {"slug": slug, "resumed": True, **self._disk_outcome(slug)}

    def _disk_outcome(self, slug: str) -> dict:
        """The spine's authoritative result for a just-finished run, read from disk (the
        belt's source of truth). Normalises status to the belt vocabulary and surfaces the
        next gate (if it re-paused) + the video path (if it finished)."""
        proj = chat_state.load_json(self._project_path(slug), None)
        if not isinstance(proj, dict):
            return {"status": None}
        raw = proj.get("status") or ""
        out: dict = {"status": _belt_status(raw)}
        if raw.startswith("blocked_at_"):
            out["gate"] = raw[len("blocked_at_"):]
        if (self.projects_dir / slug / "video.mp4").exists():
            out["video"] = "video.mp4"
        return out

    def live_state(self) -> dict:
        """Ephemeral control state the belt view layers over the on-disk truth: which
        slugs are actually executing a stage (have an in-flight slot), which threads exist
        but are still QUEUED behind the cap, and which are mid-cancel (not yet persisted)."""
        with self._threads_lock:
            running = sorted(self._running)
            queued = sorted(s for s, t in self._threads.items()
                            if t.is_alive() and s not in self._running)
        with self._cancel_lock:
            cancelling = sorted(self._cancel)
        return {"running": running, "queued": queued, "cancelling": cancelling,
                "max_in_flight": self.max_in_flight}

    # ------------------------------------------------------------------ workers
    def _start_worker(self, slug: str, approve: list[str] | None = None,
                      backoff: float = 0.0) -> None:
        with self._cancel_lock:
            if approve or backoff:
                pass  # a resume/retry keeps any pending cancel honoured
            else:
                self._cancel.discard(slug)  # a fresh trigger clears stale cancel
        t = threading.Thread(target=self._run, name=f"belt:{slug}",
                             args=(slug,), kwargs={"approve": approve, "backoff": backoff},
                             daemon=True)
        with self._threads_lock:
            self._threads[slug] = t
        t.start()

    def _run(self, slug: str, approve: list[str] | None = None,
             backoff: float = 0.0) -> None:
        if backoff:
            time.sleep(backoff)
        # over-cap videos wait HERE as `queued` on disk until a slot frees (§6.6)
        self._inflight.acquire()
        with self._threads_lock:
            self._running.add(slug)
        try:
            if self._is_cancelled(slug):
                self._mark_cancelled(slug)
                return
            progress = Progress(sink=lambda m: self.events.emit(
                "progress", slug=slug, message=m, initiator="dispatcher"))
            result = self._produce(
                slug=slug, approve=approve, root=self.projects_dir, progress=progress,
                station_locks=self._station_locks,
                should_cancel=lambda: self._is_cancelled(slug),
            ) or {}
            self._on_result(slug, result)
        finally:
            self._inflight.release()
            with self._threads_lock:
                self._running.discard(slug)
                self._threads.pop(slug, None)

    def _on_result(self, slug: str, result: dict) -> None:
        status = result.get("status")
        # Terminal outcomes need no judgment — emit as before (exceptions-only seam, D1).
        if status == "done":
            self._retries.pop(slug, None)
            self.events.emit("done", slug=slug, message="video produced")
            return
        if status == "cancelled":
            self._retries.pop(slug, None)
            self.events.emit("cancelled", slug=slug)
            return
        # An exception (failed / blocked) is a DECISION POINT. Ask the decider (the safe
        # default reproduces today's policy), then execute its Decision. This runs AFTER
        # produce() returned, i.e. outside every station lock (spec §1).
        context = {"attempts": self._retries.get(slug, 0),
                   "max_retries": self.max_retries}
        decision = self._decide(slug, result, context)
        self._execute_decision(slug, result, decision)

    def _execute_decision(self, slug: str, result: dict,
                          decision: "supervisor.Decision") -> None:
        """Execute a Decision with the belt's reliable mechanics. Slice 1 handles
        RETRY_STAGE and ESCALATE (all the safe-default decider emits); any other kind is
        coerced to a deterministic escalation (forward-safe until a later slice implements
        it)."""
        kind = getattr(decision, "kind", "ESCALATE")
        if kind == "PROCEED":
            return  # the decider judged the exception benign — do nothing (NOT a failure)
        if kind == "RETRY_STAGE":
            attempts = self._retries.get(slug, 0)
            self._retries[slug] = attempts + 1
            self.events.emit("retry", slug=slug, stage=decision.stage,
                             message=f"transient failure — retry {attempts + 1}")
            self._reset_failed_stage(slug, decision.stage)
            self._start_worker(slug, backoff=min(2.0 ** attempts, 5.0))
            return
        self._retries.pop(slug, None)
        payload = decision.payload or {}
        if kind == "ESCALATE" and (decision.gate or payload.get("blocked")):
            self.events.emit("blocked", slug=slug, gate=decision.gate,
                             message=decision.reason or "awaiting your sign-off")
            return
        if kind == "ESCALATE":
            self.events.emit("failed", slug=slug,
                             stage=decision.stage or result.get("stage"),
                             failure_kind=payload.get("failure_kind", "transient"),
                             message=decision.reason or "stage failed")
            return
        # A kind not implemented in this slice → forward-safe deterministic escalation.
        self.events.emit("failed", slug=slug, stage=result.get("stage"),
                         failure_kind="deterministic",
                         message=f"decision {kind!r} not handled in this slice; escalating")

    # ------------------------------------------------------------------ helpers
    def _is_cancelled(self, slug: str) -> bool:
        with self._cancel_lock:
            return slug in self._cancel

    def _project_path(self, slug: str) -> pathlib.Path:
        return self.projects_dir / slug / "project.json"

    def _mark_cancelled(self, slug: str) -> None:
        p = self._project_path(slug)
        proj = chat_state.load_json(p, None)
        if isinstance(proj, dict) and proj.get("status") not in ("done", "cancelled"):
            proj["status"] = "cancelled"
            proj.setdefault("history", []).append(
                {"ts": time.time(), "stage": None, "decision": "cancelled by operator"})
            proj["updated"] = time.time()
            chat_state.atomic_write_json(p, proj)
        self.events.emit("cancelled", slug=slug)

    def _reset_failed_stage(self, slug: str, stage_key: str | None) -> None:
        if not stage_key:
            return
        p = self._project_path(slug)
        proj = chat_state.load_json(p, None)
        if isinstance(proj, dict) and stage_key in proj.get("stages", {}):
            proj["stages"][stage_key] = {"status": "pending", "artifact": None,
                                         "validated": False}
            proj["status"] = "queued"
            proj["updated"] = time.time()
            chat_state.atomic_write_json(p, proj)

    def _patch_config(self, slug: str, **kv) -> None:
        p = self._project_path(slug)
        proj = chat_state.load_json(p, None)
        if isinstance(proj, dict):
            cfg = proj.setdefault("config", {})
            for k, v in kv.items():
                if v is not None:
                    cfg[k] = v
            proj["updated"] = time.time()
            chat_state.atomic_write_json(p, proj)
