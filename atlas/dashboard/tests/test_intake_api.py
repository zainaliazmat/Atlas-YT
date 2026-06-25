"""Tests for Slice 1.5 — niche intake: niche → Scout find_topics → candidate cards.

Scout's find_topics is an LLM + YouTube-API job; it is ALWAYS injected as a fake here
(app.state.find_topics_fn) so the suite is offline/deterministic — the real Scout engine
and ANTHROPIC_API_KEY/YOUTUBE_API_KEY are never touched.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from dashboard import intake
from dashboard.app import create_app
from dashboard.tests import fixtures


# ============================================================ normalize (pure)
def test_normalize_candidates_shape_and_limit():
    ideas = [
        {"titles": ["How noise-cancelling works", "alt title"], "confidence": "high",
         "why": "evergreen + searched"},
        {"title": "Single-title idea", "confidence": "med", "angle": "trending now"},
        {"confidence": "low"},                       # no title → "(untitled)"
        "garbage",                                   # non-dict → skipped
    ] + [{"titles": [f"extra {i}"]} for i in range(10)]
    cands = intake.normalize_candidates(ideas, limit=6)
    assert len(cands) == 6                            # limited
    assert cands[0]["title"] == "How noise-cancelling works"   # first title wins
    assert cands[0]["why"] == "evergreen + searched"
    assert cands[1]["title"] == "Single-title idea" and cands[1]["why"] == "trending now"
    assert cands[2]["title"] == "(untitled)"
    assert all("idx" in c and "confidence" in c for c in cands)


# ============================================================ endpoint
def _client(tmp_path, find_fn=None, intake_mode=None):
    pdir, _ = fixtures.build_projects(tmp_path)
    app = create_app(projects_dir=pdir)
    app.state.settings_path = tmp_path / "control_room_settings.json"
    if find_fn is not None:
        app.state.find_topics_fn = find_fn
    if intake_mode:
        from dashboard import settings_store
        settings_store.save_settings(app.state.settings_path,
                                     {"defaults": {"intake_mode": intake_mode}})
    c = TestClient(app)
    c._app = app
    return c


def _fake_topics(niche):
    return {"ok": True, "count": 2, "ideas": [
        {"titles": ["Why " + niche + " is booming"], "confidence": "high", "why": "evergreen"},
        {"titles": ["The dark side of " + niche], "confidence": "med", "why": "controversy"},
    ]}


def test_intake_returns_candidates(tmp_path):
    c = _client(tmp_path, find_fn=_fake_topics)
    r = c.post("/api/intake/topics", json={"niche": "home espresso"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["niche"] == "home espresso"
    assert len(body["candidates"]) == 2
    assert body["candidates"][0]["title"].startswith("Why home espresso")
    assert body["auto_pick"] is False                # default intake_mode = pick


def test_intake_auto_pick_reflects_settings(tmp_path):
    c = _client(tmp_path, find_fn=_fake_topics, intake_mode="auto")
    body = c.post("/api/intake/topics", json={"niche": "deep sea facts"}).json()
    assert body["auto_pick"] is True and body["intake_mode"] == "auto"


def test_intake_rejects_bad_niche(tmp_path):
    c = _client(tmp_path, find_fn=_fake_topics)
    assert c.post("/api/intake/topics", json={"niche": "x"}).status_code == 400
    assert c.post("/api/intake/topics", json={}).status_code == 400


def test_intake_handles_no_topics_gracefully(tmp_path):
    c = _client(tmp_path, find_fn=lambda niche: {"ok": False,
                                                 "text": "Scout found no usable videos."})
    r = c.post("/api/intake/topics", json={"niche": "extremely narrow niche here"})
    assert r.status_code == 200                       # not a 500 — a clean degraded payload
    body = r.json()
    assert body["ok"] is False and body["candidates"] == []
    assert "no usable" in body["error"].lower()


def test_intake_survives_a_raising_scout(tmp_path):
    def boom(niche):
        raise RuntimeError("YouTube quota exhausted")
    c = _client(tmp_path, find_fn=boom)
    r = c.post("/api/intake/topics", json={"niche": "some real niche"})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert "quota" in r.json()["error"].lower()


def test_settings_persists_intake_mode(tmp_path):
    from dashboard import settings_store as st
    p = tmp_path / "control_room_settings.json"
    saved = st.save_settings(p, {"defaults": {"intake_mode": "auto"}})
    assert saved["defaults"]["intake_mode"] == "auto"
    # an invalid mode coerces back to the safe default
    saved2 = st.save_settings(p, {"defaults": {"intake_mode": "nonsense"}})
    assert saved2["defaults"]["intake_mode"] == "pick"
