# Atlas Supervisor — Slice 2 (Real Atlas Decisions + Fact-Check Auto-Fix) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the safe-default decider with a real LLM decider behind the Slice-1 seam, and implement the bounded **fact-check auto-fix** (`FIX_AND_RERUN` → Marlow revises the flagged claims → re-run, capped at 2, never ships a block), with schema-validated decisions, persisted per-gate counters, the in-flight-slot release before the (slow) LLM call, and observability.

**Architecture:** `supervisor.py` (pure) gains decision **validation** + **persisted counter transforms**. A new `atlas/atlas_decider.py` builds the **LLM decider** (`make_llm_decider(chat_fn=…)`) — injectable like `produce_fn`, so tests never hit a live LLM. The dispatcher's `_execute_decision` grows the real branches (`FIX_AND_RERUN`, `RERUN_FROM`, `KILL`, gate-scoped `APPROVE_GATE`), enforces the hard guarantees **in the executor** (never approve a factcheck block; cap fix attempts; per-video decision budget), and `_run` releases the in-flight slot **before** deciding. Marlow gets a revision-hint seam so a fix actually changes the script.

**Tech Stack:** Python 3.12, pytest, stdlib `dataclasses`/`json`. Reuses `atlas/llm.py` (`chat`), `atlas/registry.py`, `atlas/chat_state.py`. No new dependencies.

## Global Constraints

- **Hard guarantee — never ship an unverified video.** `APPROVE_GATE(factcheck)` is **illegal in the executor** (gate-scoped), not just discouraged: the only legal moves on a factcheck block are `FIX_AND_RERUN` / `ESCALATE` / `KILL`. This is executor logic, not vocabulary.
- **Bounded auto-fix.** A per-gate fix-attempt counter caps factcheck `FIX_AND_RERUN` at **2** (`max_fix_attempts=2`, configurable); the 3rd block forces `ESCALATE` regardless of the LLM's choice.
- **Per-video decision budget.** A cap on total belt-re-running Atlas actions per video (`max_decisions=12`, configurable); on reaching it Atlas escalates rather than spinning.
- **Counters persist before acting.** The per-gate attempt count + decision count live in `project.json` and are incremented + written to disk **before** the chosen action runs, so a crash mid-decision cannot reset the budget and loop.
- **Render never auto-approves in Slice 2.** The render budget rule is Slice 3. Until then `APPROVE_GATE(render)` is **not** honored — the executor converts it to `ESCALATE` (the safe default). Only `APPROVE_GATE(factcheck)` has the permanent prohibition; render's is temporary-until-Slice-3.
- **Decider is injectable** like `produce_fn`: `Dispatcher(…, decide_fn=…)`, and the dashboard builds the real one via `app.state.decide_fn` (tests inject a fake → the real LLM never runs under test).
- **LLM/decider failure degrades to today's behavior.** Any exception from the LLM call falls back to `supervisor.safe_default_decider` (transient→retry/escalate, block→escalate). An outage degrades to *current* behavior, never to unsafe behavior.
- **Malformed/illegal decision → `ESCALATE`.** A decision whose `kind` is not in `DECISION_KINDS`, or that is missing a field its kind requires, is coerced to `ESCALATE` (schema validation), never executed blindly.
- **Release the in-flight slot before the LLM decision.** `_on_result` (and the decider call) must run **after** `_run` releases the global in-flight semaphore, so a slow decision does not throttle `max_in_flight`. Proven by a concurrency test.
- **Decider model:** `claude-opus-4-8` (`atlas_decider.DECIDER_MODEL`).
- **Zero regression to Slice 1.** Every existing `atlas/tests/test_supervisor.py` and `atlas/tests/test_dispatcher.py` test must still pass; the safe-default decider path is unchanged.
- Run all commands from the `atlas/` directory. Use the venv python: `/home/zain-ali/Documents/YT-AGENTS/venv/bin/python3 -m pytest …` (fall back to `python3`). `atlas/` is on `sys.path`.
- Every commit message ends with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## File Structure

- `atlas/supervisor.py` (modify) — add `decision_from_dict`, `validate_decision` (pure schema legality) and the persisted-counter transforms (`ensure_supervisor_block`, `bump_decision`, `bump_fix_attempt`, `fix_attempts`, `decisions_count`, `record_decision`). Stays pure: dict transforms only, no I/O.
- `atlas/atlas_decider.py` (create) — the LLM decider: `DECIDER_MODEL`, `build_decision_prompt`, `make_llm_decider(chat_fn=…)`. Imports `supervisor` + `llm`.
- `atlas/llm.py` (modify) — add an optional `model` kwarg to `chat`, threaded into the Claude provider (default unchanged).
- `atlas/dispatcher.py` (modify) — `_run` releases in-flight before deciding; `__init__` gains `decide_fn`, `max_fix_attempts`, `max_decisions`; `_build_context`; `_execute_decision` real branches; `_persist_revision_hint`; Atlas emits carry `initiator="atlas"` + decision logging.
- `atlas/adapters/scriptwriter.py` (modify) — `run_write` reads a persisted revision hint from `project.json` and folds it into the brief passed to `write_script`.
- `atlas/dashboard/app.py` (modify) — `_get_dispatcher` wires `decide_fn` from `app.state.decide_fn` (default = the real LLM decider).
- Tests: `atlas/tests/test_supervisor.py`, `atlas/tests/test_atlas_decider.py` (create), `atlas/tests/test_dispatcher.py`, `atlas/tests/test_scriptwriter_revision.py` (create).

---

### Task 1: Decision parsing + schema validation (pure)

`supervisor.py` gains the two pure functions the LLM decider and executor need to turn an untrusted dict into a *legal* `Decision`, coercing anything malformed/illegal to `ESCALATE`.

**Files:**
- Modify: `atlas/supervisor.py`
- Test: `atlas/tests/test_supervisor.py` (append)

**Interfaces:**
- Consumes: `DECISION_KINDS`, `Decision` (Slice 1).
- Produces:
  - `decision_from_dict(d: dict) -> Decision | None` — build a `Decision` from a parsed dict (reads `kind`, `stage`, `gate`, `reason`, `instructions`, `payload`); returns `None` if `d` is not a dict or has no string `kind`.
  - `LEGAL_GATES: tuple[str, ...]` = `("factcheck", "final_render")`.
  - `validate_decision(decision: Decision) -> Decision` — returns `decision` unchanged if legal, else a coerced `Decision("ESCALATE", reason="illegal decision: …", payload={"illegal_kind": …})`. Legality rules: `kind ∈ DECISION_KINDS`; `RETRY_STAGE`/`RERUN_FROM`/`FIX_AND_RERUN` require a non-empty `stage`; `APPROVE_GATE` requires `gate ∈ LEGAL_GATES`; `PROCEED`/`ESCALATE`/`KILL` always legal.

- [ ] **Step 1: Write the failing tests**

Append to `atlas/tests/test_supervisor.py`:

