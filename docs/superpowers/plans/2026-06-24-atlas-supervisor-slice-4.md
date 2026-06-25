# Atlas Supervisor — Slice 4 (Escalation Surface + Live Feed) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Atlas's autonomy visible and actionable: capture the **before/after of each fact-check fix attempt**, surface it on the escalation card with **Guide** (free-text → next fix) and **Kill** actions, and show a live **"Atlas is doing X (fix 1/2)"** line per video driven by the `initiator="atlas"` events.

**Architecture:** The engine captures a per-attempt snapshot (`supervisor.fix_history[gate]`, parallel to the existing int `fix_attempts` counter so the cap logic is untouched) and exposes the latest Atlas activity per video. The data layer (`data.py`) surfaces both — `gate_detail` gains `fix_history`, `belt` gains `atlas_activity` per video. The UI (`app.js`/`index.html`/`styles.css`) renders the live per-video Atlas line, the attempt-history card, the Guide/Kill actions, and an escalation digest header. Two new endpoints (`/api/gate/{slug}/guide`, `/api/gate/{slug}/kill`) drive a CEO-guided fix and a kill.

**Tech Stack:** Python 3.12, pytest, FastAPI TestClient, Playwright (e2e). Vanilla JS dashboard. No new dependencies.

> **Working-tree note:** the dashboard files (`app.py`, `data.py`, `static/*`) carry separate uncommitted chat/publish WIP; per the CEO's decision (2026-06-24) this slice is built into them and committed together. Keep each commit message scoped to the supervisor change. The Python engine tasks (1) commit cleanly; the rest necessarily touch the entangled files.

## Global Constraints

- **The fact-check cap + prohibition are unchanged.** `fix_attempts[gate]` stays the int counter the cap reads (`>= max_fix_attempts → escalate`); `fix_history[gate]` is a NEW parallel list of snapshots, read-only for the cap. `APPROVE_GATE(factcheck)` is still illegal in the executor. Guide is a CEO-initiated fix that still re-runs the fact-check (never ships a block); Kill abandons the video.
- **Snapshots capture the trajectory:** each fact-check `FIX_AND_RERUN` records `{n, ts, flagged_before: [...claims...], instructions}` before re-running. The current `factcheck_report.json` is the latest "after"; the card shows each attempt's flagged-before + Atlas's instructions + the current still-flagged set, so the CEO sees progress in seconds.
- **The live feed is driven by `initiator="atlas"` events / the supervisor log** — no new polling loop; reuse the existing `/api/events` SSE + `/api/belt`/`/api/activity` snapshots.
- **Guide is bounded like the auto-fix:** a CEO Guide persists a revision hint + re-runs from the script stage (reusing the Slice-2 `_persist_revision_hint` + `rerun`); it does NOT bypass the fact-check.
- **No regression** to the supervisor/dispatcher/dashboard suites; the one known SSE flake (`test_belt_api.py::test_event_stream_backfills_then_stops_on_disconnect`) is allowed (passes in isolation).
- Run from `atlas/`. venv python `/home/zain-ali/Documents/YT-AGENTS/venv/bin/python3 -m pytest …`. Playwright e2e: `python3 -m pytest dashboard/tests/e2e/ …`.
- Every commit message ends with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## File Structure

- `atlas/supervisor.py` (modify) — `record_fix_snapshot`, `fix_history` (pure transforms).
- `atlas/dispatcher.py` (modify) — `_do_fix_and_rerun` records the snapshot; `_atlas_activity(slug)` → the latest Atlas-log line; `guide(slug, instructions)` + `kill(slug, reason)` public methods.
- `atlas/dashboard/data.py` (modify) — `gate_detail` adds `fix_history`; `belt` videos gain `atlas_activity`.
- `atlas/dashboard/app.py` (modify) — `POST /api/gate/{slug}/guide`, `POST /api/gate/{slug}/kill`.
- `atlas/dashboard/static/app.js` + `index.html` + `styles.css` (modify) — per-video Atlas line, attempt-history card, Guide/Kill buttons, escalation digest, new `ACT_KINDS`.
- Tests: `atlas/tests/test_supervisor.py`, `atlas/tests/test_dispatcher.py`, `atlas/dashboard/tests/test_api.py` (or `test_belt_api.py`), a new `atlas/dashboard/tests/test_escalation_api.py`, and a Playwright e2e `atlas/dashboard/tests/e2e/test_escalation_e2e.py`.

