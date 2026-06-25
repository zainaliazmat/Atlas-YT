"""The Inspector — the read-only orchestrator of the evaluation foundation.

Runs every analyzer over ONE completed project, gates each measurement against
the CEO-owned rubric, rolls the results up into a scorecard, and (optionally)
persists the scorecard + appends the rows to the tracking store. It NEVER edits
the pipeline, the contracts, or the gates; it only reads a project dir.

Usage (CLI):
    cd atlas
    python -m eval.inspector projects/<slug>            # objective only (fast)
    python -m eval.inspector projects/<slug> --judged   # + ensembled LLM judge
    python -m eval.inspector projects/<slug> --judged --no-track

As a library:
    from eval.inspector import run_inspection
    sc = run_inspection(project_dir, run_judged=False)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Callable, Optional

import rubric
from eval import rollup
from eval.types import EvalContext, Measurement
from eval.analyzers import audio as audio_an
from eval.analyzers import video as video_an
from eval.analyzers import text as text_an
from eval.analyzers import roundtable as roundtable_an
from eval import judged as judged_an
from eval.tracking import TrackingStore

# objective analyzers always run; judged is opt-in (it costs LLM calls)
_OBJECTIVE = [("audio", audio_an.analyze),
              ("video", video_an.analyze),
              ("text", text_an.analyze)]


def _atomic_write_json(path: Path, obj: dict) -> None:
    """Write JSON atomically (temp + os.replace), mirroring the repo convention.
    Falls back to chat_state.atomic_write_json if available."""
    try:
        import chat_state  # atlas's atomic writer
        if hasattr(chat_state, "atomic_write_json"):
            chat_state.atomic_write_json(str(path), obj)
            return
    except Exception:
        pass
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, path)


def gather_measurements(ctx: EvalContext, *, run_judged: bool = True,
                        chat_fn: Optional[Callable] = None, n: int = 5,
                        seed: int = 0) -> tuple[list[Measurement], dict]:
    """Run analyzers, returning (measurements, analyzer_errors). One analyzer
    blowing up never kills the scorecard — its failure is recorded and the rest
    still run (graceful degradation)."""
    measurements: list[Measurement] = []
    errors: dict[str, str] = {}
    for name, fn in _OBJECTIVE:
        try:
            measurements.extend(fn(ctx))
        except Exception as e:  # pragma: no cover - defensive
            errors[name] = f"{type(e).__name__}: {e}"
    if run_judged:
        try:
            measurements.extend(judged_an.analyze(ctx, chat_fn=chat_fn, n=n, seed=seed))
        except Exception as e:  # pragma: no cover - defensive
            errors["judged"] = f"{type(e).__name__}: {e}"
    return measurements, errors


def run_inspection(project_dir: str | Path, *, chat_fn: Optional[Callable] = None,
                   run_judged: bool = True, n: int = 5, seed: int = 0,
                   run_id: Optional[str] = None, change_id: str = "baseline",
                   write: bool = True, track: bool = True,
                   store: Optional[TrackingStore] = None) -> dict:
    """Produce a full scorecard for one project. Read-only w.r.t. the project's
    pipeline artifacts; it only WRITES its own eval_scorecard.json (+ the
    tracking log), never a pipeline artifact, contract, or the rubric."""
    ctx = EvalContext(project_dir, run_id=run_id)
    run_id = ctx.run_id
    measurements, analyzer_errors = gather_measurements(
        ctx, run_judged=run_judged, chat_fn=chat_fn, n=n, seed=seed)

    scorecard = rollup.build_scorecard(measurements)
    ts = time.time()
    scorecard.update({
        "run_id": run_id,
        "change_id": change_id,
        "project_dir": str(ctx.dir),
        "judged_included": run_judged,
        "analyzer_errors": analyzer_errors,
        "generated_at": ts,
    })

    # Roundtable process diagnostics (a SIDE CHANNEL, not rubric-gated). When a
    # specialist's internal Critic→Researcher→Craftsman review left a
    # roundtable_log.json, attach the process read-out so the coaches + CEO can
    # see HOW the work was made, not just the final scores. Absent log ⇒ no-op.
    rt_diagnostics = roundtable_an.analyze_roundtable(ctx.dir)
    scorecard["roundtable_analyzed"] = rt_diagnostics is not None
    if rt_diagnostics is not None:
        scorecard["roundtable"] = rt_diagnostics

    if write:
        _atomic_write_json(ctx.dir / "eval_scorecard.json", scorecard)

    if track:
        store = store or TrackingStore()
        store.record_run(scorecard["rows"], run_id=run_id, change_id=change_id, ts=ts)

    return scorecard


def format_summary(sc: dict) -> str:
    """A compact human summary of a scorecard for the CLI / a gate card."""
    s = sc["summary"]
    lines = [
        f"Scorecard — {sc.get('project_dir','?')}",
        f"  rubric {sc['rubric_version']}  |  run_id {sc.get('run_id','?')}  |  change {sc.get('change_id','?')}",
        f"  OVERALL: {sc['overall']}   quality_score: "
        f"{('%.3f' % sc['quality_score']) if sc['quality_score'] is not None else 'n/a'}",
        f"  gated {s['gated']}  passed {s['passed']}  failed {s['failed']}  ungated {s['ungated']}  errors {s['errors']}",
        f"  floor: {'PASS' if sc['floor']['passed'] else 'FAIL ' + str([f['band_id'] for f in sc['floor']['failures']])}",
    ]
    if sc.get("decomposition_gap"):
        lines.append("  ⚠ decomposition gap: all locals pass but the holistic anchor failed — rubric may be missing a term")
    lines.append("  dimensions:")
    for g, d in sc["dimensions"].items():
        sc_str = f"{d['score']:.2f}" if d["score"] is not None else " n/a"
        lines.append(f"    {g} {d['name']:<18} score {sc_str}  ({d['passed']}/{d['gated']} gated, w={d['weight']})")
    fails = [r for r in sc["rows"] if r["passed"] is False]
    if fails:
        lines.append("  failing properties:")
        for r in fails:
            ph = " [placeholder band]" if r["placeholder"] else ""
            lines.append(f"    ✗ {r['band_id']:<34} value={r['measured_value']}  ({r['note']}){ph}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m eval.inspector <project_dir> [--judged] [--no-track] [--no-write] [-n N]")
        return 2
    project_dir = argv[0]
    run_judged = "--judged" in argv
    track = "--no-track" not in argv
    write = "--no-write" not in argv
    n = 5
    if "-n" in argv:
        try:
            n = int(argv[argv.index("-n") + 1])
        except (ValueError, IndexError):
            pass
    sc = run_inspection(project_dir, run_judged=run_judged, n=n, track=track, write=write)
    print(format_summary(sc))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