```python
from supervisor import decision_from_dict, validate_decision, LEGAL_GATES


def test_decision_from_dict_round_trips_fields():
    d = decision_from_dict({"kind": "FIX_AND_RERUN", "stage": "script",
                            "gate": "factcheck", "instructions": "fix s5c2",
                            "reason": "claim unsupported", "payload": {"x": 1}})
    assert d.kind == "FIX_AND_RERUN" and d.stage == "script" and d.gate == "factcheck"
    assert d.instructions == "fix s5c2" and d.payload == {"x": 1}


def test_decision_from_dict_rejects_non_dict_and_missing_kind():
    assert decision_from_dict("nope") is None
    assert decision_from_dict({"stage": "script"}) is None


def test_validate_passes_a_legal_decision():
    d = Decision("FIX_AND_RERUN", stage="script", gate="factcheck")
    assert validate_decision(d) is d


def test_validate_coerces_unknown_kind_to_escalate():
    d = validate_decision(Decision("DELETE_EVERYTHING"))
    assert d.kind == "ESCALATE" and "illegal" in d.reason.lower()
    assert d.payload.get("illegal_kind") == "DELETE_EVERYTHING"


def test_validate_requires_stage_for_rerun_kinds():
    for kind in ("RETRY_STAGE", "RERUN_FROM", "FIX_AND_RERUN"):
        assert validate_decision(Decision(kind)).kind == "ESCALATE"
        assert validate_decision(Decision(kind, stage="script")).kind == kind


def test_validate_requires_real_gate_for_approve():
    assert validate_decision(Decision("APPROVE_GATE")).kind == "ESCALATE"
    assert validate_decision(Decision("APPROVE_GATE", gate="bogus")).kind == "ESCALATE"
    assert validate_decision(Decision("APPROVE_GATE", gate="factcheck")).kind == "APPROVE_GATE"
    assert tuple(LEGAL_GATES) == ("factcheck", "final_render")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_supervisor.py -k "decision_from_dict or validate" -q`
Expected: FAIL with `ImportError: cannot import name 'decision_from_dict'`.

- [ ] **Step 3: Write the implementation**

Append to `atlas/supervisor.py`:

```python
LEGAL_GATES = ("factcheck", "final_render")
_STAGE_REQUIRED = ("RETRY_STAGE", "RERUN_FROM", "FIX_AND_RERUN")


def decision_from_dict(d) -> "Decision | None":
    """Build a Decision from an untrusted parsed dict (the LLM's JSON). Returns None when
    `d` is not a dict or carries no string `kind` — the caller treats None as malformed."""
    if not isinstance(d, dict):
        return None
    kind = d.get("kind")
    if not isinstance(kind, str) or not kind:
        return None
    payload = d.get("payload")
    return Decision(
        kind=kind,
        stage=d.get("stage"),
        gate=d.get("gate"),
        reason=d.get("reason") or "",
        instructions=d.get("instructions") or "",
        payload=payload if isinstance(payload, dict) else {},
    )


def validate_decision(decision: "Decision") -> "Decision":
    """Coerce an illegal/malformed Decision to ESCALATE (schema legality only — the
    factcheck-approve prohibition + budget caps are EXECUTOR logic, not here). Returns the
    original object unchanged when legal, so callers can identity-check in tests."""
    kind = getattr(decision, "kind", None)
    if kind not in DECISION_KINDS:
        return Decision("ESCALATE", reason=f"illegal decision kind {kind!r}",
                        payload={"illegal_kind": kind})
    if kind in _STAGE_REQUIRED and not decision.stage:
        return Decision("ESCALATE", reason=f"illegal {kind}: missing stage",
                        payload={"illegal_kind": kind})
    if kind == "APPROVE_GATE" and decision.gate not in LEGAL_GATES:
        return Decision("ESCALATE", reason=f"illegal APPROVE_GATE: gate {decision.gate!r}",
                        payload={"illegal_kind": kind})
    return decision
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_supervisor.py -q`
Expected: PASS (all prior Slice-1 supervisor tests + the 6 new ones).

- [ ] **Step 5: Commit**

```bash
git add atlas/supervisor.py atlas/tests/test_supervisor.py
git commit -m "feat(control-room): decision parsing + schema validation (illegal -> ESCALATE)"
```

---

### Task 2: Persisted supervisor counters (pure transforms)

`supervisor.py` gains pure dict transforms over a `project["supervisor"]` block — the decision count, the per-gate fix-attempt count, and a decision log. The dispatcher (Task 5/8) loads/saves `project.json` around these; here they are pure and unit-tested in isolation.

**Files:**
- Modify: `atlas/supervisor.py`
- Test: `atlas/tests/test_supervisor.py` (append)

**Interfaces:**
- Produces (all operate on a `project: dict` in place, returning the new count where noted):
  - `ensure_supervisor_block(project: dict) -> dict` — sets `project["supervisor"] = {"decisions": 0, "fix_attempts": {}, "log": []}` if absent; returns the block.
  - `bump_decision(project: dict) -> int` — `+1` to `supervisor.decisions`; returns new total.
  - `decisions_count(project: dict) -> int`.
  - `bump_fix_attempt(project: dict, gate: str) -> int` — `+1` to `supervisor.fix_attempts[gate]`; returns new count.
  - `fix_attempts(project: dict, gate: str) -> int`.
  - `record_decision(project: dict, *, trigger: str, stage, kind: str, reason: str = "", latency_ms: int | None = None, model: str | None = None) -> dict` — appends one entry to `supervisor.log` **and** to `project["history"]` (so both Atlas call-shapes read one source of truth); returns the appended log entry.

- [ ] **Step 1: Write the failing tests**

Append to `atlas/tests/test_supervisor.py`:

```python
from supervisor import (ensure_supervisor_block, bump_decision, decisions_count,
                        bump_fix_attempt, fix_attempts, record_decision)


def test_ensure_block_is_idempotent():
    p = {}
    b = ensure_supervisor_block(p)
    assert b == {"decisions": 0, "fix_attempts": {}, "log": []}
    b["decisions"] = 5
    assert ensure_supervisor_block(p)["decisions"] == 5  # does not clobber


def test_bump_decision_counts_up():
    p = {}
    assert bump_decision(p) == 1 and bump_decision(p) == 2
    assert decisions_count(p) == 2


def test_fix_attempts_are_per_gate():
    p = {}
    assert bump_fix_attempt(p, "factcheck") == 1
    assert bump_fix_attempt(p, "factcheck") == 2
    assert bump_fix_attempt(p, "final_render") == 1
    assert fix_attempts(p, "factcheck") == 2 and fix_attempts(p, "final_render") == 1
    assert fix_attempts(p, "never") == 0


def test_record_decision_appends_to_log_and_history():
    p = {"history": []}
    entry = record_decision(p, trigger="blocked", stage="script", kind="FIX_AND_RERUN",
                            reason="fix s5c2", latency_ms=1200, model="claude-opus-4-8")
    assert entry["kind"] == "FIX_AND_RERUN" and entry["latency_ms"] == 1200
    assert p["supervisor"]["log"][-1]["kind"] == "FIX_AND_RERUN"
    assert p["history"][-1]["decision"].startswith("atlas: FIX_AND_RERUN")
    assert p["history"][-1].get("initiator") == "atlas"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_supervisor.py -k "ensure_block or bump_decision or fix_attempts or record_decision" -q`
Expected: FAIL with `ImportError: cannot import name 'ensure_supervisor_block'`.

- [ ] **Step 3: Write the implementation**

Append to `atlas/supervisor.py` (add `import time` to the top of the file if not present):