---

### Task 1: Fix-attempt snapshot transforms (pure)

`supervisor.py` gains a parallel `fix_history` capture so the escalation card can show the before/after of each fix — without disturbing the int `fix_attempts` counter the cap depends on.

**Files:**
- Modify: `atlas/supervisor.py`
- Test: `atlas/tests/test_supervisor.py` (append)

**Interfaces:**
- Produces:
  - `record_fix_snapshot(project: dict, gate: str, *, attempt_no: int, flagged: list, instructions: str = "") -> dict` — appends `{"n": attempt_no, "ts": <time>, "flagged_before": flagged, "instructions": instructions}` to `project["supervisor"]["fix_history"].setdefault(gate, [])`; returns the entry. Uses `ensure_supervisor_block` and sets `fix_history` if absent.
  - `fix_history(project: dict, gate: str) -> list` — the recorded snapshots for a gate (empty list if none).

- [ ] **Step 1: Write the failing tests**

Append to `atlas/tests/test_supervisor.py`:

```python
from supervisor import record_fix_snapshot, fix_history


def test_record_fix_snapshot_appends_trajectory():
    p = {}
    e1 = record_fix_snapshot(p, "factcheck", attempt_no=1,
                             flagged=[{"claim_id": "s5c2", "status": "flagged"}],
                             instructions="drop s5c2")
    assert e1["n"] == 1 and e1["instructions"] == "drop s5c2"
    record_fix_snapshot(p, "factcheck", attempt_no=2, flagged=[], instructions="ok")
    hist = fix_history(p, "factcheck")
    assert [h["n"] for h in hist] == [1, 2]
    assert hist[0]["flagged_before"][0]["claim_id"] == "s5c2"
    assert fix_history(p, "never") == []


def test_fix_history_is_separate_from_fix_attempts_counter():
    from supervisor import bump_fix_attempt, fix_attempts
    p = {}
    bump_fix_attempt(p, "factcheck"); bump_fix_attempt(p, "factcheck")
    record_fix_snapshot(p, "factcheck", attempt_no=2, flagged=[], instructions="")
    assert fix_attempts(p, "factcheck") == 2          # the int cap counter is intact
    assert isinstance(p["supervisor"]["fix_attempts"]["factcheck"], int)
    assert len(fix_history(p, "factcheck")) == 1       # parallel list, unrelated to the int
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_supervisor.py -k "fix_snapshot or fix_history_is_separate" -q`
Expected: FAIL with `ImportError: cannot import name 'record_fix_snapshot'`.

- [ ] **Step 3: Implement**

Append to `atlas/supervisor.py`:

```python
def record_fix_snapshot(project: dict, gate: str, *, attempt_no: int, flagged: list,
                        instructions: str = "") -> dict:
    """Capture a fix attempt's 'before' state (the flagged claims + Atlas's instructions),
    PARALLEL to the int fix_attempts counter (which the cap reads). The escalation card
    diffs successive snapshots against the current report to show the CEO the trajectory."""
    blk = ensure_supervisor_block(project)
    blk.setdefault("fix_history", {})
    entry = {"n": attempt_no, "ts": time.time(),
             "flagged_before": flagged or [], "instructions": instructions or ""}
    blk["fix_history"].setdefault(gate, []).append(entry)
    return entry


def fix_history(project: dict, gate: str) -> list:
    return ensure_supervisor_block(project).get("fix_history", {}).get(gate, [])
```

Also add `blk.setdefault("fix_history", {})` inside `ensure_supervisor_block` (so the block is always well-formed):

```python
    blk.setdefault("fix_history", {})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_supervisor.py -q`
Expected: PASS (all prior + the 2 new).

- [ ] **Step 5: Commit**

```bash
git add atlas/supervisor.py atlas/tests/test_supervisor.py
git commit -m "feat(control-room): fix-attempt snapshot history (before + instructions, parallel to the cap counter)"
```

---

### Task 2: Dispatcher captures snapshots + exposes Atlas activity + guide/kill

