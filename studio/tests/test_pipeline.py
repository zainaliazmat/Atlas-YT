"""Front-of-pipeline tests for studio.pipeline (offline; the LLM/engines are mocked).

Covers: research_brief + script production, the HyperFrames-native project layout,
and the hard fact-check gate — passed, blocked, and proof that a block can never be
approved away (only a re-check on a fixed script clears it).
"""

from __future__ import annotations

import json

import pytest

from studio import config, engines, pipeline

BRIEF = {"topic": "Why social media is addictive", "angle": "the design is deliberate"}


# --- canned engine outputs (stand in for the real Sage/Marlow engines) -------
def _fake_pack(topic, angle=None):
    return {
        "topic": topic,
        "angle": angle,
        "overview": "Platforms engineer compulsion.",
        "verified_facts": [
            {"claim": "Pull-to-refresh mimics a slot machine.", "sources": [0, 1], "confidence": "high"},
        ],
        "myths_and_corrections": [],
        "contested_or_uncertain": [],
        "key_statistics": [],
        "sources": [
            {"url": "https://example.org/a", "title": "A"},
            {"url": "https://example.org/b", "title": "B"},
        ],
    }


def _fake_script(brief):
    return {
        "working_title": "The Dark Pattern",
        "hook": "You didn't choose to scroll.",
        "cta": "Log off.",
        "total_scenes": 2,
        "est_runtime_sec": 30,
        "scenes": [
            {"scene_no": 1, "beat": "hook", "point": "It's designed",
             "narration": "You didn't choose to scroll.", "on_screen_text": "DESIGNED",
             "visual_note": "", "duration_est_sec": 5, "claims": []},
            {"scene_no": 2, "beat": "evidence", "point": "Slot machine",
             "narration": "Pull-to-refresh is a slot machine.", "on_screen_text": "SLOT MACHINE",
             "visual_note": "", "duration_est_sec": 6,
             "claims": [{"claim_id": "c1", "text": "Pull-to-refresh mimics a slot machine.", "support": "F1"}]},
        ],
    }


def _report(verdict):
    if verdict == "pass":
        claims = [{"claim_id": "c1", "scene_no": 2, "status": "verified", "sources": [0]}]
        summary = {"verified": 1, "flagged": 0, "unverifiable": 0}
    else:
        claims = [{"claim_id": "c1", "scene_no": 2, "status": "flagged", "sources": []}]
        summary = {"verified": 0, "flagged": 1, "unverifiable": 0}
    return {"verdict": verdict, "summary": summary, "claims": claims}


