# Atlas Supervisor — Slice 1 (Supervisor Seam) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce the `atlas_decide` supervisor seam in the dispatcher — every failure/gate decision routes through an injectable decider — with a **safe-default decider that reproduces today's behavior exactly** (zero behavior change).

**Architecture:** A new pure module `atlas/supervisor.py` defines the bounded `Decision` vocabulary and `safe_default_decider` (today's policy expressed as decisions, no LLM). `Dispatcher._on_result` is refactored: terminal outcomes (done/cancelled) emit as before; an exception (failed/blocked) becomes a **decision point** — the dispatcher asks the injected decider, then executes the returned `Decision` with its existing reliable mechanics (retry, park, escalate). Later slices swap in the real LLM decider behind the same seam.

**Tech Stack:** Python 3.12, pytest, stdlib `dataclasses`. No new dependencies.

## Global Constraints

- **Zero behavior change.** Every existing test in `atlas/tests/test_dispatcher.py` must pass **unchanged** — that is the proof the seam is behaviour-identical to today.
- **Decider is injectable** like `produce_fn`: `Dispatcher(..., decide_fn=None)`, default `supervisor.safe_default_decider`.
- **Exceptions-only invocation** (spec D1): a `done` / `cancelled` result never calls the decider; only `failed` / `blocked` do.
- **Decision runs outside the station lock** (spec §1): `_on_result` already runs after `pipeline.produce` returns (all station locks released), so this holds structurally — do not move the call back inside a lock.
- **Counters persist before acting** (spec §2): the decision counter is incremented + written to `project.json` before the chosen action runs.
- Run all commands from the `atlas/` directory. `atlas/` is on `sys.path` for tests (existing tests do `import pipeline`, `import chat_state`).
- No new dependencies.

---

### Task 1: The `supervisor` module — `Decision` + `safe_default_decider`

A pure, dependency-free module: the bounded decision vocabulary and the safe-default decider that reproduces the dispatcher's historical policy as `Decision` values. No dispatcher, no LLM — fully unit-testable in isolation.

**Files:**
- Create: `atlas/supervisor.py`
- Test: `atlas/tests/test_supervisor.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `DECISION_KINDS: tuple[str, ...]` = `("PROCEED","RETRY_STAGE","FIX_AND_RERUN","RERUN_FROM","APPROVE_GATE","ESCALATE","KILL")`
  - `Decision` — frozen dataclass: `kind: str`, `stage: str | None = None`, `gate: str | None = None`, `reason: str = ""`, `instructions: str = ""`, `payload: dict = {}`.
  - `safe_default_decider(slug: str, result: dict, context: dict) -> Decision` — `context` carries `{"attempts": int, "max_retries": int}`.

- [ ] **Step 1: Write the failing tests**

Create `atlas/tests/test_supervisor.py`:

```python
"""Unit tests for the supervisor decision seam (Slice 1).

`safe_default_decider` must reproduce the dispatcher's historical failure policy as
Decision values — proven here in isolation (no dispatcher, no LLM)."""
from supervisor import DECISION_KINDS, Decision, safe_default_decider


def _ctx(attempts=0, max_retries=1):
    return {"attempts": attempts, "max_retries": max_retries}


def test_transient_failure_with_budget_retries():
    d = safe_default_decider("s", {"status": "failed", "failure_kind": "transient",
                                   "stage": "script"}, _ctx(attempts=0, max_retries=1))
    assert d.kind == "RETRY_STAGE" and d.stage == "script"


def test_transient_failure_without_budget_escalates():
    d = safe_default_decider("s", {"status": "failed", "failure_kind": "transient",
                                   "stage": "script", "errors": ["boom"]},
                             _ctx(attempts=1, max_retries=1))
    assert d.kind == "ESCALATE" and d.stage == "script"
    assert d.payload.get("failure_kind") == "transient"
    assert "boom" in d.reason


def test_deterministic_failure_never_retries():
    d = safe_default_decider("s", {"status": "failed", "failure_kind": "deterministic",
                                   "stage": "compose"}, _ctx(attempts=0, max_retries=5))
    assert d.kind == "ESCALATE"
    assert d.payload.get("failure_kind") == "deterministic"


def test_blocked_gate_escalates_as_a_gate():
    d = safe_default_decider("s", {"status": "blocked", "gate": "factcheck",
                                   "reason": "awaiting"}, _ctx())
    assert d.kind == "ESCALATE" and d.gate == "factcheck"
    assert d.payload.get("blocked") is True


def test_non_exception_status_proceeds():
    assert safe_default_decider("s", {"status": "done"}, _ctx()).kind == "PROCEED"


def test_decision_kind_is_always_legal():
    for result in ({"status": "failed", "failure_kind": "transient"},
                   {"status": "failed", "failure_kind": "deterministic"},
                   {"status": "blocked"}, {"status": "done"}):
        assert safe_default_decider("s", result, _ctx()).kind in DECISION_KINDS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_supervisor.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'supervisor'`.

- [ ] **Step 3: Write the implementation**

Create `atlas/supervisor.py`:

```python
"""Atlas's decision seam — the supervisor brain's interface to the belt.

Slice 1 introduces the seam with ZERO behavior change: `safe_default_decider` reproduces
the dispatcher's historical failure policy exactly (a transient stage failure retries
while budget remains, else escalates; a human gate escalates = parks for sign-off). Later
slices swap in the LLM decider behind this same interface.

A Decision is a bounded instruction the dispatcher EXECUTES with its existing reliable
mechanics — the LLM (later) may propose ONLY from this legal set; it never touches the
belt directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# The full legal decision vocabulary (spec §1). Slice 1's executor handles RETRY_STAGE +
# ESCALATE (all the safe-default decider emits); later slices implement the rest.
DECISION_KINDS = ("PROCEED", "RETRY_STAGE", "FIX_AND_RERUN", "RERUN_FROM",
                  "APPROVE_GATE", "ESCALATE", "KILL")


@dataclass(frozen=True)
class Decision:
    """One bounded instruction returned by a decider. `kind` ∈ DECISION_KINDS."""
    kind: str
    stage: str | None = None
    gate: str | None = None
    reason: str = ""
    instructions: str = ""
    payload: dict = field(default_factory=dict)


def safe_default_decider(slug: str, result: dict, context: dict) -> Decision:
    """Today's dispatcher policy, expressed as a Decision (no LLM).

    `context` = {"attempts": int, "max_retries": int}. Reproduces historical behavior:
    - a TRANSIENT stage failure with retry budget left → RETRY_STAGE;
    - any other stage failure (transient exhausted or deterministic) → ESCALATE(failed),
      carrying the original failure_kind so the UI's retry-ability read is unchanged;
    - a human gate (blocked) → ESCALATE(gate) = park for sign-off;
    - anything else → PROCEED.
    """
    status = result.get("status")
    if status == "failed":
        kind = result.get("failure_kind", "transient")
        if kind == "transient" and \
                context.get("attempts", 0) < context.get("max_retries", 0):
            return Decision("RETRY_STAGE", stage=result.get("stage"),
                            reason="transient failure — retry")
        return Decision("ESCALATE", stage=result.get("stage"),
                        reason="; ".join(result.get("errors") or []) or "stage failed",
                        payload={"failure_kind": kind})
    if status == "blocked":
        return Decision("ESCALATE", gate=result.get("gate"),
                        reason=result.get("reason") or "awaiting your sign-off",
                        payload={"blocked": True})
    return Decision("PROCEED")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_supervisor.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add atlas/supervisor.py atlas/tests/test_supervisor.py
git commit -m "feat(control-room): supervisor decision seam — Decision + safe_default_decider"
```

---

### Task 2: Wire the decider into the dispatcher (the seam)

Inject `decide_fn` into `Dispatcher`, and refactor `_on_result` so an exception routes through the decider + a new `_execute_decision`. The safe default makes this behaviour-identical to today; an injected decider proves the seam is live.

**Files:**
- Modify: `atlas/dispatcher.py` (the `import` block, `Dispatcher.__init__` at `dispatcher.py:92-98`, and `_on_result` at `dispatcher.py:353-377`)
- Test: `atlas/tests/test_dispatcher.py` (append a new test)

**Interfaces:**
- Consumes: `supervisor.Decision`, `supervisor.safe_default_decider` (Task 1).
- Produces:
  - `Dispatcher(__init__)` gains `decide_fn: Callable | None = None`; sets `self._decide = decide_fn or supervisor.safe_default_decider`.
  - `Dispatcher._execute_decision(slug: str, result: dict, decision: supervisor.Decision) -> None`.

- [ ] **Step 1: Write the failing test**

Append to `atlas/tests/test_dispatcher.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_dispatcher.py::test_injected_decider_overrides_default_policy -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'decide_fn'`.

- [ ] **Step 3a: Add the supervisor import**

In `atlas/dispatcher.py`, add `import supervisor` alongside the existing module imports (next to `import pipeline`):

```python
import chat_state
import pipeline
import supervisor
from pipeline import PROJECTS_DIR, STAGES
from progress import Progress
```

- [ ] **Step 3b: Inject `decide_fn` in `__init__`**

In `atlas/dispatcher.py`, change the `__init__` signature + body (`dispatcher.py:92-98`):

```python
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
```

- [ ] **Step 3c: Refactor `_on_result` + add `_execute_decision`**

In `atlas/dispatcher.py`, replace the whole `_on_result` method (`dispatcher.py:353-377`) with:

```python
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
```

- [ ] **Step 4: Run the new test, then the FULL dispatcher suite (zero-behavior-change proof)**

Run: `python3 -m pytest tests/test_dispatcher.py -q`
Expected: PASS — the new test passes AND every pre-existing dispatcher test passes unchanged (this is the zero-behavior-change proof).

- [ ] **Step 5: Commit**

```bash
git add atlas/dispatcher.py atlas/tests/test_dispatcher.py
git commit -m "feat(control-room): route dispatcher failures through the supervisor seam (zero behavior change)"
```

---

### Task 3: Persist the decision counter (before acting)

Every executed decision increments a per-video counter written to `project.json` **before** the action runs — the persistence the bounded-autonomy budget will rely on in Slice 2, and a property that survives a crash mid-decision.

**Files:**
- Modify: `atlas/dispatcher.py` (add `_bump_decision_count`; call it at the top of `_execute_decision`)
- Test: `atlas/tests/test_dispatcher.py` (append a new test)

**Interfaces:**
- Consumes: `chat_state.load_json`, `chat_state.atomic_write_json`, `self._project_path` (existing).
- Produces: `Dispatcher._bump_decision_count(slug: str) -> int`. Adds a `project.json` key `supervisor: {"decisions": int}` (additive; nothing reads it yet).

- [ ] **Step 1: Write the failing test**

Append to `atlas/tests/test_dispatcher.py`:

```python
def test_each_decision_persists_a_counter(tmp_path):
    """Every executed decision bumps a persisted per-video counter (the budget Slice 2
    relies on). One transient failure → one RETRY_STAGE decision recorded."""
    fake, probe = make_fake_produce(outcomes={"script": "transient"}, transient_fails=1)
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, max_in_flight=2, max_retries=1)
    slug = d.trigger(topic="count-decisions")["slug"]
    assert _wait_status(tmp_path, slug, "done", timeout=12), _status(tmp_path, slug)
    proj = chat_state.load_json(tmp_path / slug / "project.json", {})
    assert proj.get("supervisor", {}).get("decisions", 0) >= 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_dispatcher.py::test_each_decision_persists_a_counter -v`
Expected: FAIL — `assert 0 >= 1` (no `supervisor.decisions` key written yet).

- [ ] **Step 3a: Add the `_bump_decision_count` helper**

In `atlas/dispatcher.py`, add this method in the helpers block (next to `_reset_failed_stage`):

```python
    def _bump_decision_count(self, slug: str) -> int:
        """Increment + persist this video's supervisor decision count (project.json).
        Counters persist BEFORE the action runs so a crash mid-decision cannot reset the
        budget and loop (spec §2). Additive: nothing reads `supervisor` until Slice 2."""
        p = self._project_path(slug)
        proj = chat_state.load_json(p, None)
        if not isinstance(proj, dict):
            return 0
        sup = proj.setdefault("supervisor", {})
        sup["decisions"] = int(sup.get("decisions", 0)) + 1
        proj["updated"] = time.time()
        chat_state.atomic_write_json(p, proj)
        return sup["decisions"]
```

- [ ] **Step 3b: Call it before acting in `_execute_decision`**

In `atlas/dispatcher.py`, add the bump as the FIRST line of `_execute_decision` (immediately after the docstring, before `kind = getattr(...)`):

```python
        self._bump_decision_count(slug)   # persist the counter BEFORE acting (spec §2)
        kind = getattr(decision, "kind", "ESCALATE")
```

- [ ] **Step 4: Run the new test, then the full dispatcher suite**

Run: `python3 -m pytest tests/test_dispatcher.py -q`
Expected: PASS (the counter test passes; all others still pass).

- [ ] **Step 5: Commit**

```bash
git add atlas/dispatcher.py atlas/tests/test_dispatcher.py
git commit -m "feat(control-room): persist supervisor decision counter before acting"
```

---

### Task 4: Full-suite regression gate

Prove the seam changed nothing across the whole project (the slice's headline guarantee).

**Files:** none (verification only).

- [ ] **Step 1: Run the full atlas + dashboard suites**

Run: `python3 -m pytest tests/ dashboard/tests/ -q -p no:cacheprovider`
Expected: PASS, except the one **pre-existing** flaky SSE test `dashboard/tests/test_belt_api.py::test_event_stream_backfills_then_stops_on_disconnect` (passes in isolation — unrelated cross-test async bleed; confirm with the isolation run below). No other failures.

- [ ] **Step 2: Confirm the lone failure is the known flaky test (not a regression)**

Run: `python3 -m pytest dashboard/tests/test_belt_api.py -q -p no:cacheprovider`
Expected: PASS (all of `test_belt_api.py` green in isolation → the full-suite failure is the known flake, not Slice 1).

- [ ] **Step 3: Done — report**

State: Slice 1 complete. The supervisor seam is live and injectable; the safe-default decider reproduces today's behavior (proven by the unchanged dispatcher suite); decision counters persist. Ready for Slice 2 (the real LLM decider + bounded fact-check auto-fix).

---

## Self-Review

**Spec coverage (Slice 1 scope):**
- `atlas_decide` seam replacing `_on_result` → Task 2. ✓
- Safe-default decider, behaviour-identical to today → Task 1 (logic) + Task 2 (proven by unchanged suite). ✓
- Exceptions-only invocation (done/cancelled bypass) → Task 2 `_on_result`. ✓
- Decider injectable like `produce_fn` → Task 2 `decide_fn`. ✓
- Decision runs outside the station lock → structurally preserved (Global Constraints) + Task 2 comment. ✓
- Persist decision/attempt counters before acting → Task 3. ✓
- Schema-validate / coerce illegal decision → Task 2 `_execute_decision` forward-safe fallback (full validation lands in Slice 2 with the LLM). ✓ (noted)

**Out of Slice 1 (deferred to later slices, correctly):** the real LLM decider, FIX_AND_RERUN/APPROVE_GATE/RERUN_FROM/KILL executor branches, the render budget, the escalation cards, the unified request path. Slice 1 is pure plumbing.

**Placeholder scan:** none — every step has exact code, exact commands, exact expected output.

**Type consistency:** `decide_fn`/`self._decide`, `Decision(kind, stage, gate, reason, instructions, payload)`, `_execute_decision(slug, result, decision)`, `_bump_decision_count(slug)` are consistent across Tasks 1–3 and match the dispatcher's existing helpers (`_reset_failed_stage`, `_start_worker`, `_project_path`).
