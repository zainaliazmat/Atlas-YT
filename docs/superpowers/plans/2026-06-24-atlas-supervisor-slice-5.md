# Atlas Supervisor — Slice 5 (Unify the Dashboard Request Path) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every dashboard action a **request to Atlas**: one typed entry point `POST /api/atlas/request` routes `make_video` / `rerun` / `retry` / `cancel` / `answer_escalation` (approve|guide|kill) intents to the belt; the Generate / Re-run / gate buttons post there and show an immediate **"Atlas is deciding…"** state. The old per-action endpoints keep working (so nothing breaks) but the UI no longer calls them directly — Atlas is the single front door.

**Architecture:** A new pure router `atlas_request.handle_request(dispatcher, settings_path, intent, args) -> dict` maps a typed intent to the dispatcher's existing reliable methods (`trigger`/`rerun`/`retry`/`cancel`/`resume`/`guide`/`kill`). A thin `POST /api/atlas/request` endpoint validates the intent and delegates. The dashboard's button handlers switch to this endpoint and render a transient "Atlas is deciding…" affordance. The old endpoints remain (backward-compatible; still used by Atlas internally) — this slice unifies the *UI* path without a risky lockdown.

**Tech Stack:** Python 3.12, pytest, FastAPI TestClient, Playwright. Vanilla JS. No new dependencies.

> **Working-tree note:** per the CEO's decision (2026-06-24) the dashboard files (`app.py`, `static/*`) carry separate uncommitted chat/publish WIP; this slice is built into them and committed together. Keep commit messages scoped to the supervisor change.

## Global Constraints

