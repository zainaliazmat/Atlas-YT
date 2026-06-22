# AUDIT_REPORT.md — neutral, evidence-based assessment

> Phase §2 deliverable. Synthesized from four parallel auditors (core spine · specialist engines · tests · creative output), with key claims re-verified by the main thread. Evidence is cited by file:line. **Verified** = ran/observed; **inferred** = reasoned from code.

---

## Overall grade: **B−  ("a beautifully engineered machine currently shipping an amateur product")**

The split verdict is the whole story. As *software*, this is strong, disciplined work: a real two-plane architecture, frozen-extensible contracts, a genuinely lockstepped closed-set vocabulary, 420 passing behavior-driven tests, and airtight determinism/gate logic. As a *video agency* — its only actual job — the one finished deliverable is an **amateur AI slideshow**: wrong stock imagery, a broken display font on every scene, a 1×1-pixel placeholder standing in for the climactic chart, and no music bed — all stamped `PASS` by the gates. The talent in this pipeline is real but **trapped upstream of the frame.** The good news: the in-flight Issue #2 work targets exactly this, and the worst rendering bug (fonts) is a ~3-line fix. The engine is ready; the product is one focused push away.

## Scorecard (1–10)

| Dimension | Score | One-line justification | What moves it up a point |
|---|---|---|---|
| **Architecture & design** | **8** | True two-plane split (LLM judgment vs `pipeline.py` guarantees); registry→tools "new agent = 1 entry + 1 adapter"; lockstep vocab is real & test-enforced. | Kill the two duplicate gate-detail builders (`pipeline._render_plan` vs `project_view.gate2_preview`); derive/assert `loader._COLLIDING` instead of hand-maintaining it. |
| **Code quality & maintainability** | **7.5** | High-signal docstrings explaining *intent*; small pure helpers; engines never import Atlas (verified by grep). | One canonical model-ID source; de-dup `_strip_json`/`_chat_json` across siblings; remove dead stub paths that invite confusion. |
| **Correctness & robustness** | **7** | Comprehensive error containment at the tool seam; corrupt-JSON backup-not-crash; resume re-earns the factcheck verdict from the engine, never trusts disk. | Guard against double-dispatch of abandoned timed-out jobs; close the cross-process state race. |
| **Test coverage & meaningfulness** | **8** | 420 passing, behavior-driven; gate-block invariant tested 5 ways incl. a disk-tamper integrity test; cross-engine vocab drift caught by AST-parsing. **VERIFIED by running every suite.** | Revive the 16 dead Scout tests (missing fixture); add a real `hf_tools`/CLI-parse test to kill the vacuous-pass risk. |
| **Determinism & safety** | **8** | `block` verdict provably un-approvable (re-evaluated from disk every call); no `Math.random`/`Date.now`/render-time fetch in emitted HTML (grep-verified); license truth-table exact; `ANTHROPIC_API_KEY` warn path correct. | Make `hf_tools.run_gate` fail-closed on unparseable-but-rc0 CLI output; add `-fflags +bitexact` to ffmpeg mux for cross-version byte-identity. |
| **Creative output quality** | **3.5** | Excellent script + sophisticated style-guide *spec*, but the rendered video is wrong stock + broken font + blank proof-chart + no music, all gated `PASS`. **VERIFIED against artifacts.** | This is the headline gap — see Critical bugs #1/#2 and all of §5. A relevance/fidelity gate + on-brand graphics engine moves this to 6–7 immediately. |
| **Docs & onboarding accuracy** | **4** | `PROJECT_CONTEXT.md` is good, but README/PLAN/CHANGELOG describe the early "Scout+Sage only" phase; "7 roles" is stale (there are 8); pipeline/contracts docstrings still say "stubs." | Reconcile README/PLAN/CHANGELOG to code; acknowledge the 8th agent (Vera); fix the stale "stub" docstrings. (§4 doc-reconcile.) |
| **Developer & operator experience** | **6.5** | Clean CLIs, operator-grade error messages, a full web UI. But two real traps: the Node-18-vs-22 render cliff with no guardrail, and terminal+web sharing one `chat_state.json`. | Add a Node-version preflight check before render; separate or CAS-guard the shared state file. |

---

## Critical reframe: the "completed example" is a PRE-FIX baseline

Auditor-D's brutal 3.5/10 was scored against `atlas/projects/gpt-4o-vs-claude-…/video.mp4`, which was rendered **before** the uncommitted Issue #2 work (brand chips + relevance sourcing). So its worst symptoms (no real logos, Met-museum altarpiece captioned "Writing → Claude", dice photo for "four logos") are precisely what Issue #2 fixes. This is the **"before" reference** for the §6 quality climb. But it also surfaced bugs that are **live in current code** (font rendering) or **structural** (gates can't see creative correctness) — those are real §4 work, not pre-fix artifacts.

