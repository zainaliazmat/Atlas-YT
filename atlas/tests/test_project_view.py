"""Read-only views over a project dir for the gate UI (project_view.py).

Pure functions over on-disk artifacts — no LLM, no network, no pipeline run. These
build the inline gate previews (fact-check report + script; render plan + draft
renders + palette) and detect which project is paused at a gate.
"""
import json

import project_view


# ----------------------------------------------------------------------
# Fixtures — minimal but real-shaped project dirs.
# ----------------------------------------------------------------------
def _write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))


def _make_project(root, slug, *, status, updated, topic="A topic"):
    pdir = root / slug
    pdir.mkdir(parents=True, exist_ok=True)
    gate = status[len("blocked_at_"):] if status.startswith("blocked_at_") else None
    gates = {"factcheck": {"status": "pending", "details": None},
             "final_render": {"status": "pending", "details": None}}
    if gate:
        gates[gate] = {"status": "blocked", "details": {"marker": gate}}
    _write(pdir / "project.json", {
        "slug": slug, "status": status, "updated": updated, "topic": topic,
        "gates": gates, "artifacts": {}})
    return pdir


def _add_factcheck(pdir):
    _write(pdir / "factcheck_report.json", {
        "verdict": "block",
        "summary": {"verified": 3, "flagged": 2, "unverifiable": 0},
        "claims": [
            {"claim_id": "s1c1", "scene_no": 1, "claim_text": "Verified thing",
             "status": "verified", "sources": ["http://x"]},
            {"claim_id": "s2c1", "scene_no": 2, "claim_text": "Shaky thing",
             "status": "flagged", "sources": [], "note": "no source"},
            {"claim_id": "s3c1", "scene_no": 3, "claim_text": "Unknowable thing",
             "status": "unverifiable", "sources": []},
        ]})
    _write(pdir / "script.json", {
        "working_title": "The Title", "total_scenes": 3, "est_runtime_sec": 60,
        "scenes": [{"scene_no": 1, "point": "p1"}, {"scene_no": 2, "point": "p2"}]})


def _add_render_artifacts(pdir):
    _write(pdir / "style_guide.json",
           {"palette": {"bg": "#F4F1EA", "signature_highlight": "#FFD000",
                        "accents": ["#1E47C8"]}})
    _write(pdir / "script.json",
           {"working_title": "Render Me", "total_scenes": 2, "est_runtime_sec": 40})
    _write(pdir / "audio" / "audio_manifest.json", {"total_duration_sec": 42.5})
    for n in (2, 1):  # created out of order on purpose -> must come back sorted
        d = pdir / "scenes" / f"scene-0{n}" / "renders"
        d.mkdir(parents=True, exist_ok=True)
        (d / "draft.mp4").write_bytes(b"\x00")


# ----------------------------------------------------------------------
# find_latest_blocked
# ----------------------------------------------------------------------
def test_find_latest_blocked_picks_most_recent_and_ignores_done(tmp_path):
    _make_project(tmp_path, "done-one", status="done", updated=100)
    _make_project(tmp_path, "old-block", status="blocked_at_factcheck", updated=200)
    _make_project(tmp_path, "new-block", status="blocked_at_final_render", updated=300)

    hit = project_view.find_latest_blocked(tmp_path)
    assert hit["slug"] == "new-block"
    assert hit["gate"] == "final_render"
    assert hit["details"] == {"marker": "final_render"}
    assert hit["project_dir"].endswith("new-block")
    # The web UI's gate-card dedup keys on (slug, gate, updated). `updated` is the
    # project.json timestamp, which pipeline._save() bumps on every gate write — so a
    # revise→resume that re-blocks at the SAME gate yields a NEW `updated` and the card
    # re-shows. Confirm the field is surfaced and is that timestamp.
    assert hit["updated"] == 300


def test_find_latest_blocked_updated_changes_on_reblock(tmp_path):
    # Same slug + gate, but re-saved with a newer timestamp (a revise re-block) must
    # produce a different dedup key so the operator isn't left without a card.
    _make_project(tmp_path, "p", status="blocked_at_factcheck", updated=10)
    first = project_view.find_latest_blocked(tmp_path)
    _make_project(tmp_path, "p", status="blocked_at_factcheck", updated=20)  # re-block
    second = project_view.find_latest_blocked(tmp_path)
    assert first["updated"] != second["updated"]        # dedup key changes -> re-shows


def test_find_latest_blocked_returns_none_when_nothing_blocked(tmp_path):
    _make_project(tmp_path, "done-one", status="done", updated=100)
    assert project_view.find_latest_blocked(tmp_path) is None


def test_find_latest_blocked_handles_missing_dir(tmp_path):
    assert project_view.find_latest_blocked(tmp_path / "nope") is None


# ----------------------------------------------------------------------
# gate1_preview (fact-check) — report + script, only flagged/unverifiable surfaced
# ----------------------------------------------------------------------
def test_gate1_preview_surfaces_verdict_summary_flagged_and_script(tmp_path):
    pdir = _make_project(tmp_path, "p", status="blocked_at_factcheck", updated=1)
    _add_factcheck(pdir)

    pv = project_view.gate1_preview(pdir)
    assert pv["gate"] == "factcheck"
    assert pv["verdict"] == "block"
    assert pv["summary"] == {"verified": 3, "flagged": 2, "unverifiable": 0}
    ids = {c["claim_id"] for c in pv["flagged"]}
    assert ids == {"s2c1", "s3c1"}                      # verified claim NOT surfaced
    assert pv["script"]["working_title"] == "The Title"
    assert pv["script"]["total_scenes"] == 3


# ----------------------------------------------------------------------
# gate2_preview (final render) — plan + sorted draft renders + palette
# ----------------------------------------------------------------------
def test_gate2_preview_has_plan_sorted_drafts_and_palette(tmp_path):
    pdir = _make_project(tmp_path, "p", status="blocked_at_final_render", updated=1)
    _add_render_artifacts(pdir)

    pv = project_view.gate2_preview(pdir)
    assert pv["gate"] == "final_render"
    assert pv["plan"]["working_title"] == "Render Me"
    assert pv["plan"]["audio_duration_sec"] == 42.5
    drafts = [str(p) for p in pv["draft_renders"]]
    assert len(drafts) == 2
    assert drafts[0].endswith("scene-01/renders/draft.mp4")   # sorted
    assert drafts[1].endswith("scene-02/renders/draft.mp4")
    assert pv["palette"]["signature_highlight"] == "#FFD000"


def test_draft_renders_only_returns_existing_draft_mp4s_sorted(tmp_path):
    pdir = _make_project(tmp_path, "p", status="blocked_at_final_render", updated=1)
    _add_render_artifacts(pdir)
    paths = project_view.draft_renders(pdir)
    assert [p.name for p in paths] == ["draft.mp4", "draft.mp4"]
    assert all(p.exists() for p in paths)