The executor records a snapshot before each fact-check fix; a helper exposes the latest Atlas activity line for the live feed; and `guide`/`kill` give the escalation card its actions.

**Files:**
- Modify: `atlas/dispatcher.py` (`_do_fix_and_rerun`, add `_atlas_activity`, `guide`, `kill`)
- Test: `atlas/tests/test_dispatcher.py` (append)

**Interfaces:**
- Consumes: `supervisor.record_fix_snapshot`, `supervisor.fix_history`, `_persist_revision_hint`, `rerun`, `_mark_cancelled`, `chat_state`.
- Produces:
  - `_do_fix_and_rerun` records a snapshot (gate `factcheck`: the current `factcheck_report.json` flagged claims) before re-running.
  - `_atlas_activity(slug: str) -> dict | None` — `{"text": str, "ts": float}` from the latest `supervisor.log` entry (`f"Atlas: {kind}"` + reason), else `None`.
  - `guide(slug: str, instructions: str, *, initiator: str = "ceo") -> dict` — persists the revision hint, records a `guide` history entry, re-runs from `script`. Returns `{"slug", "guided": True}`.
  - `kill(slug: str, reason: str = "", *, initiator: str = "ceo") -> dict` — marks cancelled + emits `killed`. Returns `{"slug", "killed": True}`.

- [ ] **Step 1: Write the failing tests**

Append to `atlas/tests/test_dispatcher.py`:

```python
def test_fix_and_rerun_records_a_snapshot(tmp_path):
    """A fact-check FIX_AND_RERUN snapshots the flagged claims + instructions before re-run."""
    import json as _json
    from supervisor import Decision, fix_history

    def fake(slug=None, approve=None, root=None, progress=None, station_locks=None,
             should_cancel=None):
        pdir = root / slug; pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "factcheck_report.json").write_text(_json.dumps(
            {"verdict": "block", "claims": [
                {"claim_id": "s5c2", "status": "flagged", "claim_text": "42%"}]}))
        (pdir / "project.json").write_text(_json.dumps(
            {"slug": slug, "status": "blocked_at_factcheck", "stages": {}, "history": []}))
        return {"status": "blocked", "gate": "factcheck", "stage": "factcheck",
                "reason": "unverified"}

    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake,
                   decide_fn=lambda s, r, c: Decision("FIX_AND_RERUN", stage="script",
                       gate="factcheck", instructions="drop s5c2"), max_retries=0)
    slug = d.trigger(topic="snap-me")["slug"]
    assert _wait_for(lambda: bool(fix_history(_status(tmp_path, slug), "factcheck")),
                     timeout=12), _status(tmp_path, slug)
    hist = fix_history(_status(tmp_path, slug), "factcheck")
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
    proj = _status(tmp_path, "vid")
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
    assert _status(tmp_path, "vid")["status"] == "cancelled"
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
    assert act and "FIX_AND_RERUN" in act["text"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_dispatcher.py -k "records_a_snapshot or guide_persists or kill_marks or atlas_activity" -q`
Expected: FAIL (`guide`/`kill`/`_atlas_activity` absent; no snapshot recorded).

- [ ] **Step 3a: Record the snapshot in `_do_fix_and_rerun`**

In `_do_fix_and_rerun`, after the cap check and the `bump_fix_attempt`/`bump_decision`+`_save_project`, and before `_persist_revision_hint`, capture the snapshot for a factcheck gate:

```python
        if gate == "factcheck":
            report = chat_state.load_json(
                self._project_path(slug).parent / "factcheck_report.json", {})
            flagged = [c for c in (report.get("claims") or [])
                       if c.get("status") in ("flagged", "unverifiable")]
            proj2 = self._load_project(slug)
            if proj2 is not None:
                supervisor.record_fix_snapshot(proj2, gate, attempt_no=attempt_no,
                                               flagged=flagged,
                                               instructions=decision.instructions)
                self._save_project(slug, proj2)
```

> `attempt_no` is already computed in `_do_fix_and_rerun` (the `supervisor.fix_attempts(proj, gate)` after the bump). Reuse it. Keep the snapshot read+write minimal — it must not change the cap behavior.

- [ ] **Step 3b: Add `_atlas_activity`, `guide`, `kill`**

Add to `atlas/dispatcher.py`:

