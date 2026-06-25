"""Tests for the Slice-3 read surfaces: the Stage/Agent Inspector (depth 2), the Live
Activity Feed snapshot, the fleet current-video enrichment, and the T1 retry path.

All read-only except retry, which goes through the injected fake dispatcher produce_fn —
never a real engine/LLM (ANTHROPIC_API_KEY is never set here).
"""
from __future__ import annotations

import pathlib
import time

import chat_state
from fastapi.testclient import TestClient

from dashboard import data
from dashboard.app import create_app
from dashboard.tests import fixtures


# ============================================================ stage_detail (data)
def test_stage_detail_done_stage_has_agent_inputs_output(tmp_path):
    pdir, slugs = fixtures.build_projects(tmp_path)
    det = data.stage_detail(pdir, slugs["done"], "script")
    assert det is not None
    assert det["key"] == "script"
    # the owning agent identity is surfaced (script -> Marlow the scriptwriter)
    assert det["agent"]["name"] == "scriptwriter"
    assert det["agent"]["emoji"]
    assert det["provider"]["provider"]               # effective brain present
    assert det["status"] == "done"
    # script reads the research brief upstream
    names = [i["name"] for i in det["inputs"]]
    assert "research_brief.json" in names
    # output artifact + a real contract-validation verdict
    assert det["output"]["artifact"] == "script.json"
    assert det["output"]["valid"] in (True, False)
    assert det["failure"] is None
    # a done stage can't be retried; the honest action set is explicit
    assert det["actions"]["can_retry"] is False


def test_stage_detail_unknown_stage_or_project_is_none(tmp_path):
    pdir, slugs = fixtures.build_projects(tmp_path)
    assert data.stage_detail(pdir, slugs["done"], "no-such-stage") is None
    assert data.stage_detail(pdir, "no-such-project", "script") is None


def test_stage_detail_classifies_deterministic_failure(tmp_path):
    """A contract-validation failure is DETERMINISTIC → no retry offered (spec §6.4)."""
    pdir, _ = fixtures.build_projects(tmp_path)
    p = pdir / "det-fail"
    p.mkdir()
    chat_state.atomic_write_json(p / "project.json", {
        "schema_version": "1.0", "slug": "det-fail", "title": "Deterministic fail",
        "status": "failed", "updated": time.time(),
        "stages": {"script": {"status": "failed", "validated": False,
                              "note": "script.json failed validation: 'hook' is required",
                              "artifact": "script.json"}},
        "gates": {}, "history": [],
    })
    det = data.stage_detail(pdir, "det-fail", "script")
    assert det["failure"]["kind"] == "deterministic"
    assert det["actions"]["can_retry"] is False      # deterministic must NOT retry
    assert det["actions"]["can_cancel"] is True
    assert "required" in det["failure"]["reason"]


def test_stage_detail_classifies_transient_failure(tmp_path):
    """A producer that raised is TRANSIENT → retry IS offered (spec §6.4)."""
    pdir, _ = fixtures.build_projects(tmp_path)
    p = pdir / "tr-fail"
    p.mkdir()
    chat_state.atomic_write_json(p / "project.json", {
        "schema_version": "1.0", "slug": "tr-fail", "title": "Transient fail",
        "status": "failed", "updated": time.time(),
        "stages": {"narration": {"status": "failed", "validated": False,
                                 "note": "ConnectionError: TTS service timed out"}},
        "gates": {}, "history": [],
    })
    det = data.stage_detail(pdir, "tr-fail", "narration")
    assert det["failure"]["kind"] == "transient"
    assert det["actions"]["can_retry"] is True
    assert det["actions"]["can_cancel"] is True


# ============================================================ stage endpoint
def _client(pdir) -> TestClient:
    app = create_app(projects_dir=pdir)
    c = TestClient(app)
    c._app = app
    return c


def test_stage_endpoint_ok_and_404s(tmp_path):
    pdir, slugs = fixtures.build_projects(tmp_path)
    c = _client(pdir)
    r = c.get(f"/api/projects/{slugs['done']}/stage/script")
    assert r.status_code == 200
    assert r.json()["key"] == "script"
    assert c.get(f"/api/projects/{slugs['done']}/stage/bogus").status_code == 404
    assert c.get("/api/projects/nope/stage/script").status_code == 404
    # path-traversal slug is refused, not served
    assert c.get("/api/projects/..%2f..%2fetc/stage/script").status_code in (400, 404)


