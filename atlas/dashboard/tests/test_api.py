"""Contract tests for every dashboard endpoint: status + shape + graceful degradation.

These never touch the real projects dir (the `client` fixture points at a disposable
copy) and never run a real pipeline/LLM (no approve here; that's test_security.py).
"""
from __future__ import annotations


# ---------------------------------------------------------------- overview
def test_overview_shape(client):
    r = client.get("/api/overview")
    assert r.status_code == 200
    body = r.json()
    for k in ("kpis", "active", "active_pipeline", "fleet", "gate", "quality",
              "activity", "counts"):
        assert k in body, f"missing overview key {k}"
    kpis = body["kpis"]
    for k in ("in_production", "awaiting_you", "fleet_total", "fleet_idle",
              "latest_quality"):
        assert k in kpis, f"missing kpi {k}"
    assert kpis["fleet_total"] == 10
    assert isinstance(body["fleet"], list) and len(body["fleet"]) == 10
    assert isinstance(body["activity"], list)


def test_overview_empty_system(empty_client):
    r = empty_client.get("/api/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["kpis"]["fleet_total"] == 10  # fleet is registry-derived, always 10
    assert body["counts"]["total"] == 0
    assert body["active"] is None
    assert body["gate"] is None
    assert body["activity"] == []


# ---------------------------------------------------------------- projects
def test_projects_shape_and_counts(client, slugs):
    r = client.get("/api/projects")
    assert r.status_code == 200
    body = r.json()
    assert "projects" in body and "counts" in body
    rows = body["projects"]
    counts = body["counts"]
    found = {p["slug"] for p in rows}
    for s in slugs.values():
        assert s in found, f"project {s} missing from list"
    for p in rows:
        for k in ("slug", "title", "status", "gate", "scenes", "runtime_sec",
                  "quality", "updated"):
            assert k in p, f"project row missing {k}"
    # counts add up: total == number of rows
    assert counts["total"] == len(rows)
    for k in ("needs_you", "in_production", "blocked", "done", "queued", "failed"):
        assert k in counts
    assert counts["done"] >= 1            # the `done` fixture
    assert counts["queued"] >= 1          # the `queued` fixture (status created)
    # hard_block counts as 'blocked' not 'needs_you'; blocked_clean/blocked_final = needs_you
    assert counts["blocked"] >= 1
    assert counts["needs_you"] >= 1
    # avg_quality present (None or float)
    assert "avg_quality" in counts


def test_projects_empty(empty_client):
    r = empty_client.get("/api/projects")
    assert r.status_code == 200
    body = r.json()
    assert body["projects"] == []
    assert body["counts"]["total"] == 0
    assert body["counts"]["avg_quality"] is None


# ---------------------------------------------------------------- project detail
def test_project_detail_done(client, slugs):
    r = client.get(f"/api/projects/{slugs['done']}")
    assert r.status_code == 200
    body = r.json()
    for k in ("summary", "stages", "gates", "contracts", "artifacts", "quality",
              "history", "has_video"):
        assert k in body, f"detail missing {k}"
    assert isinstance(body["stages"], list) and body["stages"]
    for st in body["stages"]:
        for k in ("key", "label", "agent", "status", "validated", "artifact",
                  "detail"):
            assert k in st
    assert body["has_video"] is True
    assert "factcheck" in body["gates"] and "final_render" in body["gates"]


def test_project_detail_corrupt_no_500(client, slugs):
    # corrupt script.json must NOT 500; detail still renders, contracts surface it.
    r = client.get(f"/api/projects/{slugs['corrupt']}")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["slug"] == slugs["corrupt"]
    # script stage points at a corrupt artifact -> contracts marks it missing/invalid
    contract_status = {c["contract"]: c["status"] for c in body["contracts"]}
    if "script" in contract_status:
        assert contract_status["script"] in ("missing", "invalid")


def test_project_detail_unknown_404(client):
    r = client.get("/api/projects/does-not-exist")
    assert r.status_code == 404


# ---------------------------------------------------------------- fleet
def test_fleet_shape(client):
    r = client.get("/api/fleet")
    assert r.status_code == 200
    body = r.json()
    assert "agents" in body and "summary" in body
    agents = body["agents"]
    assert len(agents) == 10
    for a in agents:
        for k in ("name", "display", "emoji", "role", "blurb", "provider", "model",
                  "jobs_run", "status", "detail"):
            assert k in a, f"agent row missing {k}"
    s = body["summary"]
    for k in ("total", "working", "idle", "holding", "non_claude"):
        assert k in s
    assert s["total"] == 10
    # status buckets are mutually consistent with total
    assert s["working"] + s["idle"] + s["holding"] <= s["total"]


def test_every_agent_resolvable(client):
    fleet = client.get("/api/fleet").json()["agents"]
    for a in fleet:
        r = client.get(f"/api/agents/{a['name']}")
        assert r.status_code == 200, f"agent {a['name']} not resolvable"
        det = r.json()
        for k in ("display", "provider", "model", "jobs", "soul", "recent_jobs",
                  "jobs_run", "owned_bands"):
            assert k in det, f"agent detail missing {k} for {a['name']}"
        soul = det["soul"]
        for k in ("files", "identity", "voice"):
            assert k in soul


def test_agent_unknown_404(client):
    r = client.get("/api/agents/nonexistent_agent")
    assert r.status_code == 404


# ---------------------------------------------------------------- quality
def test_quality_degrades_gracefully(client):
    r = client.get("/api/quality")
    assert r.status_code == 200
    body = r.json()
    for k in ("available", "rubric", "rubric_version", "latest", "trend",
              "scored_count", "loop_ledger"):
        assert k in body, f"quality missing {k}"
    # available may be False if no scorecard, but rubric_version must be surfaced
    assert "rubric_version" in body
    assert isinstance(body["trend"], list)


def test_quality_empty_system(empty_client):
    r = empty_client.get("/api/quality")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["scored_count"] == 0
    assert body["trend"] == []


# ---------------------------------------------------------------- gate
def test_gate_factcheck_clean(client, slugs):
    r = client.get(f"/api/gate/{slugs['blocked_clean']}")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "factcheck"
    assert body["blocked"] is True
    assert body["approvable"] is True
    assert body["hard_block"] is False
    assert "verdict" in body and "summary" in body and "flagged" in body
    assert "verified_claims" in body


def test_gate_factcheck_hard_block(client, slugs):
    r = client.get(f"/api/gate/{slugs['hard_block']}")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "factcheck"
    assert body["approvable"] is False
    assert body["hard_block"] is True
    assert len(body["flagged"]) >= 1


def test_gate_final_render(client, slugs):
    r = client.get(f"/api/gate/{slugs['blocked_final']}")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "final_render"
    assert body["approvable"] is True
    assert "plan" in body and "palette" in body and "draft_renders" in body
    assert "has_video" in body


def test_gate_not_blocked_done(client, slugs):
    r = client.get(f"/api/gate/{slugs['done']}")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "none"
    assert body["blocked"] is False
    assert body["approvable"] is False


def test_gate_unknown_404(client):
    r = client.get("/api/gate/nope")
    assert r.status_code == 404


# ---------------------------------------------------------------- artifact
def test_artifact_whitelisted_valid(client, slugs):
    r = client.get(f"/api/artifact/{slugs['done']}/project.json")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "project.json"
    assert body["valid"] is True
    assert body["data"] is not None


def test_artifact_corrupt_returns_valid_false_not_500(client, slugs):
    r = client.get(f"/api/artifact/{slugs['corrupt']}/script.json")
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert body["data"] is None


def test_artifact_non_whitelisted_400(client, slugs):
    r = client.get(f"/api/artifact/{slugs['done']}/secrets.txt")
    assert r.status_code == 400


# ---------------------------------------------------------------- media / range
def test_video_full_get_200_accept_ranges(client, slugs):
    r = client.get(f"/api/media/{slugs['done']}/video")
    assert r.status_code == 200
    assert r.headers.get("accept-ranges") == "bytes"
    assert int(r.headers["content-length"]) > 0


def test_video_range_206_partial(client, slugs):
    # first ask for full size, then request a small window
    full = client.get(f"/api/media/{slugs['done']}/video")
    size = int(full.headers["content-length"])
    r = client.get(f"/api/media/{slugs['done']}/video",
                   headers={"Range": "bytes=0-1023"})
    assert r.status_code == 206
    assert r.headers.get("accept-ranges") == "bytes"
    cr = r.headers.get("content-range")
    assert cr == f"bytes 0-1023/{size}", cr
    # partial length is the window, NOT the whole file
    assert int(r.headers["content-length"]) == 1024
    assert int(r.headers["content-length"]) < size


def test_video_missing_404(client, slugs):
    # queued project has no video.mp4
    r = client.get(f"/api/media/{slugs['queued']}/video")
    assert r.status_code == 404