```python
def ensure_supervisor_block(project: dict) -> dict:
    """Return project['supervisor'], creating the zero-state block if absent (idempotent)."""
    blk = project.get("supervisor")
    if not isinstance(blk, dict):
        blk = {"decisions": 0, "fix_attempts": {}, "log": []}
        project["supervisor"] = blk
    blk.setdefault("decisions", 0)
    blk.setdefault("fix_attempts", {})
    blk.setdefault("log", [])
    return blk


def bump_decision(project: dict) -> int:
    blk = ensure_supervisor_block(project)
    blk["decisions"] += 1
    return blk["decisions"]


def decisions_count(project: dict) -> int:
    return ensure_supervisor_block(project)["decisions"]


def bump_fix_attempt(project: dict, gate: str) -> int:
    blk = ensure_supervisor_block(project)
    blk["fix_attempts"][gate] = blk["fix_attempts"].get(gate, 0) + 1
    return blk["fix_attempts"][gate]


def fix_attempts(project: dict, gate: str) -> int:
    return ensure_supervisor_block(project)["fix_attempts"].get(gate, 0)


def record_decision(project: dict, *, trigger: str, stage, kind: str, reason: str = "",
                    latency_ms: int | None = None, model: str | None = None) -> dict:
    """Append the decision to BOTH supervisor.log (rich) and project.history (the shared
    audit feed both Atlas call-shapes read). Returns the log entry."""
    blk = ensure_supervisor_block(project)
    entry = {"ts": time.time(), "trigger": trigger, "stage": stage, "kind": kind,
             "reason": reason, "latency_ms": latency_ms, "model": model}
    blk["log"].append(entry)
    project.setdefault("history", []).append(
        {"ts": entry["ts"], "stage": stage, "initiator": "atlas",
         "decision": f"atlas: {kind}", "why": reason})
    return entry
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_supervisor.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add atlas/supervisor.py atlas/tests/test_supervisor.py
git commit -m "feat(control-room): persisted supervisor counters + decision log (single source of truth)"
```

---

### Task 3: The LLM decider — `atlas_decider.make_llm_decider`

A new pure-seam module: builds the decision prompt from the context, calls an **injectable** `chat_fn`, parses the JSON reply, validates it (Task 1), and falls back to the safe default on any exception. Tests inject a fake `chat_fn` — no live LLM.

**Files:**
- Create: `atlas/atlas_decider.py`
- Modify: `atlas/llm.py` (add `model` kwarg to `chat`)
- Test: `atlas/tests/test_atlas_decider.py` (create)

**Interfaces:**
- Consumes: `supervisor.decision_from_dict`, `supervisor.validate_decision`, `supervisor.safe_default_decider`, `supervisor.Decision`; `llm.chat`.
- Produces:
  - `DECIDER_MODEL = "claude-opus-4-8"`.
  - `build_decision_prompt(slug: str, result: dict, context: dict) -> tuple[str, str]` — `(system, user)`. `user` is a compact JSON-ish brief of the decision state (status, stage, gate, errors, flagged claims, counters, recent history).
  - `make_llm_decider(chat_fn=None, *, model: str = DECIDER_MODEL, safe_default=supervisor.safe_default_decider) -> Callable[[str, dict, dict], Decision]` — `chat_fn(system, user) -> str`; defaults to `lambda s, u: llm.chat(s, u, model=model)`.

- [ ] **Step 1: Write the failing tests**

Create `atlas/tests/test_atlas_decider.py`:

