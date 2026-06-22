# QA_LOG.md — live end-to-end pipeline QA

> Phase §3 deliverable. A real production was driven on the CURRENT (pre-fix) code with the real engines (no stub), Node 22 active, subscription LLM. **Everything here was executed and observed (VERIFIED).**

## The run

- **Brief:** *"Coffee vs tea: which actually gives you better energy? A tight 5-scene explainer with a clear data comparison."* Chosen to stress the data/comparison + magnitude-claim path while staying at 5 scenes (tractable TTS/render).
- **Slug:** `coffee-vs-tea-which-actually-gives-you-better-ener-20260622-080322-0e8b`
- **Outcome:** **FULL END-TO-END SUCCESS.** All 10 stages `done`, both gates approved, real **`video.mp4` = 1920×1080 H.264 + AAC, 98.5s, 12.3 MB** (confirmed via ffprobe). This proves the render toolchain works live in this environment.

## Stage-by-stage (every artifact validated against its frozen contract → all valid, no errors)

| Stage | Contract valid? | Critic's note |
|---|---|---|
| research | ✅ | 8 verified facts, 32 sources; accurate caffeine data (coffee ~95 / black ~47 / green ~29 mg); credible sources; surfaces L-theanine nuance. Strong. |
| script | ✅ | 5 scenes, sharp hook ("they run on the exact same drug"); scene 4 honestly flags "smoother energy = not proven." Good restraint. |
| factcheck | ✅ | verdict **pass**, 11 verified / 0 flagged. Nit: claim s2c2 cites a *White tea* wiki page for a black-tea number — mismatched source, correct value. |
| style | ✅ | Coherent palette; #FFD000 reserved; motion budget 2/scene. |
| storyboard | ✅ | Layouts well-matched: scene 2 `data-chart`, scene 4 `split-screen` + signature beat, scene 5 `comparison-2up`. |
| assets | ✅ | 13 real Pexels/Pixabay assets w/ provenance; honest `sourced` vs `cleared`; **first asset relevance only 0.5** (Issue #2 signal). |
| narration | ✅ | 5 contiguous segments, 98.5s, clean Kokoro TTS timing. |
| compose | ✅ | auto-gate **PASS**, 5/5 scenes lint+validate+inspect clean. |
| audiomix | ✅ | narration `cleared`; music bed honestly a flagged `placeholder`. |
| render | n/a binary | Real playable MP4, 98.5s, 12.3 MB. |

## Gate behavior — VERIFIED correct
- **Fact-check gate:** verdict was `pass` (so the un-approvable *block* path wasn't triggered this run — to be exercised separately in §4 with a myth-busting brief). Confirmed the gate **re-runs factcheck on every resume** (re-earns the verdict; never trusts a stored approval) and advances correctly with `--approve factcheck`.
- **Final-render gate:** verified it **re-blocks without approval** (a bare `--resume` re-holds at `final_render`, does not advance); `--approve final_render` proceeds to render. Both gates behave per spec.

## Fleet interrogation (real subscription LLM via `adapter.ask`) — all SOLID, no hallucination/drift
- **Iris** (art_director): coherent #FFD000-on-the-tension-beat rationale; motion-budget defense ("if the whole video moves, the highlighter means nothing"). In voice.
- **Magpie** (asset_sourcer): correctly distinguishes platform-license vs rights-clearance (`sourced`≠`cleared`); owns the 0.5 relevance as "a placeholder with better paperwork." No hallucinated clearance.
- **Mason** (composition_engineer): accurately described his self-scan/lint/validate/inspect auto-gate; **admitted a leaked dict in `font-family` "probably wouldn't be caught" (silent fallback) — "a real gap."** HyperFrames facts correct.
- **Marlow** (scriptwriter): defended the unresolved ending as the payoff; refused to invent an unsupported conclusion ("the video ends where the evidence ends"). No overclaiming.

**Verdict on the fleet:** the agents' *reasoning* is genuinely excellent — they say the right things and produce contract-valid artifacts. **Every defect is in the rendering / asset-relevance layer**, not the judgment layer.

## Defects surfaced live (some NEW beyond the static audit)

- **C1 (CRITICAL, confirmed live):** every scene HTML line 10 emits `font-family:{'family': 'Inter', 'weight': 400},system-ui,sans-serif;` — leaked Python dict, browser discards it → silent `system-ui` fallback; **GT Sectra never loads.** Root cause `composition_engine.py:859` (wrong key `heading` → falls to `body` dict). Mason corroborated it himself.
- **C2 (NEW, HIGH):** auto-gate reports `contrast_failures: 5` yet stamps `auto_gate: "PASS"` — it **counts contrast failures but does not block on them.** Every scene fails WCAG contrast.
- **C3 (HIGH, Issue #2 live):** scene 4 (coffee-vs-tea energy) renders a full-bleed **coal power plant + wind turbine** image — completely off-topic, in the final video. Confirms relevance-scoring degeneracy (audit H2) even with Direction B active.
- **C4 (NEW, HIGH):** caption legibility — full narration paragraphs burned in as tiny dark text over dark imagery, near-illegible (the visual form of C2).
- **C5 (NEW, HIGH):** the `data-chart` layout renders **no chart** — scene 2 is the centerpiece comparison; storyboard picked `data-chart` with a generated `s2_caffeine_bars` data-viz asset, but the frame shows plain centered text. The data-viz never reaches the screen. For a "data comparison" brief, the comparison visual is absent. (Major §5 target: native data-viz.)
- **C6 (NEW, MED):** runtime estimate off — gate reports `est_runtime_sec: 67.0` vs actual `98.5s` (~46% under). Estimate unreliable.
- **Robustness note (NOT a creative bug):** the pipeline does **not** self-mark a stage `failed` when the process is killed externally (e.g. SIGTERM) — `project.json` stays `status: running`. It relies on idempotent resume to recover, which worked perfectly every time. Worth a `running`→`failed` reconciliation on resume.

## Net read
The spine, contracts, gates, resume, and the fleet's reasoning are all **verified solid**. The product gap is entirely in the **last mile to the frame**: fonts (C1), legibility/contrast (C2/C4), asset relevance (C3), and missing native data-viz (C5). These are exactly the §4 fixes + §5 creative upgrades — and the fact that a clean, contract-valid, gate-respecting MP4 came out the other end means the foundation to fix them on is solid.
