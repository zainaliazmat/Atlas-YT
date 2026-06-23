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
from pipeline import PROJECTS_DIR, STAGES
from progress import Progress

# Belt states (mirrors project.json `status`, normalised for the UI / spec §6.1).
BELT_STATES = ("queued", "running", "blocked", "failed", "cancelled", "done")


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
                 max_retries: int = 1):
        self.projects_dir = pathlib.Path(projects_dir) if projects_dir else PROJECTS_DIR
        self._produce = produce_fn or pipeline.produce
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

    def resume(self, slug: str, gate: str, *, initiator: str = "ceo") -> dict:
        """Resume a video down the belt after a T2 gate approval, so the resumed run also
        respects station single-occupancy. The gate decision itself was made on the
        deterministic UI (the LLM plane never satisfies a gate — spec §4/§8)."""
        self.events.emit("gate_approved", slug=slug, message=gate, initiator=initiator)
        self._start_worker(slug, approve=[gate])
        return {"slug": slug, "resumed": True}

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
        if status == "failed":
            kind = result.get("failure_kind", "transient")
            attempts = self._retries.get(slug, 0)
            if kind == "transient" and attempts < self.max_retries:
                self._retries[slug] = attempts + 1
                self.events.emit("retry", slug=slug, stage=result.get("stage"),
                                 message=f"transient failure — retry {attempts + 1}")
                self._reset_failed_stage(slug, result.get("stage"))
                self._start_worker(slug, backoff=min(2.0 ** attempts, 5.0))
                return
            self._retries.pop(slug, None)
            self.events.emit("failed", slug=slug, stage=result.get("stage"),
                             failure_kind=kind,
                             message="; ".join(result.get("errors") or []) or "stage failed")
            return
        self._retries.pop(slug, None)
        if status == "blocked":
            self.events.emit("blocked", slug=slug, gate=result.get("gate"),
                             message=result.get("reason") or "awaiting your sign-off")
        elif status == "cancelled":
            self.events.emit("cancelled", slug=slug)
        elif status == "done":
            self.events.emit("done", slug=slug, message="video produced")

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