```python
    def _atlas_activity(self, slug: str) -> dict | None:
        """The latest Atlas decision line for the live 'what Atlas is doing' feed."""
        proj = self._load_project(slug)
        if proj is None:
            return None
        log = (proj.get("supervisor", {}) or {}).get("log") or []
        if not log:
            return None
        last = log[-1]
        text = f"Atlas: {last.get('kind', '')}"
        if last.get("reason"):
            text += f" — {last['reason']}"
        return {"text": text, "ts": last.get("ts", 0)}

    def guide(self, slug: str, instructions: str, *, initiator: str = "ceo") -> dict:
        """CEO guidance on a parked fact-check block: feed instructions to the next fix and
        re-run from the script stage. Still re-runs the fact-check — never ships a block."""
        proj = self._load_project(slug)
        if proj is None:
            return {"slug": slug, "guided": False, "reason": "no such project"}
        proj.setdefault("history", []).append(
            {"ts": time.time(), "stage": "script", "initiator": initiator,
             "decision": "guide", "why": instructions})
        self._save_project(slug, proj)
        self._persist_revision_hint(slug, "script", instructions)
        self.events.emit("fixing", slug=slug, stage="script", initiator="atlas",
                         message="re-running script (CEO guided)")
        self.rerun(slug, from_stage="script", initiator=initiator)
        return {"slug": slug, "guided": True}

    def kill(self, slug: str, reason: str = "", *, initiator: str = "ceo") -> dict:
        proj = self._load_project(slug)
        if proj is not None:
            proj.setdefault("history", []).append(
                {"ts": time.time(), "stage": None, "initiator": initiator,
                 "decision": "killed", "why": reason})
            self._save_project(slug, proj)
        self._mark_cancelled(slug)
        self.events.emit("killed", slug=slug, initiator=initiator,
                         message=reason or "killed by the CEO")
        return {"slug": slug, "killed": True}
```

- [ ] **Step 4: Run the new tests, then the full dispatcher suite**

Run: `python3 -m pytest tests/test_dispatcher.py -q`
Expected: PASS — the 4 new tests AND every pre-existing dispatcher test.

- [ ] **Step 5: Commit**

```bash
git add atlas/dispatcher.py atlas/tests/test_dispatcher.py
git commit -m "feat(control-room): capture fix snapshots + Atlas-activity line + guide/kill actions"
```

---

### Task 3: Data layer — `fix_history` on the gate card + `atlas_activity` on the belt

`data.py` surfaces the captured history and the live line so the UI can render them.

**Files:**
- Modify: `atlas/dashboard/data.py` (`gate_detail`, `belt`)
- Test: `atlas/dashboard/tests/test_api.py` (or a new `test_escalation_api.py`)

**Interfaces:**
- Produces:
  - `gate_detail(...)` for `gate == "factcheck"` adds `"fix_history": [ {n, ts, flagged_before, instructions} ]` read from `project.json["supervisor"]["fix_history"]["factcheck"]`.
  - `belt(...)` each video gains `"atlas_activity": {"text", "ts"} | None` read from the latest `supervisor.log` entry (mirror `_atlas_activity`).

- [ ] **Step 1: Write the failing tests**

Create `atlas/dashboard/tests/test_escalation_api.py`:

