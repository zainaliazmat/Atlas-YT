# RESTRUCTURE_PLAN.md — Atlas-only, chat-driven architecture

**Branch:** `atlas-only` (to be created off `control-room`)
**Goal:** Atlas becomes the SOLE orchestrator, reachable ONLY through a chat UI. Delete the
deterministic pipeline and the dashboard. The end-to-end video flow becomes a PLAYBOOK in
Atlas's system prompt; progress is tracked in a lightweight per-project manifest. The
specialist engines and their determinism are untouched.

This is a plan only. Nothing is deleted or edited until you say **go**.

---

## 0. What I read (the actual coupling map)

Core Atlas modules and their pipeline/dashboard coupling, as they exist today:

| Module | Role today | Coupling to remove |
|---|---|---|
| `run.py` | CLI entry: `chat`, one-shot niche, **`produce` subcommand** | `produce` → `pipeline.produce` |
| `orchestrator.py` | Atlas's brain (SDK `query` loop) + system prompt | system prompt's **PRODUCTION PLAYBOOK** points at `produce_video`; two-gate "sacred" contract assumes the pipeline tool |
| `tools.py` | Generates agent tools from registry | **`_make_produce_tool` / `produce_video`** + its registration in `build_server` |
| `pipeline.py` | The deterministic state-machine spine | **DELETE entirely** |
| `session.py` | UI-neutral session core | **`approve_gate`, `latest_blocked_project`, `_pipeline_produce`, `produce_fn`** injection, `project_view` import |
| `project_view.py` | Read-only gate previews for the web UI | gate-card detection (`find_latest_blocked`, `gate1/gate2_preview`) |
| `web/app.py` | Chainlit chat UI | **gate buttons** (`_maybe_show_gate`, `_show_*_gate`, `approve_gate`/`revise_gate` callbacks, `_gate_actions`) |
| `chat.py` | Terminal REPL (thin frontend over session) | `/agents` text mentions `produce_video`; otherwise clean |
| `dispatcher.py` | Assembly-line "belt" over `pipeline.produce` (control-room) | built ON pipeline + dashboard — **DELETE** |
| `supervisor.py` | Decision seam for the belt | belt-only — **DELETE** |
| `atlas_decider.py` | LLM decider for the belt | belt-only — **DELETE** |
| `adapters/*.py` | Wrap sibling engines | each `_resolve_project_dir` uses **`pipeline.PROJECTS_DIR` + `pipeline._slug`** (topic-heuristic) |
| `contracts/__init__.py` | Frozen schemas | only **docstring** mentions of "pipeline" — no real import. KEEP. |
| `eval/loop.py` | Coach write-boundary guard | line 49 lists `pipeline.py` as a denied write path — update the list |
| `dashboard/` | FastAPI monitoring UI (19 test files) | **DELETE entire dir** |

**Out of scope / untouched:** `studio/` and `studio-cli` (the newer v2 spine at repo root —
a separate project), `eval/`, `rubric/`, the 6 specialist projects + engines, `registry.py`,
`adapters/loader.py`, `adapters/base.py`, the contract schemas.

---

## (a) Files / directories to DELETE

**Dashboard (Phase 2):**
- `atlas/dashboard/` — entire directory (app, server, data, chat, intake, publish, media,
  security, settings_store, atlas_request, static/, REPORT.md, requirements.txt, and
  `dashboard/tests/` incl. `e2e/` — 19 test files + Playwright).
- Repo-root launchers named `yt-atlas` and `yt-agents-dashboard.html` — **NOTE: neither
  exists in the tree today** (already absent). I'll confirm and remove only if present.
- `studio-cli` is a *studio* launcher, NOT the dashboard — left untouched.

**Pipeline + belt (Phase 3):**
- `atlas/pipeline.py`
- `atlas/dispatcher.py` (assembly-line belt — exists only to drive `pipeline.produce`)
- `atlas/supervisor.py` (decision seam for the belt)
- `atlas/atlas_decider.py` (LLM decider for the belt)

**Tests that die with the above (Phase 5/6):**
- `tests/test_pipeline.py`, `tests/test_produce_tool.py`, `tests/test_dispatcher.py`,
  `tests/test_supervisor.py`, `tests/test_atlas_decider.py`
- `tests/test_project_view.py` (if `project_view.py` is deleted — see (e))
- Partial edits (not deletion): `tests/test_session.py` (drop gate/approve cases),
  `tests/test_orchestration_tools.py` (drop `produce_video` cases),
  `tests/test_showrunner_registry.py`, `tests/test_research_producer.py`,
  `tests/test_scriptwriter_roundtable.py`, `tests/test_eval_loop.py` (update denied-path list),
  `tests/test_chat_state_atomic.py` (if it touches pipeline helpers).