```python
"""The LLM decider seam: injected chat_fn, JSON → validated Decision, safe fallback."""
import json
import atlas_decider
from supervisor import Decision


def _result_block():
    return {"status": "blocked", "gate": "factcheck", "stage": "factcheck",
            "reason": "unverified claims"}


def _ctx():
    return {"attempts": 0, "max_retries": 1, "fix_attempts": {"factcheck": 0},
            "decisions": 0, "flagged_claims": [{"claim_id": "s5c2", "claim_text": "X",
            "status": "flagged", "note": "no source"}], "history": []}


def test_decider_parses_a_legal_json_decision():
    def chat_fn(system, user):
        return json.dumps({"kind": "FIX_AND_RERUN", "stage": "script",
                           "gate": "factcheck", "instructions": "drop s5c2 or source it",
                           "reason": "claim unsupported"})
    decide = atlas_decider.make_llm_decider(chat_fn=chat_fn)
    d = decide("vid", _result_block(), _ctx())
    assert d.kind == "FIX_AND_RERUN" and d.stage == "script"
    assert "s5c2" in d.instructions


def test_decider_extracts_json_from_chatty_reply():
    def chat_fn(system, user):
        return "Sure! Here is my call:\n```json\n{\"kind\": \"ESCALATE\", \"reason\": \"unfixable\"}\n```\nThanks"
    decide = atlas_decider.make_llm_decider(chat_fn=chat_fn)
    assert decide("vid", _result_block(), _ctx()).kind == "ESCALATE"


def test_decider_coerces_illegal_decision_to_escalate():
    def chat_fn(system, user):
        return json.dumps({"kind": "APPROVE_GATE", "gate": "factcheck"})  # legal schema...
    # schema-legal, but the EXECUTOR (not the decider) bans factcheck-approve; the decider
    # returns it as-is and the dispatcher rejects it. Here we only assert it parses.
    d = atlas_decider.make_llm_decider(chat_fn=chat_fn)("vid", _result_block(), _ctx())
    assert d.kind == "APPROVE_GATE" and d.gate == "factcheck"


def test_decider_coerces_unknown_kind_to_escalate():
    def chat_fn(system, user):
        return json.dumps({"kind": "NUKE"})
    assert atlas_decider.make_llm_decider(chat_fn=chat_fn)("vid", _result_block(), _ctx()).kind == "ESCALATE"


def test_decider_falls_back_to_safe_default_on_chat_error():
    def chat_fn(system, user):
        raise RuntimeError("LLM down")
    decide = atlas_decider.make_llm_decider(chat_fn=chat_fn)
    # safe default on a transient stage failure with budget → RETRY_STAGE (today's policy)
    res = {"status": "failed", "failure_kind": "transient", "stage": "script"}
    assert decide("vid", res, {"attempts": 0, "max_retries": 1}).kind == "RETRY_STAGE"


def test_decider_escalates_on_unparseable_reply():
    def chat_fn(system, user):
        return "I am not going to give you JSON, sorry."
    assert atlas_decider.make_llm_decider(chat_fn=chat_fn)("vid", _result_block(), _ctx()).kind == "ESCALATE"


def test_build_prompt_includes_flagged_claims_and_counters():
    system, user = atlas_decider.build_decision_prompt("vid", _result_block(), _ctx())
    assert "factcheck" in user and "s5c2" in user
    assert "PROCEED" in system and "FIX_AND_RERUN" in system  # the legal vocabulary
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_atlas_decider.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'atlas_decider'`.

- [ ] **Step 3a: Add the `model` kwarg to `llm.chat`**

In `atlas/llm.py`, change the `chat` signature and thread `model` into the Claude provider. The provider helper `_chat_claude`/`_claude_chat_async` currently reads `CLAUDE_MODEL`; add an optional `model` that defaults to it. Concretely:

- Change `def chat(system: str, user: str) -> str:` to
  `def chat(system: str, user: str, *, model: str | None = None) -> str:`.
- Where `chat` dispatches to the provider, pass `model` through; for the Claude provider use `model or CLAUDE_MODEL` when constructing `ClaudeAgentOptions(model=…)`. For non-Claude providers `model` may be ignored (they have their own model constants) — that is acceptable; document it with a one-line comment.

> The decider always passes `model=DECIDER_MODEL`; under test the fake `chat_fn` ignores it, so the live model string is never exercised in CI. Keep the default-`None` behavior byte-identical to today for every existing caller.

- [ ] **Step 3b: Write `atlas/atlas_decider.py`**

```python
"""Atlas's single-shot decision call — the LLM behind the dispatcher's `decide_fn` seam.

`make_llm_decider(chat_fn=…)` returns a `(slug, result, context) -> Decision` callable with
the SAME signature as `supervisor.safe_default_decider`, so it drops straight into
`Dispatcher(decide_fn=…)`. The LLM only PROPOSES — `supervisor.validate_decision` clamps the
reply to the legal vocabulary, and the dispatcher's executor enforces the hard guarantees
(never approve a factcheck block, cap fix attempts, budget). Any chat error degrades to the
safe-default decider = today's deterministic policy.
"""
from __future__ import annotations

import json
from typing import Callable

import llm
import supervisor
from supervisor import Decision

DECIDER_MODEL = "claude-opus-4-8"

_SYSTEM = """You are Atlas, the autonomous supervisor of a YouTube video production belt.
A stage just FAILED or a gate is BLOCKED. Decide the single best next move. You may ONLY
return one decision from this exact vocabulary (JSON object, no prose):

  PROCEED                              — the exception is benign; continue down the belt
  RETRY_STAGE   {stage}               — re-run a station after a transient hiccup
  FIX_AND_RERUN {stage, instructions} — delegate a fix to the specialist, then re-run from
                                        that station. For a FACT-CHECK block: stage="script",
                                        instructions = concrete edits so Marlow re-grounds or
                                        drops the flagged claims.
  RERUN_FROM    {stage}               — send the video back to an earlier station
  APPROVE_GATE  {gate}                — self-approve a gate (NEVER legal for factcheck)
  ESCALATE      {reason}              — hand the decision to the CEO
  KILL          {reason}              — abandon a genuinely unworkable video

Reply with ONE JSON object: {"kind": "...", "stage": "...", "gate": "...",
"instructions": "...", "reason": "..."} — include only the fields the kind needs.
NEVER approve a fact-check block: a video that fails fact-check must never ship. If you
cannot fix it within the attempts left, ESCALATE.
"""


def build_decision_prompt(slug: str, result: dict, context: dict) -> tuple[str, str]:
    """Compact, fully-specified decision brief — underspecified input is where an LLM
    hallucinates, so hand it the failing stage, contract errors, flagged claims, the
    attempt counters, and recent history."""
    brief = {
        "slug": slug,
        "status": result.get("status"),
        "stage": result.get("stage"),
        "gate": result.get("gate"),
        "errors": result.get("errors") or [],
        "reason": result.get("reason") or "",
        "flagged_claims": context.get("flagged_claims") or [],
        "counters": {
            "transient_attempts": context.get("attempts", 0),
            "max_retries": context.get("max_retries", 0),
            "fix_attempts": context.get("fix_attempts") or {},
            "decisions_so_far": context.get("decisions", 0),
        },
        "recent_history": (context.get("history") or [])[-6:],
    }
    user = "DECIDE on this exception:\n" + json.dumps(brief, indent=2, default=str)
    return _SYSTEM, user


def _extract_json(text: str):
    """Pull the first JSON object out of a possibly chatty reply. Returns a dict or None."""
    if not isinstance(text, str):
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def make_llm_decider(chat_fn: Callable[[str, str], str] | None = None, *,
                     model: str = DECIDER_MODEL,
                     safe_default=supervisor.safe_default_decider):
    """Build a decider callable for `Dispatcher(decide_fn=…)`."""
    chat_fn = chat_fn or (lambda system, user: llm.chat(system, user, model=model))

    def decide(slug: str, result: dict, context: dict) -> Decision:
        system, user = build_decision_prompt(slug, result, context)
        try:
            reply = chat_fn(system, user)
        except Exception as exc:  # noqa: BLE001 — any LLM failure degrades to today's policy
            return safe_default(slug, result, context)
        parsed = _extract_json(reply)
        decision = supervisor.decision_from_dict(parsed)
        if decision is None:
            return Decision("ESCALATE", gate=result.get("gate"), stage=result.get("stage"),
                            reason="Atlas could not produce a valid decision",
                            payload={"blocked": bool(result.get("gate"))})
        return supervisor.validate_decision(decision)

    return decide
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_atlas_decider.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add atlas/atlas_decider.py atlas/llm.py atlas/tests/test_atlas_decider.py
git commit -m "feat(control-room): LLM decider seam (injectable chat_fn, JSON->validated Decision, safe fallback)"
```

---

### Task 4: Release the in-flight slot before the decision (+ concurrency proof)

Restructure `Dispatcher._run` so `_on_result` (and therefore the LLM decision) runs **after** the global in-flight semaphore is released, so a slow decision cannot throttle the belt.

**Files:**
- Modify: `atlas/dispatcher.py` (`_run`)
- Test: `atlas/tests/test_dispatcher.py` (append)

**Interfaces:**
- Consumes: existing `_run`, `_on_result`, `_inflight`.
- Produces: no signature change; `_on_result(slug, result)` is invoked after the `finally` that releases `_inflight`.

- [ ] **Step 1: Write the failing test**

Append to `atlas/tests/test_dispatcher.py`:

```python
def test_slow_decision_does_not_hold_an_inflight_slot(tmp_path):
    """A slow decider for video A must NOT throttle the belt: with max_in_flight=1, video B
    still completes while A's decision sleeps — proving _on_result runs AFTER the in-flight
    slot is released (spec §1, Slice 2)."""
    import threading, time as _t
    from supervisor import Decision

    release_b = threading.Event()

    def slow_decider(slug, result, context):
        if slug.startswith("aaa"):
            release_b.wait(timeout=10)          # A's decision blocks for a while
            return Decision("ESCALATE", stage=result.get("stage"),
                            payload={"failure_kind": "deterministic"})
        return Decision("PROCEED")

    # A fails (→ enters the slow decision); B is a clean run.
    fake, _ = make_fake_produce(outcomes={"script": "deterministic"})
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, decide_fn=slow_decider,
                   max_in_flight=1, max_retries=0)
    a = d.trigger(topic="aaa-slow")["slug"]
    _t.sleep(0.3)                                # let A reach its decision
    b = d.trigger(topic="bbb-fast")["slug"]
    # B must finish even though A's decision is still sleeping and holds the only slot count.
    assert _wait_status(tmp_path, b, "done", timeout=12), _status(tmp_path, b)
    release_b.set()
    assert _wait_status(tmp_path, a, "failed", timeout=12), _status(tmp_path, a)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_dispatcher.py -k slow_decision -q`
Expected: FAIL (B times out at `done`) — today `_on_result` runs while A still holds the only in-flight slot, so B never starts.

- [ ] **Step 3: Restructure `_run`**

In `atlas/dispatcher.py`, replace the body of `_run` (locate by name) so the decision runs outside the in-flight slot:

```python
    def _run(self, slug: str, approve: list[str] | None = None,
             backoff: float = 0.0) -> None:
        if backoff:
            time.sleep(backoff)
        # over-cap videos wait HERE as `queued` on disk until a slot frees (§6.6)
        self._inflight.acquire()
        with self._threads_lock:
            self._running.add(slug)
        result = None
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
        finally:
            self._inflight.release()
            with self._threads_lock:
                self._running.discard(slug)
                self._threads.pop(slug, None)
        # The decision (an LLM call in Slice 2) runs AFTER the in-flight slot is released, so
        # a slow decision never throttles max_in_flight (spec §1). A produce() exception
        # re-raises through the finally and skips this — same as before.
        if result is not None:
            self._on_result(slug, result)
