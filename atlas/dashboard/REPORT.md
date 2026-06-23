# YT-AGENTS Control Room — Dashboard build report

An **additive, read-mostly** monitoring dashboard over the Showrunner pipeline. It
turns the approved `yt-agents-dashboard.html` prototype into a real, tested service
wired to live project data, with one sanctioned write (gate approval) that goes
through the existing pipeline seam. Nothing in the engine, pipeline, contracts,
gates, registry, session, or Chainlit UI was modified — the whole deliverable lives
in the new `atlas/dashboard/` package.

---

## 1. How to run it

```bash
cd atlas
../venv/bin/pip install -r dashboard/requirements.txt      # fastapi/uvicorn/httpx/pytest-playwright
../venv/bin/python -m playwright install chromium          # once, for E2E

# serve the Control Room over the REAL projects dir (atlas/projects):
../venv/bin/python -m dashboard.server                     # http://127.0.0.1:8848
#   or: ../venv/bin/uvicorn dashboard.app:app
#   --port N   --projects <dir>   to point at a different projects dir
```

Open `http://127.0.0.1:8848/` — the six Control Room screens (+ the gate screen)
render from real data. API is browsable at `/api/docs`.

### Tests
```bash
cd atlas
../venv/bin/python -m pytest dashboard/tests/test_api.py \
      dashboard/tests/test_security.py dashboard/tests/test_gate_write_real.py -q   # 39 passed
../venv/bin/python -m pytest dashboard/tests/e2e/ -q                                # 12 passed (Playwright)
```

---

## 2. Architecture chosen (and why)

A small **FastAPI** service that reads real system state and serves typed JSON +
the static Control Room UI. Run from the `atlas/` working dir exactly like the
existing `python -m eval.inspector`, so sibling modules import top-level
(`registry`, `pipeline`, `contracts`, `chat_state`, `rubric`, `eval`).

```
atlas/dashboard/
  app.py        FastAPI factory + routes (7 GET screens, 3 media, 1 write, /healthz)
  data.py       read-only data-access: real state -> each screen's JSON (the keystone)
  media.py      range-streamed video/draft media + whitelisted artifact JSON serving
  security.py   path-traversal containment + secret/abs-path redaction + provider resolve
  server.py     uvicorn entrypoint (python -m dashboard.server)
  static/       the approved prototype, refactored: index.html + styles.css + app.js
  tests/        fixtures.py + test_api.py + test_security.py + test_gate_write_real.py
  tests/e2e/    Playwright: conftest.py (live server fixture) + test_e2e.py
  requirements.txt
```

**Why this shape**
- *Reuse, don't reimplement.* Every value comes from a module the spine already
  owns: `registry.REGISTRY` (fleet/agents/jobs), `project.json` (status/stages/gates/
  history), `contracts.validate` (artifact validity badges), `pipeline.STAGES`
  (canonical stage→role/label order, lazy-imported like `session._pipeline_produce`),
  `rubric` + eval `eval_scorecard.json` (Quality).
- *Read-mostly is structural.* The ONLY mutation is `POST /api/gate/{slug}/approve`,
  which delegates to `session.AtlasSession.approve_gate` → `pipeline.produce(slug,
  approve=[gate])`. The gate logic (incl. a fact-check `block` that can never be
  approved away) runs in the spine; the dashboard only refuses to *offer* an approve
  the spine would reject.
- *Frontend = the approved design, wired.* `styles.css` is the prototype's `<style>`
  block verbatim; `index.html` is the prototype body with data cells replaced by
  mount points; `app.js` is vanilla `fetch`+render per screen. No redesign, no
  framework, no build step.

### Subagent decomposition & parallelism map
The main thread held all the data-shape context and built the **backend keystone**
itself (fastest path, lowest integration risk), then fanned out isolated-context
subagents over **distinct file areas** (no collisions), stitching results:

| Wave | Workstream | Owner | Files |
|---|---|---|---|
| 0 | Ground-truth read + backend keystone + smoke-test | main | `data/media/security/app/server.py` |
| 1 ∥ | Fixtures + API/security pytest | subagent A | `tests/fixtures.py,test_api.py,test_security.py` |
| 1 ∥ | Frontend wiring (preserve design) | subagent B | `static/*` |
| 2 | Real gate-write E2E (real spine) + `_get_session` hardening | main | `tests/test_gate_write_real.py`, `app.py` |
| 3 ∥ | Playwright E2E (live server, disposable dir) | subagent C | `tests/e2e/*` |
| 3 ∥ | Security/leak/edge audit (read-only) | subagent D | (report only) |
| 4 | Fix audit findings + full regression + report | main | `security/media/data/app.py` |

Concurrency cap was ~2 active subagents at a time; integration was serialized in the
main thread. Subagents returned distilled reports (module + test counts + risks), not
transcripts.

---

## 3. Screen → real-data-source mapping