# ============================================================ fleet current video+stage
def test_fleet_running_agent_names_its_video_and_stage(tmp_path):
    """The corrupt fixture is `running` with the `script` stage running → Marlow shows the
    project + stage he is on (not just 'running a stage now')."""
    pdir, slugs = fixtures.build_projects(tmp_path)
    fl = data.fleet(pdir)
    marlow = next(a for a in fl["agents"] if a["name"] == "scriptwriter")
    assert marlow["status"] == "running"
    assert marlow.get("current")                    # {slug, label, stage}
    assert marlow["current"]["stage"] == "script"
    assert marlow["current"]["slug"] == slugs["corrupt"]


# ============================================================ activity snapshot
def test_activity_endpoint_returns_events_newest_first(tmp_path):
    pdir = fixtures.build_empty(tmp_path)
    c = _client(pdir)
    # seed the ring directly through the dispatcher the app builds
    from dashboard.app import _get_dispatcher
    disp = _get_dispatcher(c._app)
    disp.events.emit("triggered", slug="a", message="first", initiator="ceo")
    disp.events.emit("done", slug="a", message="second", initiator="dispatcher")
    r = c.get("/api/activity")
    assert r.status_code == 200
    body = r.json()
    assert "events" in body and "last_id" in body
    ev = body["events"]
    assert len(ev) == 2
    assert ev[0]["id"] > ev[1]["id"]                # newest first
    assert ev[0]["initiator"] == "dispatcher"
    # `since` returns only newer events (backfill parity with SSE)
    r2 = c.get("/api/activity", params={"since": ev[1]["id"]})
    assert [e["id"] for e in r2.json()["events"]] == [ev[0]["id"]]


# ============================================================ retry (T1)
def _fast_fail_then_done(root):
    """Fake spine: fails `narration` transiently the FIRST time per slug, succeeds after —
    so an explicit retry drives it to done."""
    seen: set[str] = set()

    def fake(slug=None, approve=None, root=root, progress=None,
             station_locks=None, should_cancel=None):
        pp = pathlib.Path(root) / slug / "project.json"
        proj = chat_state.load_json(pp, {})
        if slug not in seen:
            seen.add(slug)
            proj["status"] = "failed"
            proj.setdefault("stages", {})["narration"] = {
                "status": "failed", "validated": False, "note": "TTS timed out"}
            chat_state.atomic_write_json(pp, proj)
            return {"status": "failed", "stage": "narration",
                    "failure_kind": "transient", "errors": ["TTS timed out"]}
        proj["status"] = "done"
        proj["stages"] = {k: {"status": "done"} for k in proj.get("stages", {})}
        chat_state.atomic_write_json(pp, proj)
        return {"status": "done", "video": "video.mp4"}

    return fake


def test_retry_endpoint_restarts_a_failed_video(tmp_path):
    import supervisor as _sup
    pdir = fixtures.build_empty(tmp_path)
    app = create_app(projects_dir=pdir)
    app.state.produce_fn = _fast_fail_then_done(pdir)
    app.state.decide_fn = _sup.safe_default_decider   # keep tests offline — no real LLM
    app.state.max_retries = 0                        # no auto-retry; the UI drives it
    c = TestClient(app)
    c._app = app
    # disable the dispatcher's own auto-retry so the first run parks as failed
    from dashboard.app import _get_dispatcher
    disp = _get_dispatcher(app)
    disp.max_retries = 0
    slug = c.post("/api/trigger", json={"topic": "retry me"}).json()["slug"]
    # wait until it parks failed
    for _ in range(250):
        v = next((x for x in c.get("/api/belt").json()["videos"]
                  if x["slug"] == slug), None)
        if v and v["belt_state"] == "failed":
            break
        time.sleep(0.02)
    assert v and v["belt_state"] == "failed"
    # the operator hits RETRY → it advances to done
    assert c.post(f"/api/retry/{slug}").status_code == 200
    for _ in range(250):
        v = next((x for x in c.get("/api/belt").json()["videos"]
                  if x["slug"] == slug), None)
        if v and v["belt_state"] == "done":
            break
        time.sleep(0.02)
    assert v and v["belt_state"] == "done"


def test_retry_unknown_slug_404(tmp_path):
    c = _client(fixtures.build_empty(tmp_path))
    assert c.post("/api/retry/no-such-project").status_code == 404
