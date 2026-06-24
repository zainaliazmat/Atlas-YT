# Atlas Supervisor — Slice 3 (Render Budget Policy + HyperFrames Escalation Card) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the final-render gate **autonomous under a budget rule**: when Atlas chooses `APPROVE_GATE(render)` and the render plan is **under** the CEO's budget, the executor honors it (self-approves → renders); **over** budget it converts the approval to an escalation carrying the render plan + the per-scene HyperFrames draft frames (the existing draft-preview card). The factcheck prohibition is untouched.

**Architecture:** The budget is a setting (`defaults.render_budget_sec`, a runtime ceiling — there is no $ cost in the pipeline, so estimated runtime is the proxy). The dispatcher gains `render_budget_sec` (injected; the dashboard passes the setting) and a pure `_render_under_budget` check that reads the render plan from `project.json`. The Slice-2 `APPROVE_GATE` branch (which currently always escalates) grows a render-specific path: under budget → `resume(final_render)`; over budget → escalate with the render-plan + draft-frames payload. The decider gets render-plan + budget context so its choice is informed. The draft-preview card itself already exists (the gate-detail endpoint serves `draft_renders`); this slice wires the autonomy and the over-budget payload.

**Tech Stack:** Python 3.12, pytest, stdlib. Reuses `atlas/dispatcher.py`, `atlas/supervisor.py`, `atlas/atlas_decider.py`, `atlas/dashboard/settings_store.py`. No new dependencies.

## Global Constraints

