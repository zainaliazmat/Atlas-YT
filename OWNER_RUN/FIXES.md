# FIXES.md — §4 repairs (every fix has a test, root cause, and commit)

> Phase §4 deliverable. All work on branch `owner-run-fixes`. **Full suite after: 462 passing across 9 projects, 0 errors** (was 420 passing + 16 dead). Each fix was TDD'd by a per-area subagent (test fails before, passes after).

## Test count delta (verified, all run)

| Project | Before | After |
|---|---|---|
| atlas | 134 | 134 |
| composition-engineer | 55 | **74** (+19) |
| asset-sourcer | 21 | **25** (+4) |
| audio-designer | 27 | **30** (+3) |
| youtube-topic-agent | 7 (+16 errored) | **23** (revived) |
| reference-analyst | 18 | 18 |
| scriptwriter | 50 | 50 |
| art-director | 41 | 41 |
| topic-researcher | 67 | 67 |
| **Total** | **420 + 16 dead** | **462, 0 dead** |

## Fixes

### C1 — CRITICAL — Mason leaked a Python dict into `font-family` (commit `0e58a1a`)
- **Root cause:** `composition_engine.py:859` read `typ.get("heading")` (a key Iris never emits — she uses `display`), fell through to the `body` *dict*, and injected it raw into CSS → `font-family:{'family': 'Inter', ...}`. Browser discarded the declaration → silent `system-ui` fallback; the designed display font (GT Sectra) never loaded on any scene.
- **Fix:** new `_font_family()` resolves dict-shape (`.get("family")`) / bare-string / fallback; heading font from `display`, body font separately, both as strings.
- **Tests:** `test_dict_shaped_typography_emits_font_name_not_dict_repr`, `test_string_shaped_typography_still_works` (assert family name present, no `{` in any font-family).

### C5 — HIGH — `data-chart` layout rendered no chart (commit `0e58a1a`)
- **Root cause:** generated data-viz assets have no file, so the media path fell through to placeholder/text; a data-comparison scene rendered as bare centered text.
- **Fix:** `parse_chart_data()` + `render_bar_chart()` — deterministic build-time inline-SVG bars (tallest accented `#FFD000`, no JS/animation) from the scene's data, with present-asset precedence. A `data-chart` scene can never render bare text again.
- **Tests:** `test_data_chart_renders_native_bar_chart_from_scene_data`, `test_data_chart_scene_with_data_is_not_bare_text`, `test_parse_chart_data_extracts_label_value_pairs`.

### C2 — HIGH — auto-gate passed despite contrast failures (commit `0e58a1a`)
- **Root cause:** `contrast_failures` were counted into the summary but never gated; the live run showed `contrast_failures: 5` with `auto_gate: PASS`.
- **Fix:** a scene with `contrast_failures > 0` sets `gate_ok = False`; the final tally requires `not contrast_failures`.
- **Test:** `test_contrast_failures_block_the_auto_gate`.

### C4 — HIGH — caption legibility (commit `0e58a1a`)
- **Root cause:** long narration burned in as small text with only a faint shadow over dark imagery.
- **Fix:** caption wrapped in a `.caption-scrim` panel (`rgba(0,0,0,0.72)` rounded bg + shadow), larger weight/size; heading already uses the short `on_screen_text` label.
- **Test:** `test_captions_have_a_legibility_scrim`.

### H1 — HIGH — Issue #2 named-model brand gap (commit `0e58a1a`)
- **Root cause:** `detect_brands` matched only the 4 registered aliases; a generic "four AI logos lined up" shot naming no model → no chips → placeholder.
- **Fix:** `_is_generic_roster_shot()` — on `kind:brand/logo` or a roster-word + lineup/count cue, fall back to the full `BRAND_CHIPS` roster; named models still take precedence.
- **Tests:** `test_generic_logo_lineup_shot_falls_back_to_full_roster`, `test_generic_models_lineup_by_content_cue_without_kind`, `test_named_models_still_take_precedence_over_roster_fallback`.

### H2/C3 — HIGH — Magpie relevance-scoring degeneracy (commit `281d278`)
- **Root cause:** `relevance = |q∩hit|/|q|` over ≤6 tokens was binary for short queries — a single coincidental token scored 1.0 and passed both the 0.20 floor and 0.50 weak threshold. Live failure: a coal-power-plant image shipped full-bleed in a coffee-vs-tea scene. The 0.1 sort bucket also let 1.0 and 0.6 tie and fall back to license-rank.
- **Fix:** single-token match capped at 0.45 (below WEAK); ≥2-token overlap keeps the clean fraction; comparison-filler stopwords stripped both sides; sort bucket `round(_,1)`→`round(_,3)`.
- **Tests:** `test_coal_plant_single_incidental_token_lands_below_floor` (regression), `test_single_token_query_match_does_not_score_full_confidence`, `test_sort_bucket_is_fine_enough_to_separate_near_relevances`, + the relevance-first intent re-confirmed.

