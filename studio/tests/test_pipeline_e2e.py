"""End-to-end tests for the full v2 production spine (studio.pipeline.produce).

Every LLM + render + review seam is mocked, so the WHOLE flow — research → script →
factcheck★GATE → vo → compose → draft → review → final★GATE → video.mp4 — runs offline in
milliseconds. The tests assert:

  * the full flow runs to ``complete`` and writes video.mp4 (unattended, under budget);
  * BOTH human gates fire — the final gate PAUSES without approval and resumes on approval;
  * a ``block`` fact-check can NEVER be approved away (it re-runs on resume, still blocks,
    and the pipeline never advances past it);
  * ``--unattended`` HOLDS the final gate when a quality gate fails or the render is over
    budget;
  * determinism is enforced on the composed index.html.
"""

from __future__ import annotations

import json
from pathlib import Path

from studio import pipeline


# ----------------------------------------------------------------------
# fakes — valid artifacts at each seam, written where the spine expects them
# ----------------------------------------------------------------------
def _brief():
    return {"topic": "why infinite scroll is engineered", "angle": "empowerment"}


def fake_research(topic, angle):
    return {"topic": topic, "angle": angle,
            "verified_facts": ["fact a", "fact b"], "sources": ["https://example.org"]}


def fake_script(brief):
    return {"working_title": "T", "hook": "h", "cta": "c", "scenes": [
        {"scene_no": 1, "narration": "n1", "on_screen_text": "A", "claims": []},
        {"scene_no": 2, "narration": "n2", "on_screen_text": "B", "claims": []},
    ]}


def fake_factcheck_pass(script, brief):
    return {"verdict": "pass", "summary": {"verified": 2, "flagged": 0, "unverifiable": 0},
            "claims": []}


def fake_factcheck_block(script, brief):
    return {"verdict": "block",
            "summary": {"verified": 0, "flagged": 1, "unverifiable": 0},
            "claims": [{"claim_id": "c1", "status": "flagged"}]}


