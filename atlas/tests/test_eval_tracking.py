"""Tests for the EVAL TRACKING STORE (atlas/eval/tracking.py).

Fully deterministic: every record_run passes an explicit ts. Uses tmp_path so no
real runs/ dir is touched. Variance is POPULATION variance (see tracking.py).
"""
from __future__ import annotations

import statistics

import pytest

from eval.tracking import TrackingStore


def _row(prop="speech_cadence", stage="narration", *, measured_value=None,
         passed=None, **extra):
    """Minimal canonical-ish row; only prop+stage are required by record_run."""
    row = {
        "artifact": "script.json",
        "stage": stage,
        "prop": prop,
        "kind": "judged",
        "measured_value": measured_value,
        "passed": passed,
        "placeholder": False,
        "rolls_up_to": [],
    }
    row.update(extra)
    return row


# (a) two runs -> all_rows count + runs() order ------------------------------
def test_records_two_runs_count_and_order(tmp_path):
    store = TrackingStore(tmp_path / "log.jsonl")
    n1 = store.record_run([_row(), _row(prop="info_density")],
                          run_id="run-A", ts=1000.0)
    n2 = store.record_run([_row()], run_id="run-B", ts=2000.0)

    assert n1 == 2
    assert n2 == 1
    assert len(store.all_rows()) == 3
    assert store.runs() == ["run-A", "run-B"]


# (b) filters -----------------------------------------------------------------
def test_filters_by_prop_and_run(tmp_path):
    store = TrackingStore(tmp_path / "log.jsonl")
    store.record_run(
        [_row(prop="speech_cadence"), _row(prop="info_density")],
        run_id="run-A", ts=1.0,
    )
    store.record_run([_row(prop="speech_cadence")], run_id="run-B", ts=2.0)

    cadence = store.rows(prop="speech_cadence")
    assert len(cadence) == 2
    assert all(r["prop"] == "speech_cadence" for r in cadence)

    run_a = store.rows(run_id="run-A")
    assert len(run_a) == 2
    assert all(r["run_id"] == "run-A" for r in run_a)

    assert store.rows(run_id="run-A", prop="info_density")[0]["prop"] == "info_density"


# (c) noise floor over K=5 (population variance) ------------------------------
def test_noise_floor_k5(tmp_path):
    store = TrackingStore(tmp_path / "log.jsonl")
    vals = [0.80, 0.84, 0.79, 0.82, 0.85]
    for i, v in enumerate(vals):
        store.record_run(
            [_row(prop="hook_strength", measured_value=v)],
            run_id=f"run-{i}", change_id="baseline", ts=float(i),
        )

    nf = store.noise_floor("hook_strength")
    assert nf["n"] == 5
    assert nf["values"] == vals
    assert nf["mean"] == pytest.approx(statistics.fmean(vals))
    # POPULATION variance/std (documented choice).
    assert nf["variance"] == pytest.approx(statistics.pvariance(vals))
    assert nf["std"] == pytest.approx(statistics.pstdev(vals))
    assert nf["min"] == min(vals)
    assert nf["max"] == max(vals)

    # run_ids subset narrows the floor.
    sub = store.noise_floor("hook_strength", run_ids=["run-0", "run-1"])
    assert sub["n"] == 2
    assert sub["values"] == [0.80, 0.84]

    # Unknown prop -> zero floor, never raises.
    empty = store.noise_floor("nope")
    assert empty["n"] == 0 and empty["values"] == []


def test_noise_floor_ignores_none_and_bool(tmp_path):
    store = TrackingStore(tmp_path / "log.jsonl")
    store.record_run([_row(prop="p", measured_value=None)], run_id="r0", ts=0.0)
    store.record_run([_row(prop="p", measured_value=True)], run_id="r1", ts=1.0)
    store.record_run([_row(prop="p", measured_value=0.5)], run_id="r2", ts=2.0)
    nf = store.noise_floor("p")
    assert nf["n"] == 1
    assert nf["values"] == [0.5]


# (d) crash-safety: garbage tail line is skipped, never raises ----------------
def test_tolerates_corrupt_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    store = TrackingStore(p)
    store.record_run([_row(prop="a", measured_value=1.0)], run_id="run-A", ts=1.0)

    # Simulate a process killed mid-write: append a truncated/garbage line.
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"prop": "b", "stage": "x", "measu')  # torn, no newline

    rows = store.all_rows()  # must not raise
    assert len(rows) == 1
    assert rows[0]["prop"] == "a"
    assert store.noise_floor("a")["n"] == 1

    # A good run appended AFTER the torn line is still readable.
    store.record_run([_row(prop="c", measured_value=2.0)], run_id="run-B", ts=2.0)
    props = {r["prop"] for r in store.all_rows()}
    assert props == {"a", "c"}


# (e) appends accumulate across a fresh store on the same path ----------------
def test_appends_accumulate_across_instances(tmp_path):
    p = tmp_path / "log.jsonl"
    TrackingStore(p).record_run([_row(prop="a")], run_id="run-A", ts=1.0)

    reopened = TrackingStore(p)  # brand-new instance, same path
    reopened.record_run([_row(prop="b")], run_id="run-B", ts=2.0)

    assert reopened.runs() == ["run-A", "run-B"]
    assert len(reopened.all_rows()) == 2


# (f) pass_rate counts -------------------------------------------------------
def test_pass_rate(tmp_path):
    store = TrackingStore(tmp_path / "log.jsonl")
    store.record_run(
        [
            _row(prop="p1", passed=True),
            _row(prop="p2", passed=False),
            _row(prop="p3", passed=None),   # ungated
            _row(prop="p4", passed=True),
        ],
        run_id="run-A", ts=1.0,
    )
    store.record_run([_row(prop="p5", passed=False)], run_id="run-B", ts=2.0)

    overall = store.pass_rate()
    assert overall == {"passed": 2, "failed": 2, "ungated": 1, "total": 5}

    just_a = store.pass_rate(run_id="run-A")
    assert just_a == {"passed": 2, "failed": 1, "ungated": 1, "total": 4}


# extra: stamping precedence + required-field validation ----------------------
def test_stamp_precedence_and_validation(tmp_path):
    store = TrackingStore(tmp_path / "log.jsonl")
    # Row's own run_id/change_id/ts win over kwargs.
    store.record_run(
        [_row(prop="p", run_id="own-run", change_id="own-change", ts=999.0)],
        run_id="kwarg-run", change_id="kwarg-change", ts=1.0,
    )
    r = store.all_rows()[0]
    assert r["run_id"] == "own-run"
    assert r["change_id"] == "own-change"
    assert r["ts"] == 999.0

    # Missing prop/stage raises.
    with pytest.raises(ValueError):
        store.record_run([{"stage": "narration"}], run_id="x", ts=1.0)
    with pytest.raises(ValueError):
        store.record_run([{"prop": "speech_cadence"}], run_id="x", ts=1.0)


def test_empty_batch_and_missing_file(tmp_path):
    store = TrackingStore(tmp_path / "nope.jsonl")
    assert store.all_rows() == []        # missing file -> []
    assert store.runs() == []
    assert store.record_run([], run_id="x", ts=1.0) == 0
    assert not (tmp_path / "nope.jsonl").exists()  # empty batch writes nothing