### M3 — MED — `hf_tools.run_gate` vacuous-PASS (commit `0e58a1a`)
- **Root cause:** CLI exit 0 + unparseable JSON → `_parse_json→None→{}` → zero findings → gate PASS.
- **Fix:** lint/validate/inspect fail-closed when `json is None` (rc0 but no parseable payload → cannot confirm → FAIL); missing-binary fail-closed preserved.
- **Tests:** new `test_hf_tools_gate.py` — fail-closed-on-garbage + clean-pass + missing-binary guards.

### M4 — MED — `_DIM_CUE` dimmed all brands in a multi-brand shot (commit `0e58a1a`)
- **Fix:** `_dim_brands_in_shot()` — 1 brand → whole-shot cue; 2+ brands → split clauses and dim only the brand sharing a clause with the cue; the foregrounded winner stays bright.
- **Tests:** `test_dim_cue_does_not_dim_the_foregrounded_brand_in_a_multibrand_shot`, `test_single_brand_dim_cue_still_applies`.

### H5 — HIGH — 16 dead Scout tests (commit `c05691b`)
- **Root cause:** `youtube-topic-agent/tests/` used a `tmp` fixture that was never defined (no conftest); 16 tests across 2 files errored at collection.
- **Fix:** added `youtube-topic-agent/tests/conftest.py` with `tmp` → `tmp_path`. **7 → 23 passing**, no real failures hiding underneath.

### M1 — MED — model-ID drift (commit `7c84a82`)
- **Root cause:** mixed formats (full slug `claude-sonnet-4-6` vs bare `opus`/`sonnet`), divergent semantics, no shared source.
- **Fix:** all Claude model constants normalized to full slugs; the four creative/judgment agents (Marlow, Iris, Sage, Scout) mapped from bare `opus` → **`claude-opus-4-8`**; sonnet agents unchanged. **All three IDs (`claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`) probe-verified to resolve on the subscription** before shipping. No bare aliases remain.

### Perf — Cadence per-scene TTS parallelized (commit `bf218e9`)
- **Change:** concurrent synthesis via a thread pool (sync `tts_fn` seam); offsets/transcript/concat computed strictly in original scene order. Determinism is locked by `test_parallel_preserves_order_when_later_scenes_finish_first` (a `Barrier(3)` proves real concurrency; forced out-of-order completion still yields byte-identical transcript + concat order) and a mid-list-failure test (raises, no partial master). **27 → 30 tests.** Master-bridge unaffected.

### Docs reconciled (commit `3603c7a`)
- `atlas/README.md` / `PLAN.md` rewritten from "Scout+Sage only" to the full 8-agent / 10-stage / two-gate / web-UI reality; `CHANGELOG.md` 0.3.0 entry added (2026-06-22); `PROJECT_CONTEXT.md` updated to 8 agents + Issue #2 RESOLVED; three stale "stub" docstrings (`pipeline.py`, `contracts/__init__.py`, `registry.py` comment) corrected. Prose/comments only — no logic touched.

## Issue #2 — RESOLVED and reconciled

Direction A (Mason brand chips) and Direction B (Magpie relevance sourcing) **cooperate** (verified by Auditor-B): Iris retags model shots `kind:brand` → Magpie skips render-kinds → Mason renders the chip with precedence over any sourced asset. The two known holes are now closed: the **named-model gap** (H1 — generic lineups fall back to the full roster) and the **relevance degeneracy** (H2 — single-token coincidences can no longer ship). Both committed with tests.

## Deliberately deferred (documented, lower-leverage)
- **H4** in-flight job double-dispatch guard, **M2** cross-process `chat_state.json` race, **C6** runtime-estimate accuracy, the `running→failed` reconciliation on external kill, and the factcheck source-text corroboration (currently a same-model LLM-judge). These are real but medium and don't move §6 output quality; carried into the FINAL_REPORT open-items list.

## Commits (this phase)
`d67c2e2` Vera · `281d278` Magpie relevance · `0e58a1a` Mason render bundle · `7c84a82` model IDs · `bf218e9` TTS · `c05691b` Scout fixture · `3603c7a` docs · `4594567` gitignore+reports.
