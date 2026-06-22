# SYSTEM_MAP.md — how YT-AGENTS actually fits together

> Phase §1 deliverable. Synthesized from `PROJECT_CONTEXT.md` + a deep code-walk subagent (evidence by file:line). **Ground truth = code.** Where code ≠ prose docs, see the last section.

## One-paragraph model

A topic brief enters through **Atlas the Showrunner** (an LLM on the Claude Agent SDK `query()` loop). Atlas calls a single `produce_video` tool, which hands off to a **deterministic spine** (`atlas/pipeline.py`) that runs **10 fixed stages**, validates every artifact against a **frozen JSON-Schema contract**, enforces two human gates + one auto-gate, and is resumable. Each stage's producer calls a **specialist engine in-process** (isolated import via `adapters/loader.py`), each a self-contained sibling project with its own persona/brain/memory. The render stage shells to **HyperFrames** (Node CLI) + FFmpeg to produce `video.mp4`.

## Agent roster — 8 agents (NOT 7; docs lag)

`atlas/registry.py` declares **8** AgentEntries. PROJECT_CONTEXT/MEMORY say "7 roles" — stale; the 8th (Vera) is uncommitted but fully wired.

| # | name | persona | role | jobs | project |
|---|---|---|---|---|---|
| 1 | scout | Viral Scout 🔎 | Topic intake | scout_find_topics | youtube-topic-agent |
| 2 | sage | Sage 📚 | Researcher & Fact-Checker | sage_research, sage_factcheck | topic-researcher |
| 3 | scriptwriter | Marlow 📝 | Scriptwriter | scriptwriter_write_script | scriptwriter |
| 4 | art_director | Iris 🎨 | Art Director | art_director_design_style, _build_storyboard | art-director |
| 5 | asset_sourcer | Magpie 🗂️ | Asset Sourcer & Licensing | asset_sourcer_source_assets | asset-sourcer |
| 6 | audio | Cadence 🎙️ | Audio / Sound Designer | audio_record_narration, audio_mix_audio | audio-designer |
| 7 | composition_engineer | Mason 🛠️ | Composition Engineer | _compose_scenes, _render_video | composition-engineer |
| 8 | **reference_analyst** | **Vera 🔬** | **Reference Analyst (standards)** | reference_analyst_build_rubric | reference-analyst |

No entry sets `stub=True` — every slot has a real engine. Tools generated: 11 job + 8 persona (`ask_<name>`) + 1 `produce_video` = **20 SDK tools**.

## Pipeline — 10 stages, fixed order (`atlas/pipeline.py:58`)

1. **research** → `sage.produce_research` → `research_brief` *(stub fallback only via `ATLAS_RESEARCH_STUB`)*
2. **script** → `scriptwriter.produce_script` → `script`
3. **factcheck** → `sage.produce_factcheck` → `factcheck_report` — **★ fact-check gate AFTER**
4. **style** → `art_director.produce_style` → `style_guide`
5. **storyboard** → `art_director.produce_storyboard` → `storyboard`
6. **assets** ∥ → `asset_sourcer.produce_assets` → `asset_manifest`
7. **narration** ∥ → `audio.produce_narration` → `narration_transcript`
8. **compose** → `composition_engineer.produce_compose` → `composition_manifest` — **▲ auto-gate**
9. **audiomix** → `audio.produce_audiomix` → `audio_manifest`
10. **render** → `composition_engineer.produce_render` → `video.mp4` (no contract; binary) — **★ final-render gate BEFORE**

**Gate mechanics** (all read from disk so they re-fire on resume):
- **Fact-check gate** (`_factcheck_gate`, pipeline.py:345): `verdict=="block"` → `rejected` + `blocked_at_factcheck`, **un-approvable** — re-blocks every invocation. On `approve=factcheck` the stage is **reset to pending and re-run** (the old report is never trusted — re-earn path).
- **Final-render gate** (`_final_render_gate`, pipeline.py:383): pauses BEFORE spending the render.
- **Composition auto-gate** (pipeline.py:309): blocks unless `"auto-gate PASS"` appears in Mason's summary (self-scan + lint + validate + inspect).
- **Resume**: `done` stages skipped; approve-only resume scoped strictly to `blocked_at_<gate>`; new slugs carry timestamp+uuid to avoid same-second collisions.

