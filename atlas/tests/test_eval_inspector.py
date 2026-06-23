"""Integration tests for the Inspector orchestrator. Kept fast by running on a
synthetic minimal project (no media) with a fake judged chat_fn — the full
gold-fixture run is exercised via the CLI / per-analyzer tests / live-QA."""
from __future__ import annotations

import json

from eval.inspector import run_inspection, gather_measurements, format_summary
from eval.types import EvalContext
from eval.tracking import TrackingStore


def _mini_project(tmp_path):
    """A minimal project dir: a real script.json, no media. Audio/video analyzers
    degrade gracefully; the text analyzer produces real script rows."""
    d = tmp_path / "proj-mini"
    d.mkdir()
    script = {
        "schema_version": "1.0",
        "working_title": "Test",
        "hook": "A strong opening line that pulls you in immediately.",
        "cta": "Tell me what you think below.",
        "total_scenes": 10,
        "est_runtime_sec": 75,
        "scenes": [
            {"scene_no": i, "point": f"point {i}",
             "narration": "word " * 25, "on_screen_text": "short text",
             "duration_est_sec": 7.0, "claims": []}
            for i in range(1, 11)
        ],
    }
    (d / "script.json").write_text(json.dumps(script))
    return d


def _fake_chat(system, user):
    # The judged harness seeds the A/B order, so a fixed "WINNER: A" is NOT a
    # fixed candidate vote — it exercises the offline path and yields a valid
    # rate in [0,1]. (Slot-aware favoring is covered in test_eval_judged.py.)
    return "WINNER: A"


def test_inspector_runs_on_minimal_project_no_media(tmp_path):
    d = _mini_project(tmp_path)
    sc = run_inspection(d, run_judged=False, write=False, track=False)
    assert sc["overall"] in {"PASS", "FAIL", "BLOCKED_BY_FLOOR"}
    assert sc["summary"]["total_rows"] > 0
    # script properties were measured
    props = {r["band_id"] for r in sc["rows"]}
    assert "script:scene_count" in props
    # audio/video properties degraded gracefully (value None, error set), no crash
    audio_rows = [r for r in sc["rows"] if r["stage"] == "audiomix"]
    assert audio_rows and all(r["measured_value"] is None for r in audio_rows)


def test_inspector_with_fake_judged(tmp_path):
    d = _mini_project(tmp_path)
    sc = run_inspection(d, run_judged=True, chat_fn=_fake_chat, n=5,
                        write=False, track=False)
    judged = [r for r in sc["rows"] if r["kind"] == "judged"]
    assert len(judged) == 2  # hook_strength + overall_polish
    hook = [r for r in judged if r["band_id"] == "script:hook_strength"][0]
    assert hook["measured_value"] is not None and 0.0 <= hook["measured_value"] <= 1.0


def test_inspector_writes_scorecard_and_tracks(tmp_path):
    d = _mini_project(tmp_path)
    store = TrackingStore(tmp_path / "runs.jsonl")
    sc = run_inspection(d, run_judged=False, write=True, track=True,
                        store=store, run_id="run-1", change_id="baseline")
    # scorecard persisted in the project dir (eval artifact, not a pipeline artifact)
    assert (d / "eval_scorecard.json").is_file()
    persisted = json.loads((d / "eval_scorecard.json").read_text())
    assert persisted["run_id"] == "run-1"
    # rows recorded to the tracking store
    rows = store.rows(run_id="run-1")
    assert len(rows) == sc["summary"]["total_rows"]


def test_gather_measurements_graceful_on_empty(tmp_path):
    ctx = EvalContext(tmp_path)
    measurements, errors = gather_measurements(ctx, run_judged=False)
    assert measurements  # analyzers still return rows (value=None)
    assert all(m.value is None for m in measurements)
    assert errors == {}  # graceful degradation is inside analyzers, not a crash


def test_format_summary_is_string(tmp_path):
    d = _mini_project(tmp_path)
    sc = run_inspection(d, run_judged=False, write=False, track=False)
    out = format_summary(sc)
    assert "OVERALL" in out and "dimensions" in out