@pytest.fixture
def projects(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    return config.PROJECTS_DIR


# --- the happy path: brief + script + passed gate ---------------------------
def test_front_pipeline_passes(projects):
    state = pipeline.produce(
        BRIEF, "addictive",
        stop_after="factcheck",
        research_fn=_fake_pack,
        script_fn=_fake_script,
        factcheck_fn=lambda s, b: _report("pass"),
    )
    pdir = projects / "addictive"

    # research_brief.json valid
    brief = json.loads((pdir / "research_brief.json").read_text())
    assert brief["topic"] == BRIEF["topic"]
    assert brief["verified_facts"]
    assert brief["schema_version"] == pipeline.SCHEMA_VERSION

    # script.json valid (one-point-per-scene shape)
    script = json.loads((pdir / "script.json").read_text())
    assert script["scenes"][0]["narration"]
    assert "on_screen_text" in script["scenes"][1]
    assert script["scenes"][1]["claims"][0]["claim_id"] == "c1"

    # gate passed
    assert state["status"] == "passed_factcheck"
    assert state["gates"]["factcheck"]["verdict"] == "pass"
    assert (pdir / "factcheck_report.json").exists()


# --- HyperFrames-native layout mirrors the reference ------------------------
def test_project_layout_is_hyperframes_native(projects):
    pipeline.produce(BRIEF, "layout", stop_after="factcheck",
                     research_fn=_fake_pack, script_fn=_fake_script,
                     factcheck_fn=lambda s, b: _report("pass"))
    pdir = projects / "layout"
    assert (pdir / "meta.json").exists()
    assert (pdir / "hyperframes.json").exists()
    assert (pdir / "assets").is_dir()
    assert (pdir / "compositions").is_dir()

    pkg = json.loads((pdir / "package.json").read_text())
    assert pkg["name"] == "layout"
    assert pkg["type"] == "module"
    # pinned to hyperframes@0.7.10 with dev/check/render scripts
    assert "hyperframes@0.7.10" in pkg["scripts"]["render"]
    assert all(k in pkg["scripts"] for k in ("dev", "check", "render"))

    hf = json.loads((pdir / "hyperframes.json").read_text())
    assert hf["paths"]["assets"] == "assets"

    meta = json.loads((pdir / "meta.json").read_text())
    assert meta["id"] == "layout"


# --- the gate blocks on a flagged claim -------------------------------------
def test_factcheck_gate_blocks(projects):
    state = pipeline.produce(BRIEF, "blocked",
                             research_fn=_fake_pack, script_fn=_fake_script,
                             factcheck_fn=lambda s, b: _report("block"))
    pdir = projects / "blocked"
    assert state["status"] == pipeline.STATUS_BLOCKED
    assert state["gates"]["factcheck"]["verdict"] == "block"
    assert state["gates"]["factcheck"]["approvable"] is False
    assert state["stages"]["factcheck"]["status"] == "blocked"
    # the report is still written for inspection
    report = json.loads((pdir / "factcheck_report.json").read_text())
    assert report["verdict"] == "block"


# --- a block can NEVER be approved away -------------------------------------
def test_block_cannot_be_approved_away(projects):
    # first run blocks
    pipeline.produce(BRIEF, "earn",
                     research_fn=_fake_pack, script_fn=_fake_script,
                     factcheck_fn=lambda s, b: _report("block"))

    # resume WITH approval but the check STILL blocks -> stays blocked (not dismissed)
    state = pipeline.produce(BRIEF, "earn", approve={"factcheck"},
                             research_fn=_fake_pack, script_fn=_fake_script,
                             factcheck_fn=lambda s, b: _report("block"))
    assert state["status"] == pipeline.STATUS_BLOCKED

    # resume WITHOUT approval -> remains paused, no silent pass
    state = pipeline.produce(BRIEF, "earn",
                             research_fn=_fake_pack, script_fn=_fake_script,
                             factcheck_fn=lambda s, b: _report("pass"))
    assert state["status"] == pipeline.STATUS_BLOCKED

    # resume WITH approval and a now-passing check (fixed script) -> earned pass
    state = pipeline.produce(BRIEF, "earn", approve={"factcheck"}, stop_after="factcheck",
                             research_fn=_fake_pack, script_fn=_fake_script,
                             factcheck_fn=lambda s, b: _report("pass"))
    assert state["status"] == "passed_factcheck"


# --- resume skips done stages (idempotent) ----------------------------------
def test_resume_skips_completed_stages(projects):
    calls = {"research": 0, "script": 0}

    def research_fn(t, a):
        calls["research"] += 1
        return _fake_pack(t, a)

    def script_fn(b):
        calls["script"] += 1
        return _fake_script(b)

    pipeline.produce(BRIEF, "resume", stop_after="factcheck",
                     research_fn=research_fn, script_fn=script_fn,
                     factcheck_fn=lambda s, b: _report("pass"))
    # second produce on a completed project re-does nothing
    pipeline.produce(BRIEF, "resume", stop_after="factcheck",
                     research_fn=research_fn, script_fn=script_fn,
                     factcheck_fn=lambda s, b: _report("pass"))
    assert calls == {"research": 1, "script": 1}


# --- the DEFAULT path routes through the real engine seams (reuse wiring) ----
def test_default_path_calls_real_engine_seams(projects, monkeypatch):
    seen = {}

    def fake_research(t, a=None):
        seen["research"] = (t, a)
        return _fake_pack(t, a)

    def fake_script(b, **k):
        seen["script"] = True
        return _fake_script(b)

    def fake_factcheck(s, b, **k):
        seen["factcheck"] = True
        return _report("pass")

    monkeypatch.setattr(engines, "research", fake_research)
    monkeypatch.setattr(engines, "write_script", fake_script)
    monkeypatch.setattr(engines, "factcheck", fake_factcheck)

    state = pipeline.produce(BRIEF, "default", stop_after="factcheck")  # NO overrides -> uses studio.engines.*
    assert seen["research"][0] == BRIEF["topic"]
    assert seen["script"] and seen["factcheck"]
    assert state["status"] == "passed_factcheck"