```python
"""Slice 4 — the escalation surface data: fix_history on the gate card, atlas_activity on belt."""
import json
from fastapi.testclient import TestClient
from dashboard.app import create_app
from dashboard.tests import fixtures


def _client(tmp_path):
    pdir, slugs = fixtures.build_projects(tmp_path)
    app = create_app(projects_dir=pdir)
    c = TestClient(app); c._app = app
    return c, pdir, slugs


def test_gate_detail_includes_fix_history(tmp_path):
    c, pdir, slugs = _client(tmp_path)
    slug = slugs["hard_block"]
    proj_path = pdir / slug / "project.json"
    proj = json.loads(proj_path.read_text())
    proj.setdefault("supervisor", {})["fix_history"] = {"factcheck": [
        {"n": 1, "ts": 1.0, "flagged_before": [{"claim_id": "s5c2", "claim_text": "42%"}],
         "instructions": "drop s5c2"}]}
    proj_path.write_text(json.dumps(proj))
    body = c.get(f"/api/gate/{slug}").json()
    assert body["kind"] == "factcheck"
    assert body["fix_history"][0]["instructions"] == "drop s5c2"
    assert body["fix_history"][0]["flagged_before"][0]["claim_id"] == "s5c2"


def test_belt_includes_atlas_activity(tmp_path):
    c, pdir, slugs = _client(tmp_path)
    slug = slugs["hard_block"]
    proj_path = pdir / slug / "project.json"
    proj = json.loads(proj_path.read_text())
    proj.setdefault("supervisor", {})["log"] = [
        {"ts": 2.0, "kind": "FIX_AND_RERUN", "reason": "fix 1/2"}]
    proj_path.write_text(json.dumps(proj))
    body = c.get("/api/belt").json()
    vid = next(v for v in body["videos"] if v["slug"] == slug)
    assert vid["atlas_activity"]["text"].startswith("Atlas: FIX_AND_RERUN")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest dashboard/tests/test_escalation_api.py -q`
Expected: FAIL — `fix_history`/`atlas_activity` not in the responses.

- [ ] **Step 3: Implement**

In `atlas/dashboard/data.py`:
1. In `gate_detail`, for the `factcheck` branch, read the supervisor block and add the history:

```python
        sup = (proj.get("supervisor", {}) or {})
        out["fix_history"] = (sup.get("fix_history", {}) or {}).get("factcheck", [])
```