```

- [ ] **Step 4: Run the new test, then the full dispatcher suite**

Run: `python3 -m pytest tests/test_dispatcher.py -q`
Expected: PASS — `slow_decision` passes AND every pre-existing dispatcher test still passes (the decision-after-release move is behaviour-identical for the instant safe-default decider).

- [ ] **Step 5: Commit**

```bash
git add atlas/dispatcher.py atlas/tests/test_dispatcher.py
git commit -m "feat(control-room): run the supervisor decision outside the in-flight slot"
```

---

### Task 5: Executor — `FIX_AND_RERUN`, the factcheck cap, the decision budget, persisted counters

The heart of the slice: the dispatcher executes `FIX_AND_RERUN` by persisting a revision hint + bumping the per-gate counter (before acting), re-running from the stage; it caps factcheck fixes at 2 and enforces a per-video decision budget — both in the executor, regardless of what the decider returns.

**Files:**
- Modify: `atlas/dispatcher.py` (`__init__`, `_execute_decision`, add `_load_project`/`_save_project`/`_persist_revision_hint`)
- Test: `atlas/tests/test_dispatcher.py` (append)

**Interfaces:**
- Consumes: `supervisor.bump_fix_attempt`, `supervisor.fix_attempts`, `supervisor.bump_decision`, `supervisor.decisions_count`; existing `rerun`, `_reset_failed_stage`, `_start_worker`, `_project_path`, `chat_state`.
- Produces:
  - `Dispatcher.__init__` gains `max_fix_attempts: int = 2`, `max_decisions: int = 12`.
  - `_load_project(slug) -> dict | None`, `_save_project(slug, proj) -> None`.
  - `_persist_revision_hint(slug: str, stage: str, instructions: str) -> None` — writes `proj["revision"] = {"stage", "hint", "ts"}`.
  - `_execute_decision` handles `FIX_AND_RERUN` and enforces caps.

- [ ] **Step 1: Write the failing tests**

Append to `atlas/tests/test_dispatcher.py`:

```python
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
    assert _wait_status(tmp_path, slug, "failed", timeout=12), _status(tmp_path, slug)
    proj = _status(tmp_path, slug)
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
    assert _wait_status(tmp_path, slug, "failed", timeout=20), _status(tmp_path, slug)
    proj = _status(tmp_path, slug)
    assert proj["supervisor"]["fix_attempts"]["factcheck"] == 2   # capped at 2 fixes
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
    assert _wait_status(tmp_path, slug, "failed", timeout=20), _status(tmp_path, slug)
    assert _status(tmp_path, slug)["supervisor"]["decisions"] >= 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_dispatcher.py -k "fix_and_rerun or factcheck_fix_capped or decision_budget" -q`
Expected: FAIL — `__init__` rejects `max_fix_attempts`/`max_decisions` (TypeError), and the executor coerces `FIX_AND_RERUN` to the not-handled `failed` path.

- [ ] **Step 3a: Extend `__init__`**

In `atlas/dispatcher.py`, add the two caps to the signature + body (keep all existing params/defaults; append the new keyword-args last):

```python
    def __init__(self, projects_dir=None, produce_fn=None, max_in_flight=2,
                 max_retries=1, decide_fn=None, max_fix_attempts=2, max_decisions=12):
        ...                                  # existing body unchanged
        self.max_fix_attempts = max_fix_attempts
        self.max_decisions = max_decisions
```

- [ ] **Step 3b: Add project I/O + hint helpers**

Add near `_project_path` in `atlas/dispatcher.py`:

```python
    def _load_project(self, slug: str) -> dict | None:
        proj = chat_state.load_json(self._project_path(slug), None)
        return proj if isinstance(proj, dict) else None

    def _save_project(self, slug: str, proj: dict) -> None:
        proj["updated"] = time.time()
        chat_state.atomic_write_json(self._project_path(slug), proj)

    def _persist_revision_hint(self, slug: str, stage: str, instructions: str) -> None:
        """Record Atlas's fix instructions so the re-run of `stage` picks them up (Marlow
        reads project['revision'] in adapters/scriptwriter.run_write)."""
        proj = self._load_project(slug)
        if proj is None:
            return
        proj["revision"] = {"stage": stage, "hint": instructions or "", "ts": time.time()}
        self._save_project(slug, proj)
```

- [ ] **Step 3c: Implement the `FIX_AND_RERUN` branch + caps in `_execute_decision`**

In `_execute_decision`, insert the `FIX_AND_RERUN` handling and the budget guard **before** the ESCALATE fallthrough. Replace the method's body from the `RETRY_STAGE` block onward with:

```python
        if kind == "RETRY_STAGE":
            attempts = self._retries.get(slug, 0)
            self._retries[slug] = attempts + 1
            if self._over_decision_budget(slug):
                return self._escalate(slug, result, decision,
                                      reason="decision budget exhausted — escalating")
            self.events.emit("retry", slug=slug, stage=decision.stage, initiator="atlas",
                             message=f"transient failure — retry {attempts + 1}")
            self._reset_failed_stage(slug, decision.stage)
            self._start_worker(slug, backoff=min(2.0 ** attempts, 5.0))
            return
        if kind == "FIX_AND_RERUN":
            return self._do_fix_and_rerun(slug, result, decision)
        # (RERUN_FROM / APPROVE_GATE / KILL land in Task 6; until then they fall through to
        # the safe ESCALATE handling below.)
        self._retries.pop(slug, None)
        return self._escalate(slug, result, decision)