## Contracts (`atlas/contracts/`) — 11 schemas

All `additionalProperties: true` + required `schema_version`. `validate(name,obj)` never raises on bad data (only on unknown contract name). `CONTRACT_VERSION="1.0"`; bumped to `1.1`: `style_guide`, `storyboard`, `audio_manifest`. New (uncommitted): `reference_rubric` — but it stamps `"reference_rubric/1.0"` (slash form), breaking the bare-`"1.0"` convention (normalize candidate).

**Closed-set vocabulary** lives in lockstep in two engines (verified to match exactly):
- LAYOUTS (10), TRANSITIONS (5), EFFECTS (7), TEXTURES (5).
- Iris specifier: `art-director/art_engine.py:79–127`. Mason renderer: `composition-engineer/composition_engine.py:91–123`. Magpie skips render-kinds (`_RENDER_KINDS={brand,chip}`) so brand shots aren't sourced as footage.

## Per-engine reads→writes

- **Scout** `agent.py`: YouTube+Trends → ranked topic ideas.
- **Sage** `researcher.py`+`factcheck.py`: web → `research_brief`; (script,brief) → `factcheck_report`.
- **Marlow** `script_engine.py`: brief → `script` (per-scene claims traced to sources; drops ungroundable).
- **Iris** `art_engine.py`: script → `style_guide` + `storyboard` (#FFD000 beat, motion budget).
- **Magpie** `source_engine.py`: storyboard+style → `asset_manifest` + local files (relevance-first ranking, license truth-table).
- **Cadence** `audio_engine.py`: script → `narration.wav`+transcript; ducked `master.wav`+`audio_manifest` (master-bridge).
- **Mason** `composition_engine.py`: all artifacts → scene HTML + `composition_manifest` → `video.mp4` (pure code, auto-gate, brand chips).
- **Vera** `reference_engine.py`+`rubric_store.py`: reference video files (FFmpeg/OpenCV) → `reference_rubric` (banded targets + style profile), persisted under `standards/`. **Standalone job — NOT a pipeline stage** (pipeline.py has zero Vera refs).

## Reference example (the "good output" benchmark) — CONFIRMED

`atlas/projects/gpt-4o-vs-claude-vs-gemini-vs-deepseek-comparison--20260621-013345-67a3/`: `status:done`, all 10 stages done, both gates approved, **`video.mp4` = ~9.0 MB, 11 scenes, 72.5s audio**. The ~8 other projects in that dir are `blocked_at_factcheck` — real gate blocks, proving the gate fires.

## Code-vs-docs discrepancies (to reconcile in §4)

1. **"7 roles" is stale everywhere** — registry has 8 (Vera). PROJECT_CONTEXT §1/§6/§12, MEMORY, agent table.
2. **`atlas/README.md` / `atlas/PLAN.md`** describe "Scout + Sage only" early phase. **`atlas/CHANGELOG.md` 0.2.0** still calls five specialists "stubs." All real now.
3. **`pipeline.py:20–22` docstring** falsely says "default producers are the stub specialists … runs with no network." 10/10 stages bind real engines.
4. **`contracts/__init__.py` docstring (~26–29)** still says "version each *stub* emits" / "when the real Art Director / Composition Engineer arrive."
5. **`registry.py:112–116` block comment** "The five not-yet-built specialists — REGISTERED SLOTS with stub adapters" — all five are built.
6. **`reference_rubric` schema_version** uses slash form `"reference_rubric/1.0"` vs bare convention — inconsistent.
7. **Vera (8th agent) is uncommitted** and undocumented in PROJECT_CONTEXT.

## Verdict on Phase 1

System is real, coherent, and matches its own architecture doc closely. Two planes cleanly separated; contracts frozen-extensible; closed-set vocabulary genuinely in lockstep; one verified end-to-end success. The prose docs (README/PLAN/CHANGELOG + the "7 roles" count) are the main drift. Render is possible under Node 22. Two uncommitted bodies of work (Issue #2, Vera) both look additive and green.