(add `out["fix_history"]` to the returned dict for the factcheck gate; `proj` is the loaded project.json — match the function's existing variable name for the loaded project).

2. In `belt`, where each video dict is built (the `videos.append({...})` / per-video dict), add:

```python
        sup_log = (proj.get("supervisor", {}) or {}).get("log") or []
        atlas_activity = None
        if sup_log:
            last = sup_log[-1]
            txt = f"Atlas: {last.get('kind', '')}"
            if last.get("reason"):
                txt += f" — {last['reason']}"
            atlas_activity = {"text": txt, "ts": last.get("ts", 0)}
        # include "atlas_activity": atlas_activity in the video dict
```

(match `belt`'s existing per-video construction; `proj` is the loaded project.json for that video.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest dashboard/tests/test_escalation_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add atlas/dashboard/data.py atlas/dashboard/tests/test_escalation_api.py
git commit -m "feat(control-room): surface fix_history on the gate card + atlas_activity on the belt"
```

---

### Task 4: Guide + Kill endpoints

Two endpoints so the escalation card can act: a CEO Guide (free-text → next fix) and a Kill.

**Files:**
- Modify: `atlas/dashboard/app.py` (two routes)
- Test: `atlas/dashboard/tests/test_escalation_api.py` (append)

**Interfaces:**
- Produces:
  - `POST /api/gate/{slug}/guide` body `{"instructions": str}` → `dispatcher.guide(slug, instructions)` → `{"result": "guided", "slug"}`. 400 on empty instructions.
  - `POST /api/gate/{slug}/kill` body `{"reason"?: str}` → `dispatcher.kill(slug, reason)` → `{"result": "killed", "slug"}`.
  - Both resolve the project dir through `security.resolve_project_dir` (404 on unsafe path), mirroring `_approve_gate`.

- [ ] **Step 1: Write the failing tests**

Append to `atlas/dashboard/tests/test_escalation_api.py`:

```python
def test_guide_endpoint_reruns(tmp_path):
    c, pdir, slugs = _client(tmp_path)
    c._app.state.produce_fn = None      # guide reruns through the belt; keep it offline:
    import supervisor
    c._app.state.decide_fn = supervisor.safe_default_decider
    r = c.post(f"/api/gate/{slugs['hard_block']}/guide",
               json={"instructions": "tighten scene 5 stat"})
    assert r.status_code == 200 and r.json()["result"] == "guided"


def test_guide_rejects_empty_instructions(tmp_path):
    c, pdir, slugs = _client(tmp_path)
    r = c.post(f"/api/gate/{slugs['hard_block']}/guide", json={"instructions": "  "})
    assert r.status_code == 400


def test_kill_endpoint(tmp_path):
    c, pdir, slugs = _client(tmp_path)
    r = c.post(f"/api/gate/{slugs['hard_block']}/kill", json={"reason": "unworkable"})
    assert r.status_code == 200 and r.json()["result"] == "killed"
```

> The `guide` rerun starts a worker thread. With `produce_fn=None` the real spine runs — for the test, inject a fast fake before the call OR accept that `guide` returns immediately (it calls `rerun` which starts a daemon thread; the endpoint returns before the thread does meaningful work). If the real-spine thread is a problem in CI, inject `c._app.state.produce_fn = <fast fake>` like the belt tests do. Use the belt-test fast-fake pattern (`test_belt_api.py`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest dashboard/tests/test_escalation_api.py -k "guide or kill" -q`
Expected: FAIL — routes 404 (not defined).

- [ ] **Step 3: Implement the routes**

In `atlas/dashboard/app.py`, near `_approve_gate` / the gate routes, add:

```python
    @app.post("/api/gate/{slug}/guide")
    async def gate_guide(slug: str, request: Request):
        try:
            security.resolve_project_dir(_projects_dir(app), slug)
        except security.UnsafePathError:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await _json_body(request)
        instructions = (body.get("instructions") or "").strip()
        if not instructions:
            return JSONResponse({"error": "instructions required"}, status_code=400)
        out = _get_dispatcher(app).guide(slug, instructions, initiator="ceo")
        return JSONResponse({"result": "guided", "slug": slug, **out})

    @app.post("/api/gate/{slug}/kill")
    async def gate_kill(slug: str, request: Request):
        try:
            security.resolve_project_dir(_projects_dir(app), slug)
        except security.UnsafePathError:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await _json_body(request)
        out = _get_dispatcher(app).kill(slug, (body.get("reason") or "").strip(),
                                        initiator="ceo")
        return JSONResponse({"result": "killed", "slug": slug, **out})
```

(Match the file's route-registration style — the routes live inside `create_app`, same as `_approve_gate`'s registration. `_json_body`, `security`, `_projects_dir`, `_get_dispatcher` already exist.)

- [ ] **Step 4: Run the tests, then the dashboard API suite**

Run: `python3 -m pytest dashboard/tests/test_escalation_api.py dashboard/tests/test_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add atlas/dashboard/app.py atlas/dashboard/tests/test_escalation_api.py
git commit -m "feat(control-room): /api/gate/{slug}/guide + /kill escalation actions"
```

---

### Task 5: UI — live Atlas line, attempt-history card, Guide/Kill, escalation digest

Render the new data: a per-video "Atlas is doing X" line on the belt, the fact-check attempt-history on the gate card with Guide/Kill, an escalation-digest header on the Needs-You tray, and the new Atlas event kinds in the activity filter.

**Files:**
- Modify: `atlas/dashboard/static/app.js`, `atlas/dashboard/static/index.html`, `atlas/dashboard/static/styles.css`
- Test: Playwright e2e `atlas/dashboard/tests/e2e/test_escalation_e2e.py` (create)

**Interfaces:** consumes `belt().videos[].atlas_activity`, `gate_detail().fix_history`, the `/api/gate/{slug}/guide|kill` endpoints, and the new event kinds.

- [ ] **Step 1: Read the target functions**

Open `atlas/dashboard/static/app.js` and locate: `spineRow` (~949), `renderNeedsTray` (~966), `renderFactGate` (~734), `wireApprove` (~830), `ACT_KINDS` (~1010). You will extend each.

- [ ] **Step 2: Live Atlas line on the belt spine**

In `spineRow`, when `v.atlas_activity` is present, render its `text` as a highlighted sub-line under the spine (a `<div class="atlas-line">🤖 <text></div>`). Add a `.atlas-line` style in `styles.css` (an accent color, small text). Add the new kinds to `ACT_KINDS`:

```js
var ACT_KINDS = ["triggered", "progress", "decision", "fixing", "approving", "retry",
                 "rerun", "rerunning", "interrupted", "blocked", "gate_approved",
                 "failed", "killed", "cancel_requested", "cancelled", "done"];
```

- [ ] **Step 3: Escalation digest on the Needs-You tray**

In `renderNeedsTray`, when `items.length > 1`, prepend a digest header: `<div class="tray-digest">⚑ <N> videos need you</div>`. Keep the per-item cards as today.

- [ ] **Step 4: Attempt-history card + Guide/Kill on the fact-check gate**

In `renderFactGate`, when `d.fix_history && d.fix_history.length`, render an "Atlas auto-fix attempts" section: one row per attempt showing `Attempt n`, the `instructions`, and the `flagged_before` claim ids — so the CEO sees the trajectory vs the current flagged set. In the decision column, add a **Kill** button (`#gt-kill`) and a **Guide** control (a textarea `#gt-guide-text` + a `#gt-guide` button). Wire them:

```js
// Guide: POST /api/gate/<slug>/guide {instructions}; on success show "re-running (guided)".
// Kill:  POST /api/gate/<slug>/kill {reason}; on success show "killed".
```

Mirror `wireApprove`'s fetch/spinner/result pattern (`#gt-result`). Disable Guide when the textarea is empty.

- [ ] **Step 5: Write a Playwright e2e**

Create `atlas/dashboard/tests/e2e/test_escalation_e2e.py` using the `belt_server` fixture pattern. Seed a project with `supervisor.fix_history` + a `blocked_at_factcheck` status; open the gate via the projects screen; assert the attempt-history section renders and the Guide/Kill buttons exist. Assert no console errors (the `guard_console` fixture). Follow the existing e2e patterns in `dashboard/tests/e2e/test_e2e.py` (the `_open_gate_via_projects` helper).

- [ ] **Step 6: Run the e2e + the dashboard suite**

Run: `python3 -m pytest dashboard/tests/e2e/test_escalation_e2e.py -q` then `python3 -m pytest dashboard/tests/ -q -p no:cacheprovider`
Expected: e2e PASS; dashboard suite green except the known SSE flake.

- [ ] **Step 7: Commit**

```bash
git add atlas/dashboard/static/app.js atlas/dashboard/static/index.html atlas/dashboard/static/styles.css atlas/dashboard/tests/e2e/test_escalation_e2e.py
git commit -m "feat(control-room): live Atlas line + fact-check attempt-history card + Guide/Kill UI"
```

---

### Task 6: Slice regression gate

**Files:** none (verification).

- [ ] **Step 1: Full suite**

Run: `python3 -m pytest tests/ dashboard/tests/ -q -p no:cacheprovider`
Expected: green except the known SSE flake (confirm in isolation: `python3 -m pytest dashboard/tests/test_belt_api.py -q`).

- [ ] **Step 2: Report** the totals + confirm the fact-check guarantees (the Slice-2 cap tests) still pass.

---

## Self-Review

**Spec coverage (design §4 escalation surface + the mission's "live feed"):**
- Needs-You tray with fact-check attempt history (before/after of each fix) → Tasks 1 (capture) + 3 (data) + 5 (card render). ✓
- Live "Atlas is doing X (fix 1/2)" feed driven by `initiator="atlas"` events → Task 2 `_atlas_activity` + 3 (belt) + 5 (spine line) + the existing `/api/events`. ✓
- Escalation digest (batch multiple) → Task 5 tray digest header. ✓
- Guide (free-text → next fix) + Kill → Task 2 (`guide`/`kill`) + 4 (endpoints) + 5 (buttons). ✓
- Audit plane `initiator="atlas"` → already emitted (Slice 2); the new kinds are added to `ACT_KINDS` (Task 5). ✓
- Never ships a block: Guide still re-runs the fact-check (Task 2 `guide` → `rerun` from script). ✓

**Deferred (correctly):** request-path unify (Slice 5). Push notifications (optional, out of scope).

**Placeholder scan:** the JS tasks (5) reference the exact functions to extend with the concrete additions; vanilla-JS has no unit-test runner here, so they are verified via the data-layer pytest (Task 3) + the Playwright e2e (Task 5). The Python engine/data tasks (1–4) are full TDD with exact code.

**Type consistency:** `fix_history[gate]` is a list of `{n, ts, flagged_before, instructions}`; `fix_attempts[gate]` stays the int cap counter; `atlas_activity` is `{text, ts} | None` consistent between `_atlas_activity` (dispatcher) and `belt` (data); `guide`/`kill` return `{slug, guided/killed}`; the endpoints wrap them as `{result, slug, ...}`.