```

Then add the helper methods (note `_escalate` centralizes the gate-vs-failed emit that Slice 1 inlined — keep its behavior identical):

```python
    def _over_decision_budget(self, slug: str) -> bool:
        """Count this decision; True once the per-video budget is exhausted (counter
        persisted BEFORE the action runs, so a crash can't reset the budget and loop)."""
        proj = self._load_project(slug)
        if proj is None:
            return False
        n = supervisor.bump_decision(proj)
        self._save_project(slug, proj)
        return n > self.max_decisions

    def _do_fix_and_rerun(self, slug: str, result: dict,
                          decision: "supervisor.Decision") -> None:
        gate = decision.gate or result.get("gate")
        proj = self._load_project(slug)
        if proj is None:
            return self._escalate(slug, result, decision)
        # HARD GUARANTEE: a factcheck block can be fixed at most `max_fix_attempts` times;
        # the next block escalates (never approved, never looping). Counter persists first.
        if gate == "factcheck" and supervisor.fix_attempts(proj, gate) >= self.max_fix_attempts:
            return self._escalate(slug, result,
                supervisor.Decision("ESCALATE", gate="factcheck", payload={"blocked": True},
                    reason=decision.reason or "fact-check unresolved after auto-fix"))
        if gate:
            supervisor.bump_fix_attempt(proj, gate)
        n = supervisor.bump_decision(proj)
        self._save_project(slug, proj)
        if n > self.max_decisions:
            return self._escalate(slug, result, decision,
                                  reason="decision budget exhausted — escalating")
        attempt_no = supervisor.fix_attempts(proj, gate) if gate else 0
        self._persist_revision_hint(slug, decision.stage, decision.instructions)
        self.events.emit("fixing", slug=slug, stage=decision.stage, initiator="atlas",
                         message=(f"re-running {decision.stage} "
                                  f"(fix {attempt_no}/{self.max_fix_attempts})"
                                  if gate == "factcheck"
                                  else f"re-running {decision.stage}"))
        self._retries.pop(slug, None)
        self.rerun(slug, from_stage=decision.stage, initiator="atlas")

    def _escalate(self, slug: str, result: dict, decision: "supervisor.Decision",
                  *, reason: str | None = None) -> None:
        """Emit the park-for-human event (gate → blocked, else failed). Hardened gate
        detection: only emit `blocked` for a REAL gate key (Task 6 reuses this)."""
        self._retries.pop(slug, None)
        payload = decision.payload or {}
        why = reason or decision.reason
        gate = decision.gate if decision.gate in supervisor.LEGAL_GATES else None
        if gate or payload.get("blocked"):
            self.events.emit("blocked", slug=slug, gate=gate, initiator="atlas",
                             message=why or "awaiting your sign-off")
            return
        self.events.emit("failed", slug=slug,
                         stage=decision.stage or result.get("stage"), initiator="atlas",
                         failure_kind=payload.get("failure_kind", "transient"),
                         message=why or "stage failed")
```

> **Note for the implementer:** the Slice-1 `_execute_decision` had inline `ESCALATE`/`PROCEED`/fallthrough branches. Keep `PROCEED` as the early no-op `return`. Route every escalation through the new `_escalate` helper so gate-detection hardening lives in one place. The existing Slice-1 dispatcher tests (`test_injected_decider_overrides_default_policy`, the blocked-gate path, etc.) must still pass — run them.

- [ ] **Step 4: Run the new tests, then the full dispatcher suite**

Run: `python3 -m pytest tests/test_dispatcher.py -q`
Expected: PASS — the three new tests AND every pre-existing dispatcher test (Slice 1 behavior preserved through `_escalate`).

- [ ] **Step 5: Commit**

```bash
git add atlas/dispatcher.py atlas/tests/test_dispatcher.py
git commit -m "feat(control-room): FIX_AND_RERUN executor + factcheck cap + per-video decision budget"
```

---

### Task 6: Executor — gate-scoped `APPROVE_GATE`, `RERUN_FROM`, `KILL`

Complete the executor vocabulary with the remaining branches and the **permanent** factcheck-approve prohibition (the never-ship-unverified guarantee, in executor logic).

**Files:**
- Modify: `atlas/dispatcher.py` (`_execute_decision`)
- Test: `atlas/tests/test_dispatcher.py` (append)

**Interfaces:**
- Consumes: `supervisor.LEGAL_GATES`, existing `rerun`, `_mark_cancelled`, `_escalate` (Task 5).
- Produces: `_execute_decision` handles `RERUN_FROM`, `KILL`, `APPROVE_GATE` (factcheck → escalate; render → escalate in Slice 2).

- [ ] **Step 1: Write the failing tests**

Append to `atlas/tests/test_dispatcher.py`:

```python
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
    evs = [e for e in d.events.since(0) if e["kind"] == "blocked"]
    assert evs and evs[-1]["gate"] == "factcheck"     # escalated as a gate, NOT approved
    # the video never advanced past the gate
    assert _status(tmp_path, slug)["status"] != "done"


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
    assert _wait_status(tmp_path, slug, "failed", timeout=15), _status(tmp_path, slug)
    # research ran at least twice (initial + the RERUN_FROM)
    assert sum(1 for s, _k in probe["ran"] if s == "research") >= 2


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_dispatcher.py -k "approve_gate_factcheck or rerun_from or kill_abandons" -q`
Expected: FAIL — these kinds currently fall through to `_escalate` as `failed` (or `APPROVE_GATE` is not specially handled).

- [ ] **Step 3: Add the branches**

In `_execute_decision`, replace the Task-5 comment line `# (RERUN_FROM / APPROVE_GATE / KILL land in Task 6 …)` and the trailing fallthrough with explicit branches (placed after the `FIX_AND_RERUN` branch, before the final ESCALATE fallthrough):

```python
        if kind == "RERUN_FROM":
            if self._over_decision_budget(slug):
                return self._escalate(slug, result, decision,
                                      reason="decision budget exhausted — escalating")
            self.events.emit("rerunning", slug=slug, stage=decision.stage, initiator="atlas",
                             message=f"sending back to {decision.stage}")
            self._retries.pop(slug, None)
            self.rerun(slug, from_stage=decision.stage, initiator="atlas")
            return
        if kind == "APPROVE_GATE":
            # HARD GUARANTEE: never approve a fact-check block. Render auto-approval is
            # gated on the budget rule (Slice 3); until then it also escalates. So in
            # Slice 2 every APPROVE_GATE escalates the gate for a human.
            return self._escalate(slug, result,
                supervisor.Decision("ESCALATE", gate=decision.gate, payload={"blocked": True},
                    reason=(decision.reason or "gate needs your sign-off")))
        if kind == "KILL":
            self._retries.pop(slug, None)
            proj = self._load_project(slug)
            if proj is not None:
                proj.setdefault("history", []).append(
                    {"ts": time.time(), "stage": result.get("stage"), "initiator": "atlas",
                     "decision": "killed by Atlas", "why": decision.reason})
                self._save_project(slug, proj)
            self._mark_cancelled(slug)
            self.events.emit("killed", slug=slug, initiator="atlas",
                             message=decision.reason or "abandoned by Atlas")
            return
        self._retries.pop(slug, None)
        return self._escalate(slug, result, decision)
```

> Verify `_mark_cancelled(slug)` exists and sets the on-disk status to `cancelled` (it is used by `cancel()`); the KILL test asserts the `cancelled` status. If `_mark_cancelled` does not persist the status, persist it in the KILL branch before emitting.

- [ ] **Step 4: Run the new tests, then the full dispatcher suite**

Run: `python3 -m pytest tests/test_dispatcher.py -q`
Expected: PASS — all three new tests AND every pre-existing dispatcher test.

- [ ] **Step 5: Commit**

```bash
git add atlas/dispatcher.py atlas/tests/test_dispatcher.py
git commit -m "feat(control-room): gate-scoped APPROVE_GATE (factcheck illegal) + RERUN_FROM + KILL"
```

---

### Task 7: Rich decision context + Marlow revision-hint plumbing

Two halves that make the fix *real*: (a) the dispatcher builds a rich `context` for the decider (flagged claims from the factcheck report, contract errors, counters, history) so it can write good fix instructions; (b) Marlow's `run_write` reads the persisted revision hint so the re-run actually changes the script.

