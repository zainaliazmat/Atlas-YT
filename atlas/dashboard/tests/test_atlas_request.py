"""The unified Atlas request router — one typed entry point over the belt's hands."""
import pytest
from dashboard import atlas_request
from dashboard.atlas_request import handle_request, UnknownIntent


class FakeDispatcher:
    def __init__(self):
        self.calls = []
    def trigger(self, **kw):
        self.calls.append(("trigger", kw)); return {"slug": "s1"}
    def rerun(self, slug, from_stage=None, *, initiator="ceo"):
        self.calls.append(("rerun", slug, from_stage)); return {"slug": slug, "rerunning": True}
    def retry(self, slug, *, initiator="ceo"):
        self.calls.append(("retry", slug)); return {"slug": slug, "retrying": True}
    def cancel(self, slug, *, initiator="ceo"):
        self.calls.append(("cancel", slug)); return {"slug": slug, "cancelling": True}
    def resume(self, slug, gate, *, initiator="ceo", wait=False, timeout=900.0):
        self.calls.append(("resume", slug, gate)); return {"slug": slug, "status": "done"}
    def guide(self, slug, instructions, *, initiator="ceo"):
        self.calls.append(("guide", slug, instructions)); return {"slug": slug, "guided": True}
    def kill(self, slug, reason="", *, initiator="ceo"):
        self.calls.append(("kill", slug, reason)); return {"slug": slug, "killed": True}


def test_make_video_routes_to_trigger():
    d = FakeDispatcher()
    out = handle_request(d, None, "make_video", {"topic": "AI", "length": "short"})
    assert out["intent"] == "make_video" and out["result"]["slug"] == "s1"
    assert d.calls[0][0] == "trigger"


def test_rerun_and_retry_and_cancel():
    d = FakeDispatcher()
    assert handle_request(d, None, "rerun", {"slug": "x", "from_stage": "script"})["result"]["rerunning"]
    assert handle_request(d, None, "retry", {"slug": "x"})["result"]["retrying"]
    assert handle_request(d, None, "cancel", {"slug": "x"})["result"]["cancelling"]
    assert ("rerun", "x", "script") in d.calls
    assert ("retry", "x") in d.calls and ("cancel", "x") in d.calls


def test_answer_escalation_approve_guide_kill():
    d = FakeDispatcher()
    assert handle_request(d, None, "answer_escalation",
                          {"action": "approve", "slug": "x", "gate": "final_render"})["result"]["status"] == "done"
    assert handle_request(d, None, "answer_escalation",
                          {"action": "guide", "slug": "x", "instructions": "fix it"})["result"]["guided"]
    assert handle_request(d, None, "answer_escalation",
                          {"action": "kill", "slug": "x", "reason": "no"})["result"]["killed"]
    assert ("resume", "x", "final_render") in d.calls
    assert ("guide", "x", "fix it") in d.calls and ("kill", "x", "no") in d.calls


def test_unknown_intent_raises():
    with pytest.raises(UnknownIntent):
        handle_request(FakeDispatcher(), None, "launch_nukes", {})


def test_unknown_escalation_action_raises():
    with pytest.raises(UnknownIntent):
        handle_request(FakeDispatcher(), None, "answer_escalation", {"action": "bogus", "slug": "x"})


from fastapi.testclient import TestClient
from dashboard.app import create_app
from dashboard.tests import fixtures
import supervisor


def _client(tmp_path):
    pdir, slugs = fixtures.build_projects(tmp_path)
    app = create_app(projects_dir=pdir)
    app.state.decide_fn = supervisor.safe_default_decider   # offline
    # a fast fake belt so make_video/rerun don't run a real engine
    def fake(slug=None, approve=None, root=None, progress=None, station_locks=None,
             should_cancel=None):
        return {"status": "done"}
    app.state.produce_fn = fake
    c = TestClient(app); c._app = app
    return c, pdir, slugs


def test_atlas_request_make_video(tmp_path):
    c, pdir, slugs = _client(tmp_path)
    r = c.post("/api/atlas/request", json={"intent": "make_video",
                                           "args": {"topic": "AI tools", "length": "short"}})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["result"]["slug"]


def test_atlas_request_unknown_intent_400(tmp_path):
    c, pdir, slugs = _client(tmp_path)
    r = c.post("/api/atlas/request", json={"intent": "nope", "args": {}})
    assert r.status_code == 400 and r.json()["ok"] is False


def test_atlas_request_cancel(tmp_path):
    c, pdir, slugs = _client(tmp_path)
    r = c.post("/api/atlas/request",
               json={"intent": "cancel", "args": {"slug": slugs["queued"]}})
    assert r.status_code == 200 and r.json()["result"]["cancelling"] is True
