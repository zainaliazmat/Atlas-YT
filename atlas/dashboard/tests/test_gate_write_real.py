"""Real-spine gate write path — NOT a fake produce_fn.

These tests drive the genuine `pipeline.produce` (via the sanctioned
`session.AtlasSession.approve_gate` seam the dashboard uses), pointed at a
DISPOSABLE projects dir through pipeline.produce's own `root=` parameter. They
prove the dashboard's one mutation really transitions a project's status on disk
— without touching the real projects, the real chat_state.json, or running a
heavy producer.

The final-render fixture is a full copy of the gold project (every stage already
`done`), flipped to `blocked_at_final_render`. Approving the final-render gate
makes the real spine flip the project to `done` by skipping all producers (the
render gate clears and the already-done render stage is skipped). A fact-check
`block` is proven un-approvable through the SAME real path.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil

import pytest
from fastapi.testclient import TestClient

import dashboard.app as dash_app

ATLAS = pathlib.Path(__file__).resolve().parents[2]
PROJECTS = ATLAS / "projects"
GOLD = PROJECTS / "gpt-4o-vs-claude-vs-gemini-vs-deepseek-comparison--20260621-013345-67a3"
# a real fact-check BLOCK (verdict=block, rejected) — the un-approvable case
HARD = PROJECTS / "gpt-4o-vs-claude-vs-gemini-vs-deepseek-head-to-hea-20260621-034108-f28f"

pytestmark = pytest.mark.skipif(
    not GOLD.exists(), reason="gold reference project not present")


def _md5(p: pathlib.Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.exists() else "absent"


def _client_with_real_spine(tmp_path):
    # produce_fn stays None → the belt dispatcher resumes the REAL pipeline.produce, pinned
    # to the disposable dir it passes as root=tmp_path (it owns the root kwarg; binding it
    # here too would double-pass it). The T2 approve now resumes through the belt (§4).
    app = dash_app.create_app(projects_dir=tmp_path)
    return TestClient(app)


def _make_final_render_blocked(tmp_path) -> str:
    slug = "disp-final-render"
    dst = tmp_path / slug
    shutil.copytree(GOLD, dst)
    pj = json.loads((dst / "project.json").read_text())
    pj["slug"] = slug
    pj["status"] = "blocked_at_final_render"
    pj["gates"]["final_render"]["status"] = "blocked"
    (dst / "project.json").write_text(json.dumps(pj, indent=2))
    return slug


def test_final_render_approve_transitions_to_done(tmp_path):
    slug = _make_final_render_blocked(tmp_path)
    client = _client_with_real_spine(tmp_path)
    pj = tmp_path / slug / "project.json"
    assert json.loads(pj.read_text())["status"] == "blocked_at_final_render"

    chat_state = ATLAS / "chat_state.json"
    before = _md5(chat_state)

    r = client.post(f"/api/gate/{slug}/approve", json={"gate": "final_render"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "approved"
    assert body["status"] == "done", body

    after = json.loads(pj.read_text())
    assert after["status"] == "done"
    assert after["gates"]["final_render"]["status"] == "approved"
    assert all(s.get("status") == "done" for s in after["stages"].values())

    # the dashboard's write path never touched the real chat_state.json
    assert _md5(chat_state) == before


def test_reapprove_done_project_is_idempotent(tmp_path):
    slug = _make_final_render_blocked(tmp_path)
    client = _client_with_real_spine(tmp_path)
    assert client.post(f"/api/gate/{slug}/approve",
                       json={"gate": "final_render"}).status_code == 200
    # second approve: project is no longer at a gate -> 409, never a silent re-run
    r2 = client.post(f"/api/gate/{slug}/approve", json={"gate": "final_render"})
    assert r2.status_code == 409
    assert r2.json().get("status") == "done"


@pytest.mark.skipif(not HARD.exists(), reason="hard-block reference not present")
def test_hard_block_unapprovable_through_real_path(tmp_path):
    slug = "disp-hard-block"
    dst = tmp_path / slug
    shutil.copytree(HARD, dst)
    pj = json.loads((dst / "project.json").read_text())
    pj["slug"] = slug
    (dst / "project.json").write_text(json.dumps(pj, indent=2))
    assert pj.get("status") == "blocked_at_factcheck"

    client = _client_with_real_spine(tmp_path)
    r = client.post(f"/api/gate/{slug}/approve", json={"gate": "factcheck"})
    assert r.status_code == 409
    assert r.json()["result"] == "routed_back"
    # un-approvable: status on disk is unchanged, the spine was never asked to run
    assert json.loads((dst / "project.json").read_text())[
        "status"] == "blocked_at_factcheck"