**Files:**
- Modify: `atlas/dispatcher.py` (`_on_result` → `_build_context`)
- Modify: `atlas/adapters/scriptwriter.py` (`run_write`)
- Test: `atlas/tests/test_dispatcher.py` (append) + `atlas/tests/test_scriptwriter_revision.py` (create)

**Interfaces:**
- Consumes: factcheck report on disk (`{slug}/factcheck_report.json`), `project.json`, `supervisor.fix_attempts`/`decisions_count`.
- Produces:
  - `Dispatcher._build_context(slug: str, result: dict) -> dict` — `{attempts, max_retries, fix_attempts: {gate: n}, decisions, flagged_claims: [...], history: [...]}`; `_on_result` passes this to `self._decide`.
  - `scriptwriter.run_write(pdir)` folds `project["revision"]["hint"]` into the brief as `brief["revision_hint"]` before calling `write_script(brief)`.

- [ ] **Step 1a: Write the failing dispatcher context test**

Append to `atlas/tests/test_dispatcher.py`:

```python
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
    assert _wait_status(tmp_path, slug, "blocked_at_factcheck", timeout=12) or \
        _wait_for(lambda: "flagged_claims" in seen, timeout=5)
    assert any(c.get("claim_id") == "s5c2" for c in seen.get("flagged_claims", []))
```

> If a `_wait_for` helper does not already exist in the test module, add a tiny one:
> ```python
> def _wait_for(pred, timeout=5.0):
>     import time as _t
>     end = _t.time() + timeout
>     while _t.time() < end:
>         if pred():
>             return True
>         _t.sleep(0.02)
>     return False
> ```

- [ ] **Step 1b: Write the failing scriptwriter test**

Create `atlas/tests/test_scriptwriter_revision.py`:

```python
"""Marlow folds Atlas's revision hint into the brief on a fix re-run."""
import json
import pathlib
import chat_state
from adapters import scriptwriter


def test_run_write_folds_revision_hint_into_brief(tmp_path, monkeypatch):
    pdir = tmp_path / "vid"
    pdir.mkdir()
    chat_state.atomic_write_json(pdir / "research_brief.json", {"topic": "T", "sources": []})
    chat_state.atomic_write_json(pdir / "project.json",
                                 {"revision": {"stage": "script", "hint": "drop s5c2"}})

    captured = {}
    class FakeEngine:
        def write_script(self, brief):
            captured["brief"] = brief
            return {"scenes": []}
    monkeypatch.setattr(scriptwriter, "_script_engine", lambda: FakeEngine())

    scriptwriter.run_write(pdir)
    assert captured["brief"].get("revision_hint") == "drop s5c2"


def test_run_write_omits_hint_when_absent(tmp_path, monkeypatch):
    pdir = tmp_path / "vid"
    pdir.mkdir()
    chat_state.atomic_write_json(pdir / "research_brief.json", {"topic": "T"})
    chat_state.atomic_write_json(pdir / "project.json", {})

    captured = {}
    class FakeEngine:
        def write_script(self, brief):
            captured["brief"] = brief
            return {"scenes": []}
    monkeypatch.setattr(scriptwriter, "_script_engine", lambda: FakeEngine())

    scriptwriter.run_write(pdir)
    assert "revision_hint" not in captured["brief"]
```

- [ ] **Step 2: Run both tests to verify they fail**

Run: `python3 -m pytest tests/test_scriptwriter_revision.py tests/test_dispatcher.py -k "revision or flagged_claims" -q`
Expected: FAIL — `run_write` does not add `revision_hint`; `_build_context` does not exist yet.

- [ ] **Step 3a: Build the rich context in the dispatcher**

In `atlas/dispatcher.py`, add `_build_context` and call it from `_on_result`. Replace the context line in `_on_result` (currently `context = {"attempts": …, "max_retries": …}`) with `context = self._build_context(slug, result)`, and add:

```python
    def _build_context(self, slug: str, result: dict) -> dict:
        """Full decision state for the decider (spec §1): counters + the flagged claims and
        contract errors that let Atlas write specific fix instructions instead of guessing."""
        ctx = {"attempts": self._retries.get(slug, 0), "max_retries": self.max_retries,
               "fix_attempts": {}, "decisions": 0, "flagged_claims": [], "history": []}
        proj = self._load_project(slug)
        if proj is not None:
            ctx["fix_attempts"] = supervisor.ensure_supervisor_block(proj)["fix_attempts"]
            ctx["decisions"] = supervisor.decisions_count(proj)
            ctx["history"] = (proj.get("history") or [])[-6:]
        if result.get("gate") == "factcheck":
            report = chat_state.load_json(
                self._project_path(slug).parent / "factcheck_report.json", {})
            ctx["flagged_claims"] = [c for c in (report.get("claims") or [])
                                     if c.get("status") in ("flagged", "unverifiable")]
        return ctx
```

> `_build_context` calls `ensure_supervisor_block`, which mutates the in-memory `proj` it just loaded but does NOT write it back — that is fine (read-only on disk); the counters are persisted by the executor when it acts.

- [ ] **Step 3b: Fold the hint into the brief in Marlow**

In `atlas/adapters/scriptwriter.py`, change `run_write` so it reads the hint and folds it in:

```python
def run_write(pdir: pathlib.Path) -> dict:
    from contracts import CONTRACT_VERSION
    pdir = pathlib.Path(pdir)
    brief = chat_state.load_json(pdir / "research_brief.json", {})
    # Atlas's fix re-run leaves a revision hint in project.json; fold it into the brief so
    # Marlow re-grounds/drops the flagged claims instead of regenerating the same script.
    revision = chat_state.load_json(pdir / "project.json", {}).get("revision") or {}
    hint = revision.get("hint")
    if hint:
        brief = {**brief, "revision_hint": hint}
    script = _script_engine().write_script(brief)
    script = {"schema_version": CONTRACT_VERSION, **script}
    chat_state.atomic_write_json(pdir / "script.json", script)
    return script
```

- [ ] **Step 4: Run both test files, then the dispatcher suite**

Run: `python3 -m pytest tests/test_scriptwriter_revision.py tests/test_dispatcher.py -q`
Expected: PASS (both new files + all dispatcher tests).

- [ ] **Step 5: Commit**

```bash
git add atlas/dispatcher.py atlas/adapters/scriptwriter.py atlas/tests/test_scriptwriter_revision.py atlas/tests/test_dispatcher.py
git commit -m "feat(control-room): rich decision context (flagged claims) + Marlow revision-hint plumbing"
```

---

### Task 8: Observability logging + wire the real decider + regression gate

Log each decision (kind, rationale, latency) to the event ring + `project.json`, wire the real LLM decider into the dashboard (injectable so tests stay offline), and prove the whole suite is green.

**Files:**
- Modify: `atlas/dispatcher.py` (`_on_result` — time + record the decision)
- Modify: `atlas/dashboard/app.py` (`_get_dispatcher` — pass `decide_fn`)
- Test: `atlas/tests/test_dispatcher.py` (append); verification run

**Interfaces:**
- Consumes: `supervisor.record_decision`; `atlas_decider.make_llm_decider`.
- Produces: `_on_result` wraps the decision in timing + `record_decision`; `app.state.decide_fn` (default = real decider) flows into `Dispatcher(decide_fn=…)`.