- **The factcheck prohibition is permanent and unchanged.** `APPROVE_GATE(factcheck)` still always escalates (Slice 2, `_escalate`). This slice adds budget logic **only** for `gate == "final_render"`.
- **Render auto-approves ONLY under the budget rule.** Over budget, `APPROVE_GATE(render)` is converted to `ESCALATE` in the **executor** (never trusted to the LLM). Under budget it is honored via `resume(slug, "final_render")`.
- **Budget metric = estimated runtime seconds.** The render plan in `project.json["gates"]["final_render"]["details"]` exposes `est_runtime_sec` (and `scenes`, `audio_duration_sec`) — there is no cost field. A render is **under budget** when `est_runtime_sec <= render_budget_sec`. Default `render_budget_sec = 600.0` (10 min): auto-approves the agency's normal short (~60–90s) and long (~5–8min) formats, escalates anomalously long renders. Configurable per deployment in settings; the CEO tightens it to force more oversight.
- **A safe default escalates.** If the budget value is missing/unreadable or the render plan has no `est_runtime_sec`, treat the render as **over budget** (escalate) — never auto-approve a render we can't size.
- **The over-budget escalation carries the card payload:** the render plan + the per-scene draft frame rel-paths (`scenes/scene-NN/renders/draft.mp4`), so the Needs-You tray / gate card (Slice 4 / existing gate endpoint) shows the HyperFrames preview.
- **Counters persist before acting** (Slice 2 invariant): an honored render approval counts against the per-video decision budget, persisted before `resume`.
- **LLM-decider failure still degrades safely.** The safe-default decider returns `ESCALATE` for a render block → the render parks for human sign-off (today's behavior). Auto-approval requires the LLM to affirmatively choose `APPROVE_GATE(render)` AND the budget to pass.
- **Dashboard wiring is a working-tree task.** `atlas/dashboard/app.py` is entangled with separate uncommitted chat/publish WIP (it imports untracked modules), so its render-budget wiring (Task 6) is applied + tested but committed only with that WIP; the committed belt keeps the safe-default decider. The engine tasks (1–5) commit cleanly.
- Run all commands from `atlas/`. Use the venv python `/home/zain-ali/Documents/YT-AGENTS/venv/bin/python3 -m pytest …` (fall back to `python3`). `atlas/` is on `sys.path`.
- Every commit message ends with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## File Structure

- `atlas/dashboard/settings_store.py` (modify) — add `defaults.render_budget_sec` to `DEFAULT_SETTINGS` + coerce it in `validate_settings`.
- `atlas/dispatcher.py` (modify) — `__init__` gains `render_budget_sec: float = 600.0`; `_render_under_budget(slug) -> bool`; `_render_plan_payload(slug) -> dict`; the `APPROVE_GATE` branch grows the render path; `_build_context` adds render context.
- `atlas/atlas_decider.py` (modify) — the decision prompt mentions the render-budget rule; `build_decision_prompt` includes the render plan + budget for a render gate.
- `atlas/dashboard/app.py` (modify, working-tree) — `_get_dispatcher` reads `defaults.render_budget_sec` from settings and passes it.
- Tests: `atlas/dashboard/tests/test_settings_api.py`, `atlas/tests/test_dispatcher.py`, `atlas/tests/test_atlas_decider.py`.

---

### Task 1: Render-budget setting

Add `render_budget_sec` to the settings defaults so the CEO can tune the render auto-approval ceiling.

**Files:**
- Modify: `atlas/dashboard/settings_store.py` (`DEFAULT_SETTINGS`, `validate_settings`)
- Test: `atlas/dashboard/tests/test_settings_api.py` (append)

**Interfaces:**
- Produces: `DEFAULT_SETTINGS["defaults"]["render_budget_sec"] = 600.0`; `validate_settings` coerces it to a non-negative float (invalid/absent → `600.0`).

- [ ] **Step 1: Read the current code**

Open `atlas/dashboard/settings_store.py`. Find `DEFAULT_SETTINGS` (the `defaults` dict has `target_length`, `voice`, `style_preset`, `intake_mode`) and the `validate_settings` function (it builds a coerced `defaults` block). You will add one field consistently in both.

- [ ] **Step 2: Write the failing tests**

Append to `atlas/dashboard/tests/test_settings_api.py` (match the file's existing import style — it already imports `settings_store`):

```python
def test_render_budget_default_is_present():
    from dashboard import settings_store
    assert settings_store.DEFAULT_SETTINGS["defaults"]["render_budget_sec"] == 600.0


def test_validate_coerces_render_budget_to_float():
    from dashboard import settings_store
    ok, errors, clean = settings_store.validate_settings(
        {"defaults": {"render_budget_sec": "300"}})
    assert ok, errors
    assert clean["defaults"]["render_budget_sec"] == 300.0


def test_validate_defaults_render_budget_when_missing_or_bad():
    from dashboard import settings_store
    _, _, clean = settings_store.validate_settings({"defaults": {}})
    assert clean["defaults"]["render_budget_sec"] == 600.0
    _, _, clean2 = settings_store.validate_settings(
        {"defaults": {"render_budget_sec": "not-a-number"}})
    assert clean2["defaults"]["render_budget_sec"] == 600.0
    _, _, clean3 = settings_store.validate_settings(
        {"defaults": {"render_budget_sec": -50}})
    assert clean3["defaults"]["render_budget_sec"] == 600.0   # negative is invalid
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m pytest dashboard/tests/test_settings_api.py -k render_budget -q`
Expected: FAIL — the key is absent / not coerced.

- [ ] **Step 4: Implement**

In `atlas/dashboard/settings_store.py`:
1. Add `"render_budget_sec": 600.0` to `DEFAULT_SETTINGS["defaults"]`.
2. In `validate_settings`, where the coerced `defaults` block is built, add a coercion that defaults to `600.0` on absence, non-numeric, or negative:

```python
        raw_budget = (defaults or {}).get("render_budget_sec", 600.0)
        try:
            budget = float(raw_budget)
            if budget < 0:
                budget = 600.0
        except (TypeError, ValueError):
            budget = 600.0
        # ... include in the cleaned defaults dict:
        #   "render_budget_sec": budget,
```

Wire `budget` into the cleaned `defaults` dict the function returns (alongside `target_length`, etc.). Match the function's existing construction style exactly.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest dashboard/tests/test_settings_api.py -q`
Expected: PASS (the 3 new tests + every pre-existing settings test unchanged).

- [ ] **Step 6: Commit**

```bash
git add atlas/dashboard/settings_store.py atlas/dashboard/tests/test_settings_api.py
git commit -m "feat(control-room): render_budget_sec setting (render auto-approval ceiling, default 600s)"
```

---

### Task 2: Dispatcher render-budget check + plan payload

The dispatcher gains the budget value and two pure helpers: is this render under budget, and the render-plan + draft-frames payload for the over-budget escalation card.

**Files:**
- Modify: `atlas/dispatcher.py` (`__init__`, add `_render_under_budget`, `_render_plan_payload`)
- Test: `atlas/tests/test_dispatcher.py` (append)

**Interfaces:**
- Produces:
  - `Dispatcher.__init__` gains `render_budget_sec: float = 600.0`; `self.render_budget_sec = render_budget_sec`.
  - `_render_under_budget(slug: str) -> bool` — reads `project.json["gates"]["final_render"]["details"]["est_runtime_sec"]`; returns `True` only when it is a number `<= self.render_budget_sec`; `False` on any missing/invalid value (safe default = escalate).
  - `_render_plan_payload(slug: str) -> dict` — `{"render_plan": <details dict>, "draft_renders": [rel-path strings], "budget_sec": float}`; `draft_renders` from globbing `{pdir}/scenes/scene-*/renders/draft.mp4` (project-relative posix), empty list if none.

- [ ] **Step 1: Write the failing tests**

Append to `atlas/tests/test_dispatcher.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_dispatcher.py -k "render_under_budget or render_over_budget or render_missing_runtime or render_plan_payload" -q`
Expected: FAIL — `__init__` rejects `render_budget_sec` (TypeError) / helpers absent.

- [ ] **Step 3: Implement**

In `atlas/dispatcher.py`, add `render_budget_sec: float = 600.0` as the LAST keyword arg of `__init__` and set `self.render_budget_sec = render_budget_sec`. Then add the helpers near `_build_context`:

```python
    def _render_under_budget(self, slug: str) -> bool:
        """True only when the render plan's estimated runtime is at/under the budget ceiling.
        Any missing/invalid size → False (we never auto-approve a render we cannot size)."""
        proj = self._load_project(slug)
        if proj is None:
            return False
        details = (proj.get("gates", {}).get("final_render", {}) or {}).get("details") or {}
        rt = details.get("est_runtime_sec")
        if not isinstance(rt, (int, float)):
            return False
        return float(rt) <= float(self.render_budget_sec)

    def _render_plan_payload(self, slug: str) -> dict:
        """The over-budget escalation card: render plan + per-scene HyperFrames draft frames."""
        proj = self._load_project(slug) or {}
        details = (proj.get("gates", {}).get("final_render", {}) or {}).get("details") or {}
        pdir = self._project_path(slug).parent
        drafts = sorted(p.relative_to(pdir).as_posix()
                        for p in pdir.glob("scenes/scene-*/renders/draft.mp4"))
        return {"render_plan": details, "draft_renders": drafts,
                "budget_sec": float(self.render_budget_sec)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_dispatcher.py -k "render_under_budget or render_over_budget or render_missing_runtime or render_plan_payload" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add atlas/dispatcher.py atlas/tests/test_dispatcher.py
git commit -m "feat(control-room): dispatcher render-budget check + draft-preview payload"
```

---

### Task 3: Executor — honor `APPROVE_GATE(render)` under budget, escalate over

Grow the Slice-2 `APPROVE_GATE` branch: for `gate == "final_render"`, under budget → self-approve via `resume`; over budget → escalate with the card payload. Factcheck (and any non-render gate) still escalates exactly as before.

**Files:**
- Modify: `atlas/dispatcher.py` (`_execute_decision` `APPROVE_GATE` branch)
- Test: `atlas/tests/test_dispatcher.py` (append)

**Interfaces:**
- Consumes: `_render_under_budget`, `_render_plan_payload`, `_over_decision_budget`, `resume`, `_escalate` (Slice 2/Task 2).
- Produces: `APPROVE_GATE(final_render)` honored (emits `gate_approved` initiator `atlas` via `resume`) under budget; escalated (`blocked`, gate `final_render`, payload carries `render_plan`/`draft_renders`) over budget.

- [ ] **Step 1: Write the failing tests**

Append to `atlas/tests/test_dispatcher.py`:

```python
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
    blocked = [e for e in d.events.since(0) if e["kind"] == "blocked"]
    assert blocked and blocked[-1]["gate"] == "factcheck"
```

> **The fake needs a `blocked_final` outcome + `final_runtime_sec`.** Extend `make_fake_produce` (Step 3a) so a stage outcome `"blocked_final"` makes the fake write a `blocked_at_final_render` project with a `gates.final_render.details.est_runtime_sec` (default e.g. 120, overridable via a new `final_runtime_sec` kwarg) and return `{"status": "blocked", "gate": "final_render", ...}`. Mirror the existing `transient`/`deterministic` outcome handling.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_dispatcher.py -k "approve_render or approve_factcheck_still" -q`
Expected: FAIL — `APPROVE_GATE(final_render)` currently always escalates (no self-approve path), and the fake has no `blocked_final` outcome.

- [ ] **Step 3a: Extend `make_fake_produce`**

In `atlas/tests/test_dispatcher.py`, add the `blocked_final` outcome to `make_fake_produce` (add a `final_runtime_sec=120` kwarg). When a stage's outcome is `"blocked_final"`, the fake writes the project's `status="blocked_at_final_render"` and `gates.final_render.details = {"working_title":"T","scenes":3,"est_runtime_sec":final_runtime_sec,"audio_duration_sec":final_runtime_sec}` to `project.json`, then returns `{"status":"blocked","gate":"final_render","stage":<key>,"reason":"awaiting render sign-off","slug":slug}`. Follow the existing pattern the fake uses for the `transient`/`deterministic` branches (writing `project.json` then returning the result dict).

- [ ] **Step 3b: Grow the `APPROVE_GATE` branch**

In `_execute_decision`, replace the Slice-2 `APPROVE_GATE` branch (which builds an ESCALATE for every gate) with the render-aware version:

```python
        if kind == "APPROVE_GATE":
            gate = decision.gate
            # HARD GUARANTEE: factcheck (and any non-render gate) is never approved away.
            if gate == "final_render":
                if self._render_under_budget(slug):
                    if self._over_decision_budget(slug):
                        return self._escalate(slug, result,
                            supervisor.Decision("ESCALATE", gate="final_render",
                                payload={"blocked": True},
                                reason="decision budget exhausted — escalating"))
                    self.events.emit("approving", slug=slug, gate="final_render",
                                     initiator="atlas",
                                     message="render under budget — self-approving")
                    self._retries.pop(slug, None)
                    self.resume(slug, "final_render", initiator="atlas")
                    return
                # over budget → escalate with the HyperFrames draft-preview card payload
                payload = {"blocked": True, **self._render_plan_payload(slug)}
                return self._escalate(slug, result,
                    supervisor.Decision("ESCALATE", gate="final_render", payload=payload,
                        reason=decision.reason or "render over budget — your call"))
            # every other gate (factcheck) escalates, exactly as Slice 2.
            return self._escalate(slug, result,
                supervisor.Decision("ESCALATE", gate=decision.gate, payload={"blocked": True},
                    reason=decision.reason or "gate needs your sign-off"))
```

> `_escalate` already clamps the gate to `LEGAL_GATES` and emits `blocked` with the payload. Confirm the emitted `blocked` event includes the `payload` kwarg (it should pass `payload=...`); if `_escalate` does not currently forward a `payload` field on the event, add `payload=payload` to its `events.emit("blocked", …)` call so the card data reaches the UI. Keep the factcheck/no-payload path behavior-identical (an empty/absent payload is fine).

- [ ] **Step 4: Run the new tests, then the full dispatcher suite**

Run: `python3 -m pytest tests/test_dispatcher.py -q`
Expected: PASS — the 3 new tests AND every pre-existing dispatcher test (the factcheck path is unchanged).

- [ ] **Step 5: Commit**

```bash
git add atlas/dispatcher.py atlas/tests/test_dispatcher.py
git commit -m "feat(control-room): render gate autonomous under budget, draft-card escalation over budget"
```

---

### Task 4: Render-gate decision context

Give the decider the render plan + the budget so its `APPROVE_GATE(render)` vs `ESCALATE` choice is informed (not a blind guess).

**Files:**
- Modify: `atlas/dispatcher.py` (`_build_context`)
- Modify: `atlas/atlas_decider.py` (`build_decision_prompt`, the system prompt's render note)
- Test: `atlas/tests/test_dispatcher.py`, `atlas/tests/test_atlas_decider.py` (append)

**Interfaces:**
- Produces: `_build_context` adds, for `gate == "final_render"`, `render_plan` (the details dict) and `render_budget_sec`; `build_decision_prompt` surfaces them in the user brief.

- [ ] **Step 1: Write the failing tests**

Append to `atlas/tests/test_dispatcher.py`:

```python
def test_build_context_includes_render_plan_for_render_gate(tmp_path):
    fake, _ = make_fake_produce()
    d = Dispatcher(projects_dir=tmp_path, produce_fn=fake, render_budget_sec=450.0)
    _write_final_render_project(tmp_path, "vid", est_runtime_sec=200)
    ctx = d._build_context("vid", {"status": "blocked", "gate": "final_render"})
    assert ctx["render_plan"]["est_runtime_sec"] == 200
    assert ctx["render_budget_sec"] == 450.0
```

Append to `atlas/tests/test_atlas_decider.py`:

```python
def test_build_prompt_surfaces_render_budget():
    result = {"status": "blocked", "gate": "final_render", "stage": "render"}
    ctx = {"attempts": 0, "max_retries": 1, "fix_attempts": {}, "decisions": 0,
           "flagged_claims": [], "history": [],
           "render_plan": {"scenes": 5, "est_runtime_sec": 200}, "render_budget_sec": 450.0}
    system, user = atlas_decider.build_decision_prompt("vid", result, ctx)
    assert "200" in user and "450" in user
    assert "budget" in system.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_dispatcher.py -k build_context_includes_render tests/test_atlas_decider.py -k "render_budget or build_prompt_surfaces" -q`
Expected: FAIL — render context not built; prompt has no render/budget mention.

- [ ] **Step 3a: Extend `_build_context`**

In `atlas/dispatcher.py` `_build_context`, after the factcheck branch, add:

```python
        if result.get("gate") == "final_render":
            proj2 = proj if proj is not None else {}
            details = (proj2.get("gates", {}).get("final_render", {}) or {}).get("details") or {}
            ctx["render_plan"] = details
            ctx["render_budget_sec"] = float(self.render_budget_sec)
```

- [ ] **Step 3b: Surface it in the prompt**

In `atlas/atlas_decider.py` `build_decision_prompt`, add the render fields to the `brief` dict when present:

```python
        "render_plan": context.get("render_plan") or {},
        "render_budget_sec": context.get("render_budget_sec"),
```

And add one line to the `_SYSTEM` prompt near the `APPROVE_GATE` description, e.g.:
`For a final_render block: APPROVE_GATE(final_render) only when the render plan's est_runtime_sec is within the stated render budget; otherwise ESCALATE so the CEO sees the draft-preview card.`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_dispatcher.py -k build_context_includes_render -q` then `python3 -m pytest tests/test_atlas_decider.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add atlas/dispatcher.py atlas/atlas_decider.py atlas/tests/test_dispatcher.py atlas/tests/test_atlas_decider.py
git commit -m "feat(control-room): render-plan + budget context for the decider"
```

---

### Task 5: Engine regression gate

Prove the engine-side render-budget work is green across the dispatcher + decider + supervisor + settings suites.

**Files:** none (verification only).

- [ ] **Step 1: Run the engine suites**

Run: `python3 -m pytest tests/test_dispatcher.py tests/test_atlas_decider.py tests/test_supervisor.py dashboard/tests/test_settings_api.py -q -p no:cacheprovider`
Expected: PASS (all green; the dispatcher suite may take ~15s).

- [ ] **Step 2: Report** the totals and confirm the factcheck-prohibition tests still pass (grep the run for `approve_factcheck` / `factcheck_fix_capped`).

---

### Task 6 (working-tree): wire the budget from settings into the dashboard dispatcher

Read `defaults.render_budget_sec` from settings and pass it when building the belt. This file is entangled with uncommitted chat/publish WIP — apply + verify, leave the commit to that workstream (note it in the report).

**Files:**
- Modify (working-tree, do not commit if it sweeps unrelated WIP): `atlas/dashboard/app.py` (`_get_dispatcher`)
- Test: the full suite (verification).

**Interfaces:**
- Produces: `_get_dispatcher` passes `render_budget_sec=settings["defaults"]["render_budget_sec"]` to the `Dispatcher`.

- [ ] **Step 1: Implement**

In `atlas/dashboard/app.py` `_get_dispatcher`, read the budget from settings and pass it:

```python
        try:
            import dashboard.settings_store as _ss
            _budget = float(_ss.load_settings(app.state.settings_path)["defaults"]
                            .get("render_budget_sec", 600.0))
        except Exception:  # noqa: BLE001 — settings unreadable → safe default
            _budget = 600.0
        app.state.dispatcher = dmod.Dispatcher(
            ...,                                  # existing args (produce_fn/decide_fn/etc.)
            render_budget_sec=_budget)
```

- [ ] **Step 2: Verify (full suite)**

Run: `python3 -m pytest tests/ dashboard/tests/ -q -p no:cacheprovider`
Expected: green except the known-flaky SSE test `dashboard/tests/test_belt_api.py::test_event_stream_backfills_then_stops_on_disconnect` (confirm in isolation). No new failures from the budget wiring.

- [ ] **Step 3: Report** whether `app.py` could be committed in isolation. If it sweeps unrelated chat/publish WIP or imports untracked modules, leave it in the working tree and document that the render-budget dashboard wiring lands with the chat/publish slice. Commit ONLY if `git diff app.py` is exclusively the render-budget change.

---

## Self-Review

**Spec coverage (Slice 3, design doc §1 "Render gate → autonomous under a budget rule" + §4 escalation card):**
- Budget rule in settings → Task 1. ✓
- `APPROVE_GATE(render)` honored under budget, else the draft-preview card → Task 3 (executor) + Task 2 (budget check + payload). ✓
- Gate-scoped: NEVER legal for factcheck → Task 3 keeps the factcheck escalation path unchanged (`test_approve_factcheck_still_escalates_unchanged`). ✓
- HyperFrames draft-preview card (per-scene draft frames + render plan) → Task 2 `_render_plan_payload` + Task 3 over-budget payload; served by the existing `/api/media/{slug}/draft/{rel}` + gate-detail endpoint. ✓
- Decider informed by the render plan + budget → Task 4. ✓
- Over budget always escalates; budget enforced in the executor not the LLM → Task 3. ✓
- Safe default (can't size → escalate; LLM down → escalate) → Task 2 `_render_under_budget` + the safe-default decider. ✓

**Deferred (correctly):** the Needs-You tray UI + live feed + digest (Slice 4 — this slice emits the `blocked` event with the card payload they consume); request-path unify (Slice 5). The card UI itself already exists (gate-detail `draft_renders`); Slice 4 surfaces it in the tray.

**Placeholder scan:** none — every step has exact code, commands, expected output. Task 1 Step 4 and Task 6 reference the surrounding function's existing construction style (read-and-match) because those functions carry unrelated fields; the field to add is given verbatim.

**Type consistency:** `render_budget_sec` (float) consistent across settings (`defaults.render_budget_sec`), `Dispatcher.__init__`, `_render_under_budget`, `_render_plan_payload`, `_build_context` (`render_budget_sec`/`render_plan`), the decider prompt, and the dashboard wiring. `_render_plan_payload` returns `{render_plan, draft_renders, budget_sec}`; the escalation event payload merges it with `{blocked: True}`. The `blocked_final` fake outcome + `final_runtime_sec` kwarg are consistent between Task 3's tests and the helper extension.