---

## Bug inventory (prioritized)

### CRITICAL

**C1 — Mason renders a broken `font-family` (leaked Python dict) and never loads the display font. [VERIFIED live in current code.]**
- Repro: produce any video → every scene HTML emits `font-family:{'family': 'Inter', 'weight': 400},system-ui,sans-serif;`.
- Root cause: Iris emits typography as nested dicts (`typography.display = {"family":"GT Sectra","weight":700}`, `art_engine.py:476-480`). Mason reads the **wrong key** `typ.get("heading")` (doesn't exist) then falls to the `body` **dict** and stringifies it into CSS (`composition_engine.py:859` → used at `:743`). So the designed display font never loads and the CSS is malformed.
- Fix: in `_scene_ctx`, resolve `font = (typ.get("display") or typ.get("body") or {}).get("family", "Inter")` (and a separate body font), pass strings to CSS. Add a test asserting no `{` in the emitted `font-family`.

**C2 — The gates pass a creatively broken video; there is no relevance/render-fidelity gate. [VERIFIED — `composition_manifest.json` shows `verdict:pass`, `auto_gate:PASS`, 0 flags on the amateur cut.]**
- Repro: the example project is `status:done`, both gates `approved`, yet half its scenes drop their specified image from the DOM and the climactic chart is a 1×1 px file.
- Root cause: the auto-gate checks structural/determinism rules (net/SMIL/late-gsap.set + lint/validate/inspect) but **nothing checks that the asset semantically matches the shot, that a specified image actually rendered, or that placeholders/1×1 files didn't reach a `done` render.** Creative correctness is invisible to the deterministic plane by design.
- Fix (design-level, §4+§5): add a fidelity check — reject `_placeholder.png`/1×1 assets in a final render, reject a scene whose storyboard shot specifies media that is absent from the DOM, and surface weak-relevance assets to the human gate. Keep it deterministic (it's a guarantee, not judgment).

### HIGH

**H1 — Issue #2 named-model brand gap (the known gap, confirmed).** Generic "four logos lined up" shots that name no specific model get `brand_keys==[]` → no chip → placeholder (`art_engine.py:139-143`, `composition_engine.py:407-423`). Fix: when a shot's kind is `brand`/`logo` (or content cues a "logos/models lineup") but detection is empty, fall back to the full `BRAND_CHIPS` roster.

**H2 — Relevance scoring is binary for short queries (Issue #2 Direction B degeneracy).** `relevance = |q∩h|/|q|` over ≤6 query tokens (`source_engine.py:411-419`); a single coincidental title word scores a perfect 1.0 and sails past `RELEVANCE_FLOOR=0.20` *and* `RELEVANCE_WEAK=0.50` — re-admitting the exact irrelevant footage Issue #2 targets (a museum "Writing Desk" for subject "writing"). The 0.1 sort-bucket also lets 1.0 and 0.6 tie and fall back to license-rank. Fix: require ≥2-token overlap for a high score / weight by phrase presence; loosen the bucket.

**H3 — Placeholder/1×1 assets reach a `done` render.** The scene-10 proof chart is `assets/_placeholder.png` = a literal 1×1 transparent PNG (verified `PNG image data, 1 x 1`). Part of C2's fidelity gate. Also exposes a missing **native data-viz capability** (the bar chart should be generated, not sourced).

**H4 — Timed-out sibling job can be double-invoked.** `tools.py:75-81` abandons a timed-out worker thread (engine event loop + file handles still live); a retry/resume can run the same non-reentrant engine twice against the same project files. Fix: track in-flight `(agent,job)` keys; refuse/serialize a second dispatch until the first is observed dead.

**H5 — 16 Scout tests are dead (missing `tmp` fixture).** `youtube-topic-agent/tests/test_distill_memory.py` — every test takes a `tmp` param with no fixture defined → 16 collection errors; Scout's distill/pending-fold/failure-parking is effectively untested. Fix: add `@pytest.fixture def tmp(tmp_path): return tmp_path` (or a project conftest). One-line, lights up 16 tests.

### MEDIUM

**M1 — Model-ID drift across the fleet.** `grep CLAUDE_MODEL */llm.py`: Atlas/asset-sourcer/audio/composition/reference use full slug `"claude-sonnet-4-6"`; scriptwriter/topic-researcher/youtube/art-director use bare `"opus"`. Two formats, divergent semantics, no shared source → silent cost/behavior divergence (Sage research runs on `opus` while Atlas runs on sonnet). Fix: one canonical full-slug source + CI assertion. (Also: evaluate Opus 4.8 as the default per the brief — read each `llm.py`, don't assume.)

**M2 — `chat_state.json` last-writer-wins cross-process race.** Terminal + web both hold an in-RAM summary and `save_summary` wholesale (`chat_state.py:81-92`); the second distill clobbers the first. Fix: per-frontend state files or CAS-on-`updated`.

**M3 — `hf_tools.run_gate` vacuous-PASS.** If the HyperFrames CLI exits 0 but emits unparseable JSON, `_parse_json→None→{}` → zero findings → gate passes (`hf_tools.py:43-49,93,110,126`). Not currently exploitable (missing-binary fails closed) but one output-shape change flips the auto-gate silently green. Fix: treat "expected shape absent + rc0" as FAIL.

**M4 — `_DIM_CUE` dims all brands in a multi-brand shot.** Dim is shot-level not brand-level (`composition_engine.py:443-462`): "Claude foregrounded, GPT-4o fades back" dims *both*. Fix: scope the cue to each alias span, or document the one-brand-class-per-shot constraint.

**M5 — On-screen text is the full narration sentence (subtitle, not designed kinetic typography).** [inferred from example HTML] The designed short labels ("Wrong question." / "Coding → Claude") never render; the bottom bar duplicates the VO line. Creative — feeds §5.

**M6 — Cross-scene transitions are metadata-only.** [inferred] Storyboard specifies match-cut/dip-to-black, but scenes are independent comps concatenated by ffmpeg; transition grammar isn't baked into render. Feeds §5 motion work — verify during §3 live run.

### LOW
- L1 `atomic_write_json` doesn't fsync tmp before `os.replace` (`chat_state.py:36-37`).
- L2 Stub `ask` fabricates an in-character reply with no grounding (`base.py:75`) — should short-circuit on `stub=True` (no live stubs today, latent).
- L3 Sage conversational `research` path returns without `validate`/`schema_version` stamp and calls `progress` unguarded (`adapters/sage.py:232-247`).
- L4 `adapters/scriptwriter.py:44` & `adapters/sage.py` stamp bare `CONTRACT_VERSION` instead of `version_for(name)` — latent drift if those contracts bump.
- L5 `reference_rubric` stamps slash-form `"reference_rubric/1.0"` vs the bare-`"1.0"` convention.
- L6 `_resolve_blocked_slug` (refuse on ambiguity) vs `find_latest_blocked` (silently newest) use different policies — a UI approve could target a different project than a CLI approve.
- L7 RuntimeWarning (un-awaited coroutine) in `atlas/tests/test_async_containment.py:25` (cosmetic).

---

## Improvement / enhancement backlog (prioritized)

1. **Fix C1 (fonts) + C2/H3 (fidelity gate)** — the two changes that most improve shipped quality. (§4)
2. **Close Issue #2 fully** — H1 (named-model fallback) + H2 (relevance scoring) + commit A & B cleanly with tests. (§4)
3. **Native on-brand graphics engine** — real model logos (already started via brand chips) **+ generated SVG/CSS data-viz** so charts/comparisons are rendered, not stock-sourced. Highest creative leverage. (§5)
4. **One canonical model-ID source + CI assertion**; evaluate Opus 4.8 as default. (§4)
5. **Parallelize Cadence per-scene TTS** (order-preserving; safe per Auditor-B analysis) — real latency win, no determinism risk. (§4)
6. **Real licensed music bed + warmer VO (SSML) + render the *designed* kinetic typography** (M5). (§5)
7. **Bake real transition grammar** (M6) instead of metadata-only. (§5)
8. **Harden grounding/factcheck** — validate narration prose, wire the unused `find_*_problems` guards, add source-text corroboration (factcheck is currently a same-model LLM-judge with no ground truth).
9. **Cross-process state safety (M2), in-flight job guard (H4), hf_tools fail-closed (M3), revive Scout tests (H5).**
10. **Doc reconciliation** (README/PLAN/CHANGELOG, "7→8 roles", stub docstrings) + **Node-22 render preflight**. (§4)

## Notable verified-GOOD (no action)
- `block`-gate un-approvability is airtight (5 tests incl. disk-tamper integrity).
- No `Math.random`/`Date.now`/render-time fetch in emitted HTML (grep-verified); brand SVGs inlined as data.
- License truth-table matches spec exactly; engines never import Atlas (grep-verified).
- Loader snapshot/restore + single-lock critical section is correct & thread-safe for load-time.
- The otel-ollama shim is correctly dormant and never imported (no finding).
- Vera (8th agent) is finished, additive, and green (32 tests).