| Screen | Endpoint | Real source |
|---|---|---|
| Overview (Mission Control) | `GET /api/overview` | merged: project.json statuses + counts, `registry` fleet, latest-blocked detection, merged `history` activity, latest scorecard (if any) |
| Projects | `GET /api/projects` | every `projects/*/project.json` + scene/runtime from script/audio manifests + rollup counts (block vs needs-you split by fact-check verdict) |
| Pipeline detail | `GET /api/projects/{slug}` | stage ladder from `pipeline.STAGES`×project stages, gate states, `contracts.validate` badges, artifact file inventory, video presence |
| Fleet | `GET /api/fleet` | all 10 `registry.REGISTRY` agents, real effective provider, live status, **job counts derived from the pipeline stages each agent actually ran on disk** |
| Agent profile | `GET /api/agents/{name}` | generalized to ANY agent: soul bundle (SOUL/STYLE/examples), real provider+model+switch, registry jobs, real recent jobs, owned rubric bands |
| Quality | `GET /api/quality` | `eval_scorecard.json` if present, else **degrades** to "no scorecard yet" while still showing the frozen `rubric` standard + the eval tracking ledger |
| Gate | `GET /api/gate/{slug}` | fact-check verdict/flags/verified claims (gate 1) or render plan + draft renders + palette (gate 2); `approvable`/`hard_block` flags |
| Media | `GET /api/media/{slug}/video`, `/draft/{rel}`, `GET /api/artifact/{slug}/{name}` | range-streamed video/drafts; whitelisted, contract-validated, redacted artifact JSON |
| Gate write | `POST /api/gate/{slug}/approve` | `session.approve_gate` → `pipeline.produce` |

**Effective provider is real, not guessed:** each agent's `<NAME>_LLM` env switch is
read the same way its sibling `llm.py` reads it (e.g. `SAGE_LLM`, `MARLOW_LLM`,
`MASON_LLM`), without importing/booting the heavy engine. Scout+Sage correctly share
`SAGE_LLM`.

**Recent jobs are real, engine-free:** rather than booting each agent's engine, the
dashboard derives "what has this agent done" from the pipeline stages it actually ran
across all on-disk projects (e.g. Marlow ← every `script` stage). Agents that aren't
pipeline stages (Scout, Vera, the two coaches) correctly show zero pipeline jobs.

---

## 4. Autonomous decisions & improvements added

1. **Generalized to all 10 registry agents** (the prototype hard-coded 8 and only
   detailed Marlow). The agent screen renders any agent's soul bundle, provider,
   jobs, and real recent work.
2. **Real recent-jobs from on-disk stages** instead of importing engines — keeps the
   service light (no engine boot, ~2.8 s import) and fully read-only.
3. **Final-render gate variant** implemented (render plan + palette + draft renders),
   not just the fact-check gate.
4. **`block` vs `flags` rendered correctly**: a hard fact-check `block` is shown
   un-approvable/routed-back; a clean-but-gated verdict is approvable. The approve
   endpoint refuses to call the spine for a `block`.
5. **Quality degrades gracefully**: no scorecard exists in the real data yet (the eval
   layer is a separate track), so the screen shows a clear empty state but still
   renders the CEO-owned rubric standard + the tracking ledger. If/when
   `eval_scorecard.json` appears, the same screen lights up with real scores.
6. **Write-path isolation hardening** (see §6, the C1/`_get_session` fixes): the
   dashboard never touches the real `chat_state.json` and never boots the orchestrator.
7. **Working search/filter, switch-project/agent, artifact JSON viewer, streamed video
   playback** wired in the frontend; presentational-only buttons (New production,
   Re-render, Run inspector, Send-back, Ask-agent) are intentionally inert because no
   sanctioned endpoint exists — surfaced but not mutating.

---

## 5. Testing & QA results

- **API + security pytest: 39 passed** (`test_api.py`, `test_security.py`,
  `test_gate_write_real.py`). Covers every endpoint shape, count consistency, all 10
  agents, graceful degradation, corrupt/missing artifacts (no 500), unknown slug/agent
  (404), empty system, video Range (206 + correct `Content-Range`), traversal refusal,
  leak scan, and the gate write path (fake + real spine).
- **Playwright E2E: 12 passed** (headless chromium, ~58 s) against a real uvicorn
  server over a disposable projects dir, **zero console/page errors** in any test:
  rail nav + cross-links; every screen with real data; pipeline detail with `<video>`;
  10 fleet cards; ≥3 generalized agent profiles incl. a coach; Quality empty-state +
  rubric; **the real gate-flow approval transitioning `e2e-final-render` →
  `done`** (confirmed via an independent API read); the **hard-block project rendered
  with NO enabled Approve button**; mobile viewport (390×844); focus + reduced-motion.
- **Real gate-write proof (the one mutation):** approving `final_render` on a
  disposable all-stages-done project runs the **genuine `pipeline.produce`** (bound to
  the disposable dir via its own `root=` param) and flips the project to `done` on disk
  with no heavy producer; re-approve on a done project → 409 "not at a gate"
  (idempotent); a `block` → 409 `routed_back`, the spine never asked to run.
