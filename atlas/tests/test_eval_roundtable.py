"""Tests for the roundtable-log analyzer (the eval system's process side-channel).

The analyzer reads `roundtable_log.json` — the artifact scriptwriter/roundtable.py
writes — and turns the internal Critic→Researcher→Craftsman record into process
diagnostics + a coach-facing context. It is a SIDE CHANNEL: it never gates against
the CEO-owned rubric (process metrics are not quality bands), and it degrades to
None when no log exists. These tests pin both the happy path and the broken-loop
flags the coaches rely on.
"""
from __future__ import annotations

import json

import rubric
from eval import loop
from eval.analyzers import roundtable as rt
from eval.inspector import run_inspection


# --- fixtures: a real-shaped roundtable_log.json ---------------------------

def _healthy_log():
    """Mirrors the keys scriptwriter/roundtable.py actually writes."""
    draft = {"scenes": [{"narration": "old a"}, {"narration": "old b"},
                        {"narration": "keep c"}], "hook": "old hook"}
    enhanced = {"scenes": [{"narration": "new a"}, {"narration": "new b"},
                          {"narration": "keep c"}], "hook": "new hook"}
    return {
        "roundtable_version": "1.0",
        "specialist": "Marlow",
        "role": "Scriptwriter",
        "duration_seconds": 12.5,
        "draft_artifact": draft,
        "enhanced_artifact": enhanced,
        "criticisms": [
            {"rank": 1, "severity": "critical", "principle_violated": "SKILL.md Rule 1",
             "diagnosis": "the hook buries the surprise", "location": "scene 1"},
            {"rank": 2, "severity": "major", "principle_violated": "SKILL.md Rule 4",
             "diagnosis": "scene 2 is abstract", "location": "scene 2"},
        ],
        "research_findings": [
            {"target_criticism_rank": 1, "found_detail": "34% drop in Q3 2019",
             "detail_type": "statistic", "source_url": "https://example.com/a"},
            {"target_criticism_rank": 2, "found_detail": "a vivid anecdote",
             "detail_type": "anecdote", "source_url": ""},
        ],
        "diff_summary": {"scenes_modified": 2,
                         "key_changes": [{"scene": 1, "change_type": "narration_rewritten"},
                                         {"change_type": "hook_rewritten"}]},
        "error": None,
    }


def _write_log(d, log):
    d.mkdir(parents=True, exist_ok=True)
    (d / "roundtable_log.json").write_text(json.dumps(log))
    return d


# --- analyze_roundtable ----------------------------------------------------

def test_no_log_returns_none(tmp_path):
    assert rt.analyze_roundtable(tmp_path) is None


def test_healthy_log_diagnostics(tmp_path):
    d = _write_log(tmp_path / "p", _healthy_log())
    m = rt.analyze_roundtable(d)
    assert m is not None
    assert m["roundtable_active"]["value"] is True
    # severity distribution counted correctly
    dist = m["critic_severity_distribution"]["value"]
    assert dist == {"critical": 1, "major": 1, "moderate": 0}
    # one finding carries a source, one does not
    prod = m["researcher_productivity"]["value"]
    assert prod["total_findings"] == 2 and prod["findings_with_sources"] == 1
    # craftsman changed 2 of 3 scenes
    impact = m["craftsman_impact"]["value"]
    assert impact["scenes_modified"] == 2 and impact["total_scenes"] == 3
    # healthy process
    assert m["roundtable_process_health"]["value"] == "healthy"
    # no warning flags raised on a healthy log
    assert "critic_leniency_flag" not in m
    assert "researcher_source_gap" not in m
    assert "craftsman_no_op_flag" not in m


def test_lenient_critic_flagged(tmp_path):
    log = _healthy_log()
    for c in log["criticisms"]:
        c["severity"] = "moderate"
    d = _write_log(tmp_path / "p", log)
    m = rt.analyze_roundtable(d)
    assert m["critic_leniency_flag"]["value"] == "warning"


def test_researcher_source_gap_flagged(tmp_path):
    log = _healthy_log()
    for f in log["research_findings"]:
        f["source_url"] = ""
    d = _write_log(tmp_path / "p", log)
    m = rt.analyze_roundtable(d)
    assert m["researcher_source_gap"]["value"] == "warning"


