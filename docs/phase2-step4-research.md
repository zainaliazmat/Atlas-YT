# Phase 2 — Step 4: bounded research / self-study for the coaches

> The last step in the plan. Each coach can now STUDY current best practice for a
> metric — but research only ever produces **hypotheses**; the rubric and the
> held-out set **prune**. "Research widens what to try; the eval decides what's kept."

## What was added

- **A web-search seam in each coach** (`editorial-coach/search.py`, `production-coach/search.py`)
  — copied from Sage's proven seam: DuckDuckGo by default (NO API key), optional
  Tavily/Brave behind an env switch, every call wrapped (a flaky source degrades to
  empty, never crashes a loop).
- **`coach_engine.research_hypotheses(*, band_id, direction, search_fn=None, chat_fn=None,
  max_queries=2, max_hypotheses=4)`** — gathers current best-practice hypotheses for a
  metric. Pure + injectable (both seams). Returns `{hypotheses, sources, queries,
  n_results, budget}`. With a `chat_fn` it distills the findings into a few concise,
  TESTABLE coaching hypotheses; without one it falls back to the raw result
  titles/snippets. **Never raises** — research must never crash a loop.
- **`propose_addendum(..., research=False, search_fn=None, max_queries=2)`** — when
  `research=True` the coach folds the hypotheses into the authoring prompt and tags the
  result `source="llm-research"` (with the full research record attached for audit).
  The named band + direction still govern; a hypothesis is only ever a suggestion.
- **Wired through the loop**: `run_loop(use_coaches=True, research=True)` →
  `propose_fix(research=True)` → `delegate_to_coach(..., research=True)` → the owning
  coach's adapter. No orchestrator edits.

## The two non-negotiables, kept

1. **Research is hypotheses only; the rubric prunes.** A researched technique changes
   nothing on its own. The coach's addendum (whether or not it used research) must still
   clear EVERY loop gate — band, noise floor, no-regression, and the **held-out
   generalization check** — before it is accepted. A researched "best practice" that
   doesn't beat the held-out scorecard is discarded, exactly like any other candidate.
   (Proven offline in `atlas/tests/test_eval_research.py`:
   `test_researched_addendum_accepted_only_if_beats_heldout` — the same researched
   addendum is ACCEPTED when held-out passes and REJECTED when it doesn't.)
2. **Hard budget.** Research costs at most `max_queries` web calls per proposal (default
   2), capped well below any runaway; the loop's `max_iters` bounds the rest. The budget
   used is returned (`research.budget`) and the search seam self-throttles + degrades.

## Proof

- **Offline / deterministic** (no network, no LLM): 6 coach-engine research tests each
  (`{editorial,production}-coach/tests/test_research.py`) — hypotheses from injected
  search, budget cap, empty-search and search-failure degradation, research folded into
  the addendum, and the no-research path unchanged. Atlas side: `test_eval_research.py`
  — the `research` flag threads to the owning coach, and the held-out gate prunes a
  researched change that doesn't generalize.
- **Live seam** (free DuckDuckGo, no key, no subscription): a single real query for
  `script:hook_strength` returned 5 results within the 1-query budget and produced
  hypotheses from the findings — confirming the research dimension is live, not just
  injectable.

## Status

Step 4 complete. All four steps of the Phase-2 plan (calibrate → harden the loop →
split coaching → bounded research) are built and proven. The self-improvement program
now has: a calibrated, CEO-owned standard; a hardened, regression- and overfit-aware
loop that cannot rewrite its own success bar; two domain coaches the loop delegates to;
and a bounded research dimension that widens the search while the eval prunes.