---

## (b) Couplings to SEVER (explicit)

1. **`run.py` `produce` subcommand → pipeline.** Remove `run_produce()` and the `produce`
   branch in `main()`. `run.py` becomes: launch chat UI + minimal help.
2. **`tools.produce_video` → pipeline.** Remove `_make_produce_tool` and its registration
   in `build_server`. Keep all per-job tools + `ask_*` tools. (Keep `configure_logging`.)
3. **`session.approve_gate` / `latest_blocked_project` / `_pipeline_produce` → pipeline.**
   Remove these three + the `produce_fn` constructor arg + the `project_view` import.
   Session keeps: streaming `send`, `ask_agent`, distill/summarize/memory, `AgentSession`,
   `SessionRegistry`.
4. **`web/app.py` gate buttons → pipeline.** Remove `_maybe_show_gate`, `_show_factcheck_gate`,
   `_show_render_gate`, `_gate_actions`, `_approve_summary`, the `approve_gate`/`revise_gate`
   `@cl.action_callback`s, the `project_view` import, and the `_maybe_show_gate` calls in
   `on_chat_start` / `on_message`. (The Marlow `/write` button seam is independent of the
   pipeline — see (e) for its fate.)
5. **`orchestrator.py` system prompt → pipeline tool.** Replace the `PRODUCTION PLAYBOOK`
   section (which calls `produce_video` and describes pause/resume gates) with the new
   tool-call PLAYBOOK (see (c)).
6. **`adapters/*._resolve_project_dir` → `pipeline.PROJECTS_DIR` / `pipeline._slug`.** Relocate
   `PROJECTS_DIR` + `slugify` into a new small module `atlas/projects.py` (the manifest module,
   see (d)). Adapters import from there; the topic-heuristic `_resolve_project_dir` is replaced
   by explicit-slug resolution (see (d)).
7. **`eval/loop.py` denied-write list → `pipeline.py`.** Replace the `pipeline.py` entry with
   the new manifest/playbook module path(s) so the coach still cannot write the spine.

---

## (c) The Atlas PLAYBOOK (replaces the pipeline state machine)

Lives in `orchestrator.py`'s `ORCHESTRATION_CONTRACT`, as prose Atlas executes by calling the
generated agent tools in order against ONE project workspace. Canonical sequence:

```
research (Sage)  →  script (Marlow)  →  factcheck (Sage) ★checkpoint
  →  style + storyboard (Iris)  →  assets (Magpie)
  →  narration (Cadence)  →  compose (Mason)  →  audiomix (Cadence)  →  render (Mason)
```

(The creative-architecture sub-stages — treatment → narrative_intent → motion_mood_board —
remain available as Iris jobs; the playbook notes they are optional and run before script when
a richer creative pass is wanted. They are additive and a missing artifact leaves downstream
on prior behavior, exactly as today.)

**Playbook rules baked into the prompt:**
- **Start every video by calling `start_project` with the brief** → it returns a `slug`. Pass
  that `slug` to every subsequent job so all artifacts accumulate in `projects/<slug>/`.
- **Consult the manifest** (`project_status(slug)`) to know what's done and resume without
  re-doing or skipping a step.
- **Fact-check is a CONVERSATIONAL checkpoint.** After `sage_factcheck`, Atlas reads
  `factcheck_report` and tells the CEO the verdict + flagged claims. **A `block` verdict MUST
  route back to the script** (`scriptwriter_write_script` to revise the flagged claims) and
  re-run `sage_factcheck`. It is NEVER approved away — strongest possible wording: *"You would
  rather kill a video than narrate an unverified claim. A `block` is not a gate you can sign
  off; the only path forward is fix-the-script-and-recheck."*
- **Before the final render, pause and ask the CEO to proceed** (plain conversational
  approval — present the draft/plan, wait for a yes).