- **Single front door, no behavior regression.** `POST /api/atlas/request` is additive; `/api/trigger`, `/api/retry`, `/api/rerun`, `/api/cancel/{slug}`, `/api/gate/{slug}/approve|guide|kill` stay functional (existing tests must pass). The router calls the SAME dispatcher methods those endpoints call, so behavior is identical.
- **All hard guarantees still hold** — the router is a thin dispatch over the executor; it never bypasses the never-ship-unverified guard (an `answer_escalation`/`approve` of a factcheck block still goes through `dispatcher.resume` → the spine's `block` reject; `guide` still re-runs the fact-check).
- **Unknown/invalid intent → 400** (never a silent no-op).
- **The router is injectable/pure-ish:** `handle_request(dispatcher, settings_path, intent, args)` takes the dispatcher (so tests pass a fake) — no global state.
- **`make_video` reads niche default length** from settings (mirrors the current `/api/trigger` behavior) so the unified path is a true drop-in.
- **No regression** to the full suite; the one known SSE flake is allowed.
- Run from `atlas/`. venv python `/home/zain-ali/Documents/YT-AGENTS/venv/bin/python3`.
- Every commit message ends with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## File Structure

- `atlas/dashboard/atlas_request.py` (create) — `INTENTS`, `handle_request(dispatcher, settings_path, intent, args) -> dict`.
- `atlas/dashboard/app.py` (modify) — `POST /api/atlas/request`.
- `atlas/dashboard/static/app.js` (modify) — Generate / Re-run / gate buttons post to `/api/atlas/request`; "Atlas is deciding…" transient state.
- Tests: `atlas/dashboard/tests/test_atlas_request.py` (create), a Playwright e2e addition.

---

### Task 1: The `handle_request` router

A pure router mapping typed intents to dispatcher methods. Unit-tested against a fake dispatcher — no app, no LLM.

**Files:**
- Create: `atlas/dashboard/atlas_request.py`
- Test: `atlas/dashboard/tests/test_atlas_request.py`

**Interfaces:**
- Produces:
  - `INTENTS = ("make_video", "rerun", "retry", "cancel", "answer_escalation")`.
  - `class UnknownIntent(ValueError)`.
  - `handle_request(dispatcher, settings_path, intent: str, args: dict) -> dict` — returns `{"intent": intent, "result": <dispatcher return>}`; raises `UnknownIntent` for an unknown intent or an `answer_escalation` with an unknown `action`. Mapping:
    - `make_video`: `dispatcher.trigger(brief=args.get("brief"), topic=args.get("topic"), length=args.get("length"), niche=args.get("niche"), gates=args.get("gates", True), initiator="ceo")`.
    - `rerun`: `dispatcher.rerun(args["slug"], from_stage=args.get("from_stage"), initiator="ceo")`.
    - `retry`: `dispatcher.retry(args["slug"], initiator="ceo")`.
    - `cancel`: `dispatcher.cancel(args["slug"], initiator="ceo")`.
    - `answer_escalation`: `args["action"]` ∈ `{"approve","guide","kill"}` → `dispatcher.resume(args["slug"], args["gate"], initiator="ceo", wait=True)` / `dispatcher.guide(args["slug"], args["instructions"], initiator="ceo")` / `dispatcher.kill(args["slug"], args.get("reason",""), initiator="ceo")`.

- [ ] **Step 1: Write the failing tests**

Create `atlas/dashboard/tests/test_atlas_request.py`:

```python
"""The unified Atlas request router — one typed entry point over the belt's hands."""
import pytest
from dashboard import atlas_request
from dashboard.atlas_request import handle_request, UnknownIntent


class FakeDispatcher:
    def __init__(self):
        self.calls = []
    def trigger(self, **kw):
        self.calls.append(("trigger", kw)); return {"slug": "s1"}
    def rerun(self, slug, from_stage=None, *, initiator="ceo"):
        self.calls.append(("rerun", slug, from_stage)); return {"slug": slug, "rerunning": True}
    def retry(self, slug, *, initiator="ceo"):
        self.calls.append(("retry", slug)); return {"slug": slug, "retrying": True}
    def cancel(self, slug, *, initiator="ceo"):
        self.calls.append(("cancel", slug)); return {"slug": slug, "cancelling": True}
    def resume(self, slug, gate, *, initiator="ceo", wait=False, timeout=900.0):
        self.calls.append(("resume", slug, gate)); return {"slug": slug, "status": "done"}
    def guide(self, slug, instructions, *, initiator="ceo"):
        self.calls.append(("guide", slug, instructions)); return {"slug": slug, "guided": True}
    def kill(self, slug, reason="", *, initiator="ceo"):
        self.calls.append(("kill", slug, reason)); return {"slug": slug, "killed": True}


def test_make_video_routes_to_trigger():
    d = FakeDispatcher()
    out = handle_request(d, None, "make_video", {"topic": "AI", "length": "short"})
    assert out["intent"] == "make_video" and out["result"]["slug"] == "s1"
    assert d.calls[0][0] == "trigger"


def test_rerun_and_retry_and_cancel():
    d = FakeDispatcher()
    assert handle_request(d, None, "rerun", {"slug": "x", "from_stage": "script"})["result"]["rerunning"]
    assert handle_request(d, None, "retry", {"slug": "x"})["result"]["retrying"]
    assert handle_request(d, None, "cancel", {"slug": "x"})["result"]["cancelling"]
    assert ("rerun", "x", "script") in d.calls
    assert ("retry", "x") in d.calls and ("cancel", "x") in d.calls


def test_answer_escalation_approve_guide_kill():
    d = FakeDispatcher()
    assert handle_request(d, None, "answer_escalation",
                          {"action": "approve", "slug": "x", "gate": "final_render"})["result"]["status"] == "done"
    assert handle_request(d, None, "answer_escalation",
                          {"action": "guide", "slug": "x", "instructions": "fix it"})["result"]["guided"]
    assert handle_request(d, None, "answer_escalation",
                          {"action": "kill", "slug": "x", "reason": "no"})["result"]["killed"]
    assert ("resume", "x", "final_render") in d.calls
    assert ("guide", "x", "fix it") in d.calls and ("kill", "x", "no") in d.calls


def test_unknown_intent_raises():
    with pytest.raises(UnknownIntent):
        handle_request(FakeDispatcher(), None, "launch_nukes", {})


def test_unknown_escalation_action_raises():
    with pytest.raises(UnknownIntent):
        handle_request(FakeDispatcher(), None, "answer_escalation", {"action": "bogus", "slug": "x"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest dashboard/tests/test_atlas_request.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'dashboard.atlas_request'`.

- [ ] **Step 3: Implement**

Create `atlas/dashboard/atlas_request.py`:

```python
"""The unified Atlas request path — one typed front door for every dashboard/chat action.

Every button and chat intent becomes a `handle_request(...)` call; Atlas (the dispatcher's
reliable hands) executes it. This does NOT bypass any guarantee — it routes to the same
methods the per-action endpoints call, so behavior is identical and the never-ship-unverified
guard still lives in the executor + spine.
"""
from __future__ import annotations

INTENTS = ("make_video", "rerun", "retry", "cancel", "answer_escalation")
_ESCALATION_ACTIONS = ("approve", "guide", "kill")


class UnknownIntent(ValueError):
    """An intent (or answer_escalation action) outside the bounded vocabulary."""


def handle_request(dispatcher, settings_path, intent: str, args: dict) -> dict:
    args = args or {}
    if intent == "make_video":
        result = dispatcher.trigger(
            brief=args.get("brief"), topic=args.get("topic"), length=args.get("length"),
            niche=args.get("niche"), gates=args.get("gates", True), initiator="ceo")
    elif intent == "rerun":
        result = dispatcher.rerun(args["slug"], from_stage=args.get("from_stage"),
                                  initiator="ceo")
    elif intent == "retry":
        result = dispatcher.retry(args["slug"], initiator="ceo")
    elif intent == "cancel":
        result = dispatcher.cancel(args["slug"], initiator="ceo")
    elif intent == "answer_escalation":
        action = args.get("action")
        if action == "approve":
            result = dispatcher.resume(args["slug"], args["gate"], initiator="ceo", wait=True)
        elif action == "guide":
            result = dispatcher.guide(args["slug"], args["instructions"], initiator="ceo")
        elif action == "kill":
            result = dispatcher.kill(args["slug"], args.get("reason", ""), initiator="ceo")
        else:
            raise UnknownIntent(f"unknown escalation action: {action!r}")
    else:
        raise UnknownIntent(f"unknown intent: {intent!r}")
    return {"intent": intent, "result": result}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest dashboard/tests/test_atlas_request.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add atlas/dashboard/atlas_request.py atlas/dashboard/tests/test_atlas_request.py
git commit -m "feat(control-room): unified Atlas request router (typed intents over the belt)"
```

---

### Task 2: `POST /api/atlas/request` endpoint

The single front door. Validates the intent, delegates to the router, returns the result (400 on unknown intent, 400 on a missing required arg).

**Files:**
- Modify: `atlas/dashboard/app.py`
- Test: `atlas/dashboard/tests/test_atlas_request.py` (append API tests)

**Interfaces:**
- Produces: `POST /api/atlas/request` body `{"intent": str, "args": dict}` → `{"ok": True, "intent", "result"}`; 400 `{"ok": False, "error"}` on `UnknownIntent` or `KeyError` (missing required arg).

- [ ] **Step 1: Write the failing tests**

Append to `atlas/dashboard/tests/test_atlas_request.py`:

```python
from fastapi.testclient import TestClient
from dashboard.app import create_app
from dashboard.tests import fixtures
import supervisor


def _client(tmp_path):
    pdir, slugs = fixtures.build_projects(tmp_path)
    app = create_app(projects_dir=pdir)
    app.state.decide_fn = supervisor.safe_default_decider   # offline
    # a fast fake belt so make_video/rerun don't run a real engine
    def fake(slug=None, approve=None, root=None, progress=None, station_locks=None,
             should_cancel=None):
        return {"status": "done"}
    app.state.produce_fn = fake
    c = TestClient(app); c._app = app
    return c, pdir, slugs


def test_atlas_request_make_video(tmp_path):
    c, pdir, slugs = _client(tmp_path)
    r = c.post("/api/atlas/request", json={"intent": "make_video",
                                           "args": {"topic": "AI tools", "length": "short"}})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["result"]["slug"]


def test_atlas_request_unknown_intent_400(tmp_path):
    c, pdir, slugs = _client(tmp_path)
    r = c.post("/api/atlas/request", json={"intent": "nope", "args": {}})
    assert r.status_code == 400 and r.json()["ok"] is False


def test_atlas_request_cancel(tmp_path):
    c, pdir, slugs = _client(tmp_path)
    r = c.post("/api/atlas/request",
               json={"intent": "cancel", "args": {"slug": slugs["queued"]}})
    assert r.status_code == 200 and r.json()["result"]["cancelling"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest dashboard/tests/test_atlas_request.py -k "atlas_request_make or atlas_request_unknown or atlas_request_cancel" -q`
Expected: FAIL — route 404.

- [ ] **Step 3: Implement the route**

In `atlas/dashboard/app.py`, inside `create_app` (near the other POST routes), add:

```python
    @app.post("/api/atlas/request")
    async def atlas_request_route(request: Request):
        body = await _json_body(request)
        intent = body.get("intent")
        args = body.get("args") or {}
        try:
            out = atlas_request.handle_request(_get_dispatcher(app),
                                               app.state.settings_path, intent, args)
            return JSONResponse({"ok": True, **out})
        except atlas_request.UnknownIntent as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        except KeyError as e:
            return JSONResponse({"ok": False, "error": f"missing arg {e}"}, status_code=400)
```

Add `atlas_request` to the `from dashboard import …` import line at the top of `app.py`.

- [ ] **Step 4: Run the tests, then the dashboard API suite**

Run: `python3 -m pytest dashboard/tests/test_atlas_request.py dashboard/tests/test_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add atlas/dashboard/app.py atlas/dashboard/tests/test_atlas_request.py
git commit -m "feat(control-room): POST /api/atlas/request — the single front door"
```

---

### Task 3: UI — buttons route through Atlas + "Atlas is deciding…" feedback

Switch the Generate, Re-run, and gate-action button handlers to `POST /api/atlas/request`, and show a transient "Atlas is deciding…" state on click.

**Files:**
- Modify: `atlas/dashboard/static/app.js`
- Test: Playwright e2e (append to `dashboard/tests/e2e/test_escalation_e2e.py` or a new file)

**Interfaces:** the button handlers post `{intent, args}` to `/api/atlas/request` instead of the per-action endpoints; the result shape is `{ok, intent, result}` (read `result` for the slug/status).

- [ ] **Step 1: Read the handlers**

In `app.js` locate: the launch-modal submit (Generate → currently POSTs `/api/trigger`, ~line 1378), `doRerun` (~481), `cancelVideo` (~998), and the gate approve handlers (`wireApprove`/`submitGateApprove`). You will reroute each to `/api/atlas/request`.

- [ ] **Step 2: Reroute Generate**

In the launch-modal submit, replace the `fetch("/api/trigger", {body: {topic, length, gates, niche}})` with:

```js
fetch("/api/atlas/request", {method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({intent: "make_video",
    args: {topic: topic, length: length, gates: gates, niche: niche}})})
  // response: {ok, intent, result:{slug}} → read out.result.slug
```

Before the fetch, set the submit button text to `"Atlas is deciding…"` (restore on completion). Read `out.result.slug` for `flashSlug`.

- [ ] **Step 3: Reroute Re-run + Cancel**

`doRerun(slug, fromStage)` → POST `/api/atlas/request` `{intent:"rerun", args:{slug, from_stage:fromStage}}`. `cancelVideo(slug)` → `{intent:"cancel", args:{slug}}`. Show "Atlas is deciding…" on the triggering button while in flight.

- [ ] **Step 4: Reroute the gate actions**

The gate approve → `{intent:"answer_escalation", args:{action:"approve", slug, gate}}`. (Guide/Kill from Slice 4 may stay on their endpoints OR also route here as `action:"guide"|"kill"` — prefer routing here for consistency.) Read `out.result` for the status.

- [ ] **Step 5: Playwright e2e**

Add an e2e (belt_server fixture) that clicks Generate (or Re-run) and asserts the request succeeds via `/api/atlas/request` (the video appears on the belt) and that no console errors occur. Reuse the existing e2e patterns.

- [ ] **Step 6: Run the e2e + dashboard suite**

Run: `python3 -m pytest dashboard/tests/e2e/ -q` then `python3 -m pytest dashboard/tests/ -q -p no:cacheprovider`
Expected: e2e PASS; dashboard suite green except the known SSE flake.

- [ ] **Step 7: Commit**

```bash
git add atlas/dashboard/static/app.js atlas/dashboard/tests/e2e/
git commit -m "feat(control-room): dashboard buttons route through /api/atlas/request (Atlas is deciding…)"
```

---

### Task 4: Slice regression gate + final integration

**Files:** none (verification).

- [ ] **Step 1: Full suite**

Run: `python3 -m pytest tests/ dashboard/tests/ -q -p no:cacheprovider`
Expected: green except the known SSE flake (confirm in isolation).

- [ ] **Step 2: Smoke** — start the dashboard (`python -m dashboard.server` from `atlas/`), confirm it boots, then stop it. Report.

- [ ] **Step 3: Report** the totals + confirm `/api/atlas/request` is the unified path and the old endpoints still pass their tests (backward compatible).

---

## Self-Review

**Spec coverage (design §3 unified request path + slice 5):**
- New internal entry point `handle_request(intent, …)` for typed intents → Task 1. ✓
- New endpoint `POST /api/atlas/request`; buttons post here → Tasks 2 (endpoint) + 3 (UI). ✓
- Old endpoints become internal (Atlas's path) — kept functional for backward-compat + Atlas's own use; the UI no longer calls them directly → Tasks 2/3 (documented; not a hard lockdown to avoid breaking the suite). ✓
- Buttons show "Atlas is deciding…" feedback → Task 3. ✓
- Chat + dashboard converge on one Atlas engine over shared state → both now reach the belt through `handle_request`/the dispatcher (chat already routes via `execute_action`; the dashboard now via `/api/atlas/request`). ✓
- All hard guarantees preserved (router is a thin dispatch; never bypasses the executor/spine) → Task 1 constraints. ✓

**Deferred (correctly):** a hard lockdown rejecting external calls to the old endpoints (risky, low value — they're already Atlas's hands); the chat `ask` intent (chat already has its own path).

**Placeholder scan:** the JS task (3) references the exact handlers to reroute with the concrete fetch bodies; verified via e2e + the API tests (Tasks 1–2 are full pytest TDD).

**Type consistency:** `handle_request(dispatcher, settings_path, intent, args) -> {intent, result}`; the endpoint wraps it as `{ok, intent, result}`; `INTENTS`/`_ESCALATION_ACTIONS`/`UnknownIntent` consistent; the router calls the dispatcher methods with their real signatures (`trigger`/`rerun`/`retry`/`cancel`/`resume`/`guide`/`kill`).