- [ ] **Step 1: Write the failing test**

Append to `atlas/tests/test_dispatcher.py`:

```python
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
    assert _wait_status(tmp_path, slug, "failed", timeout=12), _status(tmp_path, slug)
    proj = _status(tmp_path, slug)
    log = proj["supervisor"]["log"]
    assert log and log[-1]["kind"] == "ESCALATE" and log[-1]["reason"] == "needs a human"
    assert any(h.get("initiator") == "atlas" for h in proj["history"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_dispatcher.py -k decision_is_logged -q`
Expected: FAIL — `_on_result` does not yet record the decision to `project["supervisor"]["log"]`.

- [ ] **Step 3a: Time + record the decision in `_on_result`**

In `atlas/dispatcher.py`, wrap the decision call in `_on_result` so it times the decider and records the chosen decision (after building context, before executing):

```python
        context = self._build_context(slug, result)
        t0 = time.time()
        decision = self._decide(slug, result, context)
        latency_ms = int((time.time() - t0) * 1000)
        proj = self._load_project(slug)
        if proj is not None:
            supervisor.record_decision(
                proj, trigger=status, stage=result.get("stage"), kind=decision.kind,
                reason=decision.reason, latency_ms=latency_ms,
                model=getattr(self, "decider_model", None))
            self._save_project(slug, proj)
        self.events.emit("decision", slug=slug, initiator="atlas", kind=decision.kind,
                         stage=result.get("stage"), latency_ms=latency_ms,
                         message=f"Atlas decided {decision.kind}")
        self._execute_decision(slug, result, decision)
```

> Add `self.decider_model = getattr(decide_fn, "model", None)` is NOT available (the decider is a closure). Instead, set `self.decider_model = atlas_decider.DECIDER_MODEL if decide_fn is None else None` is fragile — keep it simple: add an optional `__init__` param `decider_model: str | None = None`, store `self.decider_model = decider_model`, and the dashboard passes `atlas_decider.DECIDER_MODEL`. The model column is best-effort metadata; `None` under the safe default is correct.

Implement that: add `decider_model: str | None = None` to `__init__` (last kwarg) and `self.decider_model = decider_model`.

- [ ] **Step 3b: Wire the real decider into the dashboard**

In `atlas/dashboard/app.py`, `_get_dispatcher`, build the decider from `app.state.decide_fn` (injectable; default = real LLM decider) and pass it + the model:

```python
    if app.state.dispatcher is None:
        import dispatcher as dmod
        import atlas_decider
        decide_fn = getattr(app.state, "decide_fn", None)
        if decide_fn is None:
            decide_fn = atlas_decider.make_llm_decider()
        app.state.dispatcher = dmod.Dispatcher(
            projects_dir=app.state.projects_dir,
            produce_fn=app.state.produce_fn,
            max_in_flight=app.state.max_in_flight,
            max_retries=getattr(app.state, "max_retries", 1),
            decide_fn=decide_fn,
            decider_model=atlas_decider.DECIDER_MODEL)
    return app.state.dispatcher
```

> Add `app.state.decide_fn = None` wherever the app initializes its other injectables (next to `app.state.produce_fn`). If e2e/unit tests construct the app and rely on the safe default, leaving `decide_fn=None` here means the dashboard would build a *real* LLM decider — so the app factory must default `app.state.decide_fn` to `supervisor.safe_default_decider` under test, OR the test config already injects `produce_fn` (no real produce) and never reaches a decision point. Check `dashboard/app.py`'s app-state initialization and the e2e `conftest.py`: if any e2e drives a real failure/gate, inject `app.state.decide_fn = supervisor.safe_default_decider` there. Document what you found in the task report.

- [ ] **Step 4: Run the new test + the FULL atlas & dashboard suites (regression gate)**

Run: `python3 -m pytest tests/test_dispatcher.py -k decision_is_logged -q`
Then: `python3 -m pytest tests/ dashboard/tests/ -q -p no:cacheprovider`
Expected: the new test PASSES; the full run is green **except** the one known-flaky SSE test `dashboard/tests/test_belt_api.py::test_event_stream_backfills_then_stops_on_disconnect` (passes in isolation). Confirm with `python3 -m pytest dashboard/tests/test_belt_api.py -q` (all green in isolation).

- [ ] **Step 5: Commit**

```bash
git add atlas/dispatcher.py atlas/dashboard/app.py atlas/tests/test_dispatcher.py
git commit -m "feat(control-room): decision observability (log + event) + wire LLM decider into the dashboard"
```

---

## Self-Review

**Spec coverage (Slice 2 scope, from the design doc §1–§3 + the mission):**
- Wire `atlas_decide` to a real LLM call, model `claude-opus-4-8`, reusing `llm.chat` → Task 3. ✓
- `FIX_AND_RERUN` delegating a fix to Marlow, counted to 2, never ships a block → Tasks 5 (cap/counter/executor) + 7 (hint reaches Marlow). ✓
- Schema-validate decisions; illegal → ESCALATE → Task 1 (+ decider uses it, Task 3). ✓
- Release the in-flight slot before the LLM call + concurrency proof → Task 4. ✓
- Per-gate attempt counter + per-video decision budget, persisted before acting → Tasks 2 (transforms) + 5 (executor). ✓
- Gate-scope `APPROVE_GATE` (factcheck illegal in the executor) → Task 6. ✓
- Harden gate detection (no `blocked` with a bogus gate) → Task 5 (`_escalate` clamps gate to `LEGAL_GATES`). ✓
- Both Atlas call-shapes read/append `project.json` decision history → Task 2 (`record_decision` writes `history` with `initiator="atlas"`; the chat orchestrator already reads `history`). ✓
- Observability: decision kind, rationale, latency to event ring + project history → Task 8. ✓
- Safe-default fallback on decider error → Task 3 (`make_llm_decider` try/except). ✓

**Deferred (correctly) to later slices:** render budget rule + HyperFrames card (Slice 3 — Slice 2 escalates every `APPROVE_GATE`); Needs-You tray attempt-history UI + live "what Atlas is doing" feed + digest (Slice 4 — Slice 2 only emits the `initiator="atlas"` events they consume); `POST /api/atlas/request` unify (Slice 5).

**Placeholder scan:** none — every step has exact code, commands, and expected output. The two flagged *judgement* points (KILL→`cancelled` status persistence in Task 6; e2e `decide_fn` default in Task 8) tell the implementer exactly what to check and what to do for each branch.

**Type consistency:** `decide_fn`/`self._decide` (Slice 1); `Decision(kind, stage, gate, reason, instructions, payload)`; `make_llm_decider(chat_fn=…, model=…)` → `decide(slug, result, context)`; `validate_decision`/`decision_from_dict`/`record_decision`/`bump_fix_attempt`/`fix_attempts`/`bump_decision`/`decisions_count`/`ensure_supervisor_block` consistent across Tasks 1–8; `_build_context`/`_escalate`/`_do_fix_and_rerun`/`_over_decision_budget`/`_persist_revision_hint`/`_load_project`/`_save_project` consistent in the dispatcher; `run_write` folds `revision_hint`. `max_fix_attempts`/`max_decisions`/`decider_model` `__init__` kwargs consistent between Tasks 5/8 and the dashboard wiring.
