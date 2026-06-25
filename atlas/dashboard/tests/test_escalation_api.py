"""Slice 4 — the escalation surface data: fix_history on the gate card, atlas_activity on belt."""
import json
from fastapi.testclient import TestClient
from dashboard.app import create_app
from dashboard.tests import fixtures


def _client(tmp_path):
    pdir, slugs = fixtures.build_projects(tmp_path)
    app = create_app(projects_dir=pdir)
    c = TestClient(app); c._app = app
    return c, pdir, slugs


def test_gate_detail_includes_fix_history(tmp_path):
    c, pdir, slugs = _client(tmp_path)
    slug = slugs["hard_block"]
    proj_path = pdir / slug / "project.json"
    proj = json.loads(proj_path.read_text())
    proj.setdefault("supervisor", {})["fix_history"] = {"factcheck": [
        {"n": 1, "ts": 1.0, "flagged_before": [{"claim_id": "s5c2", "claim_text": "42%"}],
         "instructions": "drop s5c2"}]}
    proj_path.write_text(json.dumps(proj))
    body = c.get(f"/api/gate/{slug}").json()
    assert body["kind"] == "factcheck"
    assert body["fix_history"][0]["instructions"] == "drop s5c2"
    assert body["fix_history"][0]["flagged_before"][0]["claim_id"] == "s5c2"


def test_belt_includes_atlas_activity(tmp_path):
    c, pdir, slugs = _client(tmp_path)
    slug = slugs["hard_block"]
    proj_path = pdir / slug / "project.json"
    proj = json.loads(proj_path.read_text())
    proj.setdefault("supervisor", {})["log"] = [
        {"ts": 2.0, "kind": "FIX_AND_RERUN", "reason": "fix 1/2"}]
    proj_path.write_text(json.dumps(proj))
    body = c.get("/api/belt").json()
    vid = next(v for v in body["videos"] if v["slug"] == slug)
    txt = vid["atlas_activity"]["text"]
    # Humanized live line: starts with "Atlas: ", carries the reason, never the raw enum.
    assert txt.startswith("Atlas: ") and "FIX_AND_RERUN" not in txt
    assert "fix 1/2" in txt


def test_guide_endpoint_reruns(tmp_path):
    import supervisor
    c, pdir, slugs = _client(tmp_path)
    c._app.state.produce_fn = None      # guide reruns through the belt; keep it offline:
    c._app.state.decide_fn = supervisor.safe_default_decider
    r = c.post(f"/api/gate/{slugs['hard_block']}/guide",
               json={"instructions": "tighten scene 5 stat"})
    assert r.status_code == 200 and r.json()["result"] == "guided"


def test_guide_rejects_empty_instructions(tmp_path):
    c, pdir, slugs = _client(tmp_path)
    r = c.post(f"/api/gate/{slugs['hard_block']}/guide", json={"instructions": "  "})
    assert r.status_code == 400


def test_kill_endpoint(tmp_path):
    c, pdir, slugs = _client(tmp_path)
    r = c.post(f"/api/gate/{slugs['hard_block']}/kill", json={"reason": "unworkable"})
    assert r.status_code == 200 and r.json()["result"] == "killed"