- **Existing repo suite unaffected: 71 passed** across
  `test_pipeline/test_session/test_project_view/test_contracts/test_showrunner_registry/test_chat_state`.
- **Real data integrity:** across the entire test run, the real `atlas/chat_state.json`
  stays byte-identical and `git status projects/` reports **0 changed files**.

---

## 6. Security / leak / edge-case audit — findings & fixes

An adversarial read-only audit was run; all findings were fixed in the main thread and
re-verified.

| ID | Sev | Finding | Fix (verified) |
|---|---|---|---|
| **C1** | CRITICAL | `chat_state.load_json` **renames a corrupt file aside on read** — so a read-only GET could mutate the real projects tree. The dashboard used it everywhere (incl. via `project_view`). | Added a non-mutating `data.read_json` (parse-in-place, never renames); replaced every `chat_state.load_json` call; reimplemented `project_view`'s read-only previews/`find_latest_blocked` locally over `read_json`. Verified: viewing a project with a corrupt artifact leaves the dir byte-identical and returns `valid:false` (no 500). |
| **H1** | HIGH | `redact()` only scrubbed secret-hinted *keys*; a secret **value** in a free-text field (a `note`, `claim_text`, exception) passed through. | Added value-level token-shape scrubbing (`sk-ant-…`, `sk-…`, `AIza…`, `ghp_…`, `xox*-…`, `AKIA…`, JWT) → `***`. Verified. |
| **M1** | MED | Artifact/media JSON bypassed the central redaction wrapper (only inner `data` was redacted; envelope `errors`/`name` were not). | `serve_artifact` now redacts the whole envelope. |
| **M2** | MED | Path redaction only collapsed `/home`,`/Users`,`/root`; `/tmp`,`/var`,`/opt`… leaked, and the home tail was kept. | Broadened `_ABS_PATH` to all sensitive roots; collapses to `~`. Verified. |
| **L1** | LOW | Suffix Range `bytes=-N` was parsed as `0..N`. | Correct suffix handling (`start = size - N`). Verified `bytes=-100` → trailing 100 bytes. |
| **L2** | LOW | `_get_session` used a shared/predictable temp scratch path. | Per-process unique scratch path; and the session is built without the orchestrator and never writes the real `chat_state.json`. |

**Constraint scorecard (post-fix):** No secret leaks ✅ · Path traversal refused
(`..`, encoded, absolute, symlink-out, non-whitelisted artifact) ✅ · Read-mostly / no
pipeline mutation ✅ (only the sanctioned approve writes; corrupt-file rename
eliminated; `chat_state.json` never touched) ✅ · Hard fact-check `block`
un-approvable ✅ · Media streamed (constant-memory, correct 206/416) ✅ · No XSS (all
API data rendered through HTML-escaping sinks; artifact viewer uses `textContent`) ✅ ·
Edge cases (corrupt/missing/empty/unknown/idempotent) all degrade, zero 500s ✅.

---

## 7. What's wired vs gracefully degraded

- **Fully wired to real data:** Overview, Projects, Pipeline detail, Fleet, Agent
  profiles (all 10), Gate (both variants, block vs flags), streamed video, artifact
  viewer, the real gate-approval write.
- **Degraded by design (eval layer is a separate track):** Quality scorecard/trend.
  No project has an `eval_scorecard.json` yet, so the screen shows a clear empty state
  while still rendering the frozen rubric standard + the eval tracking ledger. The
  consume path is implemented and tested (a synthesized scorecard renders), so it
  lights up automatically once the Inspector scores a render.

---

## 8. Known gaps & recommended next steps

- **Quality is empty in the live data** (no scorecard exists). To populate it without
  building the eval backend, run the objective-only Inspector on a finished project:
  `cd atlas && ../venv/bin/python -m eval.inspector projects/<slug>` (writes that
  project's own `eval_scorecard.json`). The dashboard will then show real scores.
- **Presentational controls** (New production, Re-render, Run inspector, Send-back,
  Ask-agent) are inert: each would mutate beyond gate approval, so they're deferred
  until routed through a sanctioned entrypoint (`produce` for New production behind an
  explicit gate; `ask_agent`/Inspector behind their own seams).
- **Live updates** are poll-on-nav today; a `/api/overview` SSE/poll for in-flight
  projects would make the spine animate in real time (the data layer already exposes
  `running`/`blocked` live status).
- **Factcheck-gate approval is not click-tested in Playwright** on purpose — approving
  a fact-check gate re-runs Sage's real engine (LLM); the E2E only clicks the
  final-render approve (no LLM). Its rendering (approvable + flags) is covered.
- The repo's `chat_state.load_json` rename-on-corrupt behavior is fine for the engines
  but is a foot-gun for any read-only consumer; the dashboard now avoids it. Worth a
  note in `chat_state.py` for future readers.
