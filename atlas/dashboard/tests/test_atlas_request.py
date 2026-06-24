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
