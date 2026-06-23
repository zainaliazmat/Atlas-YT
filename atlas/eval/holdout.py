"""Held-out generalization guard for the improvement loop (Phase 2, step 2).

The danger the design docs name explicitly: *the loop overfits the rubric* — it
tunes a persona until the OPTIMIZE projects pass while quietly breaking others.
The guard is a classic train/test split: the loop may only ever measure itself
against the OPTIMIZE set; a separate HELD-OUT set it never optimizes against is
used by the Verifier to confirm an accepted change GENERALIZES (real improvement)
rather than MEMORIZES (overfit).

Split (the 4 completed projects):
  * OPTIMIZE  : coffee-vs-tea, how-noise-cancelling-headphones
  * HELD-OUT  : gpt-4o-...-comparison (the gold fixture), the-first-job...jensen

`verify_generalization(addendum)` re-runs the owning engine (Marlow, for the
script stage) on each HELD-OUT project's real research_brief WITH the soft
addendum applied through the engine's chat seam, then checks that no property
which PASSED on that project's existing artifact regresses to FAILING. A
regression on the held-out set ⇒ the change does not generalize ⇒ reject.

Everything is injectable (the engine re-run is a seam) so unit tests stay offline
and deterministic; the live demonstration uses the real engine + subscription LLM.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Callable, Optional

from eval import rollup
from eval.types import EvalContext, Measurement
from eval.analyzers import text as text_an

_ATLAS_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _ATLAS_DIR.parent
_PROJECTS = _ATLAS_DIR / "projects"

# Project SLUG PREFIXES for the split (a prefix tolerates the timestamp suffix and
# the existence of several re-runs — we resolve to the first matching dir).
OPTIMIZE_PREFIXES = (
    "coffee-vs-tea-which-actually-gives-you-better-ener",
    "how-noise-cancelling-headphones-actually-work-a-ti",
)
HELDOUT_PREFIXES = (
    "gpt-4o-vs-claude-vs-gemini-vs-deepseek-comparison-",
    "the-first-job-ai-will-destroy-jensen-huang-predict",
)


def _resolve(prefixes: tuple[str, ...]) -> list[Path]:
    """First existing project dir for each prefix that has a research_brief +
    script (the artifacts the script-stage verifier needs)."""
    out: list[Path] = []
    if not _PROJECTS.is_dir():
        return out
    for pre in prefixes:
        matches = sorted(d for d in _PROJECTS.iterdir()
                         if d.is_dir() and d.name.startswith(pre)
                         and (d / "research_brief.json").is_file()
                         and (d / "script.json").is_file())
        if matches:
            out.append(matches[0])
    return out


def optimize_projects() -> list[Path]:
    return _resolve(OPTIMIZE_PREFIXES)


def heldout_projects() -> list[Path]:
    return _resolve(HELDOUT_PREFIXES)


# --------------------------------------------------------------------------- #
# Script-stage engine re-run (the soft addendum injected through the chat seam)
# --------------------------------------------------------------------------- #

def _script_engine():
    sw = str(_REPO_DIR / "scriptwriter")
    if sw not in sys.path:
        sys.path.insert(0, sw)
    return importlib.import_module("script_engine")


def rerun_script(brief: dict, addendum: str, *,
                 base_chat_fn: Optional[Callable] = None,
                 workdir: Optional[Path] = None) -> list[Measurement]:
    """Re-run Marlow on `brief` with `addendum` prepended to the system prompt,
    write the script to a temp dir, and return its script-stage measurements."""
    se = _script_engine()
    base = base_chat_fn or se.llm.chat

    def wrapped(system: str, user: str) -> str:
        return base(f"{system}\n\n{addendum}", user)

    new_script = se.write_script(brief, chat_fn=wrapped)
    workdir = workdir or (_PROJECTS / "_eval_holdout_tmp")
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "script.json").write_text(json.dumps(new_script))
    ctx = EvalContext(workdir)
    return [m for m in text_an.analyze(ctx) if m.stage == "script"]


def _passing_props(measurements: list[Measurement]) -> dict[str, dict]:
    rows = {f"{m.stage}:{m.prop}": rollup.measurement_to_row(m) for m in measurements}
    return rows


# --------------------------------------------------------------------------- #
# The Verifier
# --------------------------------------------------------------------------- #

def _clearly_regressed(cand_row: dict, band_margin: float) -> bool:
    """Did a (baseline-passing) property CLEARLY fail on the held-out re-gen?

    Held-out verification re-GENERATES the engine, so it inherits the generator's
    run-to-run variance (for scripts, σ can be large). A borderline pass→fail flip
    on a single re-gen is as likely noise as a real regression. With `band_margin`
    (a fraction of band width), a range/gte/lte property counts as regressed only
    if the candidate value sits OUTSIDE the band by more than that margin; boolean
    /eq properties and any non-numeric failure are always counted (no noise band).
    band_margin=0.0 reproduces the strict (conservative) behavior."""
    if cand_row.get("passed") is not False:
        return False
    if band_margin <= 0:
        return True
    comp = cand_row.get("comparator")
    v = cand_row.get("measured_value")
    lo, hi = cand_row.get("band_min"), cand_row.get("band_max")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return True                       # unmeasurable failure -> count it
    if comp == "range" and lo is not None and hi is not None:
        tol = band_margin * (hi - lo)
        return v < (lo - tol) or v > (hi + tol)
    if comp == "gte" and lo is not None:
        return v < lo - band_margin * abs(lo or 1.0)
    if comp == "lte" and hi is not None:
        return v > hi + band_margin * abs(hi or 1.0)
    return True                           # eq / eq_true / unknown -> strict


def verify_generalization(addendum: str, *, stage: str = "script",
                          base_chat_fn: Optional[Callable] = None,
                          rerun_fn: Optional[Callable] = None,
                          projects: Optional[list[Path]] = None,
                          band_margin: float = 0.0) -> dict:
    """Apply `addendum` to each HELD-OUT project and confirm it does not regress.

    For each held-out project: baseline = its EXISTING artifact's measurements;
    candidate = a fresh engine run WITH the addendum. A regression = a property
    that PASSED in baseline FAILS in candidate. If ANY held-out project regresses,
    the change does not generalize.

    `rerun_fn(brief, addendum) -> list[Measurement]` is injectable for offline
    tests; the default re-runs the real script engine.
    """
    if stage != "script":
        # only the affordable, render-free script stage is wired for live re-run;
        # other stages would need a TTS/render and are out of this loop's budget.
        return {"stage": stage, "supported": False,
                "note": f"held-out re-run not wired for stage '{stage}' (needs render/TTS)"}

    rerun = rerun_fn or (lambda brief, add: rerun_script(brief, add, base_chat_fn=base_chat_fn))
    projects = projects if projects is not None else heldout_projects()

    per_project = {}
    all_regressions: list[str] = []
    for d in projects:
        ctx = EvalContext(d)
        brief = ctx.research_brief
        if brief is None:                      # missing/unparseable (an empty {} is still runnable)
            per_project[d.name] = {"skipped": "no research_brief"}
            continue
        baseline = [m for m in text_an.analyze(ctx) if m.stage == stage]
        candidate = rerun(brief, addendum)
        base_rows = _passing_props(baseline)
        cand_rows = _passing_props(candidate)
        regressions = [bid for bid, br in base_rows.items()
                       if br.get("passed") is True
                       and _clearly_regressed(cand_rows.get(bid, {}), band_margin)]
        per_project[d.name] = {
            "regressions": regressions,
            "baseline_passed": [b for b, r in base_rows.items() if r.get("passed") is True],
        }
        all_regressions.extend(f"{d.name}:{bid}" for bid in regressions)

    return {
        "stage": stage,
        "supported": True,
        "n_heldout": len(projects),
        "regressions": all_regressions,
        "generalizes": len(all_regressions) == 0,
        "per_project": per_project,
        "band_margin": band_margin,
        "note": ("A held-out regression means the soft change overfits the optimize "
                 "set — reject it. Held-out verification re-GENERATES the engine, so it "
                 f"inherits generator variance; band_margin={band_margin} filters "
                 "borderline noise flips (0.0 = strict). A regression that survives the "
                 "margin is treated as a genuine generalization failure."),
    }