def test_craftsman_no_op_is_critical(tmp_path):
    log = _healthy_log()
    # criticisms exist but the craftsman changed nothing
    log["enhanced_artifact"] = json.loads(json.dumps(log["draft_artifact"]))
    log["diff_summary"] = {"scenes_modified": 0, "key_changes": []}
    d = _write_log(tmp_path / "p", log)
    m = rt.analyze_roundtable(d)
    assert m["craftsman_no_op_flag"]["value"] == "critical"
    assert "identical" in m["roundtable_process_health"]["value"]


# --- get_coach_context -----------------------------------------------------

def test_coach_context_none_without_log(tmp_path):
    assert rt.get_coach_context(tmp_path) is None


def test_coach_context_shape(tmp_path):
    d = _write_log(tmp_path / "p", _healthy_log())
    ctx = rt.get_coach_context(d)
    assert ctx["roundtable_used"] is True
    assert ctx["specialist"] == "Marlow"
    assert len(ctx["criticisms"]) == 2
    assert {"severity", "principle", "diagnosis"} <= set(ctx["criticisms"][0])
    assert ctx["research_quality"] == {"total_findings": 2, "findings_with_sources": 1}
    assert ctx["craftsman_impact"]["scenes_modified"] == 2
    assert ctx["process_health"] == "healthy"


# --- inspector wiring ------------------------------------------------------

def test_inspector_attaches_roundtable_when_present(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "script.json").write_text(json.dumps({
        "schema_version": "1.0", "working_title": "T", "hook": "h", "cta": "c",
        "total_scenes": 1, "est_runtime_sec": 7,
        "scenes": [{"scene_no": 1, "point": "p", "narration": "word " * 25,
                    "on_screen_text": "x", "duration_est_sec": 7.0, "claims": []}]}))
    _write_log(d, _healthy_log())
    sc = run_inspection(d, run_judged=False, write=False, track=False)
    assert sc["roundtable_analyzed"] is True
    assert sc["roundtable"]["roundtable_active"]["value"] is True


def test_inspector_marks_absent_roundtable(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "script.json").write_text(json.dumps({
        "schema_version": "1.0", "working_title": "T", "hook": "h", "cta": "c",
        "total_scenes": 1, "est_runtime_sec": 7,
        "scenes": [{"scene_no": 1, "point": "p", "narration": "word " * 25,
                    "on_screen_text": "x", "duration_est_sec": 7.0, "claims": []}]}))
    sc = run_inspection(d, run_judged=False, write=False, track=False)
    assert sc["roundtable_analyzed"] is False
    assert "roundtable" not in sc


# --- loop threads roundtable_context to the owning coach -------------------

def _target(band_id="script:info_density", stage="script"):
    b = rubric.band_by_id(band_id)
    return {"band_id": band_id, "stage": stage, "owner": b["owner"],
            "comparator": b["comparator"], "measured_value": 9.0,
            "band_min": b.get("min"), "band_max": b.get("max"),
            "band_target": b.get("target")}


def test_delegate_to_coach_forwards_roundtable_context(monkeypatch):
    seen = {}

    class FakeAdapter:
        def run_job(self, job, progress, **params):
            seen.update(params)
            return {"ok": True, "text": "Tighten the claims.", "source": "llm"}

    import registry
    monkeypatch.setattr(registry, "build_adapters",
                        lambda: {"editorial_coach": FakeAdapter()})
    ctx = {"roundtable_used": True, "specialist": "Marlow",
           "process_health": "healthy"}
    res = loop.delegate_to_coach(_target(), "LOWER it", " keep X.",
                                 roundtable_context=ctx)
    assert res["coach"] == "editorial_coach"
    # the process data reached the adapter -> the coach engine
    assert seen["roundtable_context"] == ctx


def test_propose_fix_threads_context_through_use_coaches(monkeypatch):
    captured = {}

    def fake_delegate(target, direction, preserve, *, research=False,
                      roundtable_context=None):
        captured["ctx"] = roundtable_context
        return {"addendum": "## note\nfix\n", "coach": "editorial_coach",
                "source": "llm"}

    monkeypatch.setattr(loop, "delegate_to_coach", fake_delegate)
    ctx = {"roundtable_used": True, "specialist": "Marlow"}
    loop.propose_fix(_target(), use_coaches=True, roundtable_context=ctx)
    assert captured["ctx"] == ctx