def fake_vo(script, pdir, *, pack=None, voice=None, total=42.0):
    manifest = {"schema_version": "studio-vo-1", "voice": voice or "x",
                "grid": {"NS": [0.0, 5.0], "total": total},
                "scenes": script.get("scenes", []), "total_duration_sec": total}
    (Path(pdir) / "vo.grid.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def fake_compose(slug, pack_id):
    # a determinism-CLEAN composition: no Math.random/Date.now/fetch, timeline registered
    pdir = pipeline.project_dir(slug)
    html = ('<html><body><section class="scene clip" id="s1">A</section>'
            '<section class="scene clip" id="s2">B</section>'
            f'<script>window.__timelines["{slug}"] = makeTimeline();</script></body></html>')
    (pdir / "index.html").write_text(html, encoding="utf-8")
    return pdir / "index.html"


def make_fake_render():
    def _render(pdir, *, final=False):
        name = "video.mp4" if final else "renders/draft.mp4"
        out = Path(pdir) / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00mp4")
        return {"ok": True, "video": str(out)}
    return _render


def fake_review_clean(slug, mode):
    return {"synthesis": {"counts": {"Minor": 1}, "fixes": [
        {"id": "R01", "severity": "Minor", "scene": 1}]},
        "apply": {"applied": []}}


def fake_review_with_blocker(slug, mode):
    # a Blocker the auto-apply did NOT resolve → review_ok must be False
    return {"synthesis": {"counts": {"Blocker": 1}, "fixes": [
        {"id": "R01", "severity": "Blocker", "scene": 1}]},
        "apply": {"applied": []}}


def fake_motion_pass(slug):
    return {"any_flag": False, "scenes": []}


def fake_motion_flag(slug):
    return {"any_flag": True, "scenes": []}


def fake_gate_pass(slug):
    return {"verdict": "PASS", "reasons": [], "overall": 4.5,
            "dimensions": [], "compliance": []}


def _seams(**over):
    base = dict(research_fn=fake_research, script_fn=fake_script,
                factcheck_fn=fake_factcheck_pass, vo_fn=fake_vo, compose_fn=fake_compose,
                render_fn=make_fake_render(), review_fn=fake_review_clean,
                motion_fn=fake_motion_pass, gate_fn=fake_gate_pass)
    base.update(over)
    return base


def _run_config(budget=600.0):
    return {"channel": "main", "pack_id": "dark-truth-social", "voice": "am_onyx",
            "aspect": "16:9", "publish_target": "youtube", "render_budget_sec": budget}


# ----------------------------------------------------------------------
# 1. full flow → complete, with video.mp4, BOTH gates passed
# ----------------------------------------------------------------------
def test_full_flow_runs_to_complete_unattended(tmp_path, monkeypatch):
    from studio import config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")
    state = pipeline.produce(_brief(), "e2e", unattended=True,
                             run_config=_run_config(), **_seams())
    assert state["status"] == "complete"
    # every stage done
    for s in pipeline.STAGES:
        assert state["stages"][s]["status"] == "done", f"stage {s} not done"
    # BOTH gates fired and passed (factcheck earned, final auto-approved)
    assert state["gates"]["factcheck"]["status"] == "passed"
    assert state["gates"]["final"]["status"] == "passed"
    assert state["gates"]["final"]["approved_by"] == "unattended-auto"
    # the deliverable exists on disk
    video = Path(state["artifacts"]["video"])
    assert video.name == "video.mp4" and video.is_file()


# ----------------------------------------------------------------------
# 2. the FINAL gate fires — pauses without approval, resumes WITH it
# ----------------------------------------------------------------------
def test_final_gate_pauses_then_resumes_on_approval(tmp_path, monkeypatch):
    from studio import config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")
    seams = _seams()

    # attended run (not unattended): must PAUSE at the final gate
    state = pipeline.produce(_brief(), "e2e2", unattended=False,
                             run_config=_run_config(), **seams)
    assert state["status"] == "awaiting_final_gate"
    assert state["gates"]["final"]["status"] == "awaiting_approval"
    assert "video" not in state["artifacts"]
    assert not (tmp_path / "projects" / "e2e2" / "video.mp4").exists()

    # resume with --approve final: ships
    state = pipeline.produce(_brief(), "e2e2", approve={"final"},
                             run_config=_run_config(), **seams)
    assert state["status"] == "complete"
    assert state["gates"]["final"]["approved_by"] == "human"
    assert (tmp_path / "projects" / "e2e2" / "video.mp4").is_file()


# ----------------------------------------------------------------------
# 3. a `block` fact-check can NEVER be approved away
# ----------------------------------------------------------------------
def test_block_factcheck_cannot_be_approved(tmp_path, monkeypatch):
    from studio import config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")
    seams = _seams(factcheck_fn=fake_factcheck_block)

    state = pipeline.produce(_brief(), "e2e3", unattended=True,
                             run_config=_run_config(), **seams)
    assert state["status"] == "blocked_at_factcheck"
    assert state["gates"]["factcheck"]["approvable"] is False
    # never advanced past the gate
    assert state["stages"]["vo"]["status"] == "pending"
    assert state["stages"]["final"]["status"] == "pending"

    # try to approve it away — it RE-RUNS and (still blocking) blocks again
    state = pipeline.produce(_brief(), "e2e3", approve={"factcheck"}, unattended=True,
                             run_config=_run_config(), **seams)
    assert state["status"] == "blocked_at_factcheck"
    assert state["stages"]["vo"]["status"] == "pending"
    assert not (tmp_path / "projects" / "e2e3" / "video.mp4").exists()

    # once the (fixed) script PASSES the check, resume advances and completes
    seams_fixed = _seams()  # factcheck now passes
    state = pipeline.produce(_brief(), "e2e3", approve={"factcheck"}, unattended=True,
                             run_config=_run_config(), **seams_fixed)
    assert state["status"] == "complete"


# ----------------------------------------------------------------------
# 4. --unattended HOLDS the final gate on a failed quality gate / over budget
# ----------------------------------------------------------------------
def test_unattended_holds_when_review_has_unresolved_blocker(tmp_path, monkeypatch):
    from studio import config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")
    seams = _seams(review_fn=fake_review_with_blocker)
    state = pipeline.produce(_brief(), "e2e4", unattended=True,
                             run_config=_run_config(), **seams)
    assert state["status"] == "awaiting_final_gate"
    assert state["gates"]["final"]["details"]["review_ok"] is False


def test_unattended_holds_when_motion_flags(tmp_path, monkeypatch):
    from studio import config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")
    seams = _seams(motion_fn=fake_motion_flag)
    state = pipeline.produce(_brief(), "e2e5", unattended=True,
                             run_config=_run_config(), **seams)
    assert state["status"] == "awaiting_final_gate"
    assert state["gates"]["final"]["details"]["motion_ok"] is False


def test_unattended_holds_when_over_budget(tmp_path, monkeypatch):
    from studio import config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")
    # vo writes a 42s runtime; a 10s budget is exceeded → hold
    seams = _seams()
    state = pipeline.produce(_brief(), "e2e6", unattended=True,
                             run_config=_run_config(budget=10.0), **seams)
    assert state["status"] == "awaiting_final_gate"
    assert state["gates"]["final"]["details"]["under_budget"] is False


# ----------------------------------------------------------------------
# 5. determinism enforced on the composed index.html
# ----------------------------------------------------------------------
def test_determinism_violation_blocks_render(tmp_path, monkeypatch):
    from studio import config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")

    def bad_compose(slug, pack_id):
        pdir = pipeline.project_dir(slug)
        (pdir / "index.html").write_text(
            '<section class="scene clip">x</section>'
            '<script>const r = Math.random(); window.__timelines["x"]=t;</script>',
            encoding="utf-8")
        return pdir / "index.html"

    seams = _seams(compose_fn=bad_compose)
    try:
        pipeline.produce(_brief(), "e2e7", unattended=True,
                         run_config=_run_config(), **seams)
        assert False, "expected a determinism PipelineError"
    except pipeline.PipelineError as exc:
        assert "determinism" in str(exc).lower()
    # the draft render never ran
    assert not (tmp_path / "projects" / "e2e7" / "renders" / "draft.mp4").exists()


# ----------------------------------------------------------------------
# 6. channel/pack resolution drives the run config (config change, not code)
# ----------------------------------------------------------------------
def test_channel_resolution_selects_pack_and_voice():
    from studio import config as cfg
    reg = {"main": {"default_pack": "dark-truth-social", "voice": "am_onyx",
                    "aspect": "16:9", "publish_target": "youtube", "render_budget_sec": 600.0}}
    rc = cfg.resolve_run_config(channel="main", registry=reg)
    assert rc["pack_id"] == "dark-truth-social" and rc["voice"] == "am_onyx"
    # --pack overrides the channel default
    rc2 = cfg.resolve_run_config(channel="main", pack="clean-explainer", registry=reg)
    assert rc2["pack_id"] == "clean-explainer"
    # unknown channel falls back to defaults, never raises
    rc3 = cfg.resolve_run_config(channel="ghost", registry=reg)
    assert rc3["pack_id"] == cfg.DEFAULT_PACK