- **Deviation is allowed and expected.** Partial/iterative asks ("just research X", "rewrite
  scene 3", "re-render the composition") → call only the relevant job(s) against the active
  slug. Atlas is a manager, not a fixed pipeline.
- **Determinism for rendered output is the engines' job** — unchanged; the playbook never asks
  a specialist to be non-deterministic.
- Keep the existing transparency contract (announce decisions; status lines self-emit) and the
  research/Scout default playbook already in the prompt.

The two "sacred gate / pause-and-resume" machinery language is removed; only the fact-check
checkpoint (with the un-approvable `block`) and the pre-render confirmation survive, as
conversational behaviors.

---

## (d) The per-project manifest + artifact flow

**New module `atlas/projects.py`** (small; ~the manifest helpers carved out of pipeline.py):
- `PROJECTS_DIR = atlas/projects/`
- `slugify(text)` (the old `_slug`)
- `start_project(brief, *, slug=None) -> {slug, project_dir}` — mints `projects/<slug>/` and
  writes `project.json` as a **lightweight checklist manifest**:
  ```json
  {
    "schema_version": "...", "project_id": "...", "slug": "...",
    "brief": "...", "topic": "...", "created": ..., "updated": ...,
    "artifacts": {
      "research_brief":   {"status": "pending", "path": null},
      "script":           {"status": "pending", "path": null},
      "factcheck_report": {"status": "pending", "path": null, "verdict": null},
      "style_guide":      {"status": "pending", "path": null},
      "storyboard":       {"status": "pending", "path": null},
      "asset_manifest":   {"status": "pending", "path": null},
      "narration":        {"status": "pending", "path": null},
      "composition":      {"status": "pending", "path": null},
      "render":           {"status": "pending", "path": null}
    }
  }
  ```
  This is a **checklist, not a state machine** — no stage ordering, no gate state, no
  blocked_at_*; just done/pending + path per artifact (+ the factcheck verdict so a `block` is
  visible on resume).
- `mark_artifact(slug, name, path, **extra)` — flip one checklist entry to done.
- `manifest(slug)` / `project_dir(slug)` / `resolve_active(slug)` helpers.

It reuses `contracts/project.schema.json` (already `additionalProperties: true`, with `stages`
required — I'll either relax `stages`→optional in the schema or keep a minimal `stages: {}` for
back-compat; decided in build, noted in (f)).

**Slug wiring (tools.py + adapters):**
- `tools._make_job_tool` injects a uniform optional **`slug`** property into every job tool's
  schema (strongly described: "the active project slug from `start_project`; all artifacts read
  from and write to `projects/<slug>/`"), and passes `slug` into `adapter.run_job(..., slug=…)`.
  Registry `JobSpec.params` stay domain-focused (topic/angle/etc.) — slug is added at the tool
  boundary, so no per-entry registry churn.
- Each adapter's `run_job(self, job_name, progress, *, slug=None, **params)` resolves
  `pdir = projects.project_dir(slug)` and calls the SAME producer logic it already has
  (`run_research`/`run_write`/`run_factcheck`/`produce_*`) against that `pdir`, then
  `projects.mark_artifact(...)`. The topic-heuristic `_resolve_project_dir` is removed.
  - **Notable convergence:** today the *conversational* `sage_research` writes to the sibling's
    `research_packs/` and does NOT drop `research_brief.json` into a project. Under the new
    design it writes `research_brief.json` into `projects/<slug>/` (same as the old pipeline
    producer), so a sequence of delegations genuinely accumulates one video.
- If `slug` is omitted on a job that needs upstream artifacts, the tool returns a readable
  coaching message ("start a project first / pass the active slug"), never crashes.

**New tools exposed to Atlas (registered in `build_server`):**
- `start_project(brief, slug?)` → returns the slug.
- `project_status(slug)` → returns the checklist (resumability).
- `validate_artifact(name, slug)` → see (f).

---

## (e) The chat UI: what's kept, what's removed

**Kept as the single interface: `web/app.py` (Chainlit).** Remove only the gate machinery
listed in (b.4). Keep: streaming meeting turns, per-agent persona profiles, `/help` `/agents`
`/summary` `/new`, memory lifecycle (park/distill on disconnect).

**Marlow `/write` button seam** (`_marlow_write`, `_make_button_approver`, `_run_gated_write`,
`_resolve_brief_path`, `_marlow_chat`): this is a *scriptwriter-engine* approval gate, NOT the
pipeline. It's independent and harmless. **Recommendation: KEEP it** (it's a nice in-chat
affordance and adds no pipeline coupling). Flagged for your call — say the word and I'll remove
it for maximum minimalism.

**Terminal REPL `chat.py`:** keep as an optional dev fallback over the same session core (only
edit: drop the `produce_video` mention in `/agents` text). It adds no pipeline coupling once
session is decoupled. If you'd rather go **terminal-only**, tell me and I'll delete `web/`
instead and keep `chat.py`.

**`project_view.py`:** its only consumers are the gate UI + session.latest_blocked_project,
both being removed. It offers no in-chat artifact preview beyond gates. **Recommendation:
DELETE it** (and `tests/test_project_view.py`). If you want lightweight in-chat artifact
previews later, we can reintroduce a tiny read-only viewer then.

---

## (f) What happens to `contracts/`

**KEEP `contracts/` as-is.** The schemas are still the source of truth for artifact shape; the
specialist adapters already stamp `schema_version` and the engines emit the frozen shapes.

- The mandatory per-stage validation that lived in `pipeline._run_stage` is removed (no spine).
- Replace it with **one optional tool `validate_artifact(name, slug)`** that Atlas MAY call to
  sanity-check any artifact in `projects/<slug>/` against its frozen schema (wrapping
  `contracts.validate`). Not mandatory, not a gate — a tool Atlas can reach for.
- `contracts/project.schema.json` currently `require`s `stages`. The new manifest is
  checklist-shaped (`artifacts` map, no `stages`). Fix: relax `stages` to optional in the
  schema (additive, back-compat since `additionalProperties: true`). The other artifact schemas
  are unchanged.
- I am **not** moving `contracts/` to `legacy/` — keeping it in place is cleaner and the
  `validate_artifact` tool gives it a live purpose.

---

## Phased execution (after you approve)

- **P2 Delete dashboard:** rm `atlas/dashboard/`; confirm no non-dashboard module imports it
  (only `dispatcher.py`/`supervisor.py`/`atlas_decider.py` do, and those die in P3).
- **P3 Remove pipeline + belt; make Atlas the orchestrator:** rm `pipeline.py`,
  `dispatcher.py`, `supervisor.py`, `atlas_decider.py`; add `projects.py`; rewire `tools.py`
  (drop `produce_video`, add `start_project`/`project_status`/`validate_artifact`, inject slug);
  rewire adapters to explicit slug; install the PLAYBOOK in `orchestrator.py`; fix `run.py`.
- **P4 One chat UI:** strip gate machinery from `web/app.py`; decouple `session.py`; delete
  `project_view.py`; trim `chat.py`.
- **P5 Clean up:** dead imports, tests, `run.py` entry points, `eval/loop.py` denied-path list,
  `atlas/README.md` + CHANGELOG.
- **P6 Verify:** run remaining tests; launch chat UI; smoke-test (1) single delegated job and
  (2) full playbook reaching the fact-check checkpoint with a `block` routing back to Marlow.

**Launch command after the restructure:** `chainlit run web/app.py -w` (from `atlas/`), or
`python run.py chat` for the terminal fallback.

---

## Decisions I need from you — explained in plain words

Reply with your pick for each (e.g. "1: both, 2: keep, 3: leave"), or just say **go** and I'll
use the recommended ★ default for every one.

### 1. Which chat box keeps being the way you talk to Atlas?
There are two front doors into the SAME Atlas brain: a **web page** (opens in your browser,
looks like ChatGPT — the file `web/app.py`) and a **terminal chat** (you type into the black
command-line window — the file `chat.py`). Pick which to keep:
- **★ Keep both** — browser is the main one; the terminal stays as a quick dev/testing backup. (safest)
- **Web only** — keep the browser page, delete the terminal chat.
- **Terminal only** — keep the terminal chat, delete the whole `web/` browser UI.

### 2. Keep Marlow's little "✅ Write it" button in the web chat?
When you chat with Marlow (the scriptwriter) directly in the browser and ask for a script, a
small **Approve / Not-now button** pops up before he writes. It is NOT part of the pipeline
we're deleting — it's a separate, harmless convenience.
- **★ Keep the button** — nice affordance, adds no pipeline coupling.
- **Remove the button** — strip it out for a barer, more minimal codebase.

### 3. The `project_view.py` file (only used to draw the old gate cards)
This file exists only to render the dashboard/web "gate" approval cards we're removing. Nothing
else uses it.
- **★ Delete it** (and its test) — it has no job left once gates are gone.
- **Keep it** — only if you want me to repurpose it later for in-chat artifact previews.

### 4. Also delete the extra unused parts, or leave them?
Your system has side-features that aren't needed to make a video: the **eval/ + rubric/**
folders (an automatic quality-scoring/self-grading system) and some **off-pipeline agents**
(Scout = topic finder, Vera = reference analyzer, Quill & Flux = writing/production coaches).
None of them block this restructure.
- **★ Leave them untouched** — do only the pipeline + dashboard removal you asked for; prune
  these later if ever. (less risky)
- **Prune them now too** — also rip out eval/, rubric/, and those agents in this same pass.
  (bigger, more destructive)

---

Say **go** to proceed (★ defaults for anything you don't override), or **"skip the
checkpoint"** to proceed AND not stop for approval on future restructures.
