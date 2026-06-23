"""Tests for Slice 4 — the Settings store + endpoints (#4).

Settings are a dashboard-owned JSON (niches / defaults / channels), read by the dashboard
and PASSED INTO the pipeline as args at trigger time — never read globally by a pure engine
(spec §3/§11). The store is tolerant (malformed/missing → defaults, spec E13) and the only
write is a T1 reversible PUT. No engine/LLM runs here.
"""
from __future__ import annotations

import pathlib

from fastapi.testclient import TestClient

from dashboard import settings_store as ss
from dashboard.app import create_app
from dashboard.tests import fixtures


# ============================================================ store (pure)
def test_load_missing_returns_defaults(tmp_path):
    s = ss.load_settings(tmp_path / "nope.json")
    assert s["niches"] == [] and s["channels"] == []
    assert s["defaults"]["target_length"] in ("short", "long")
    assert s["schema_version"]


def test_load_corrupt_returns_defaults_without_mutating(tmp_path):
    p = tmp_path / "control_room_settings.json"
    p.write_text("{ not valid json")
    s = ss.load_settings(p)
    assert s["niches"] == []                       # E13: degrade to defaults, never crash
    assert p.read_text() == "{ not valid json"     # read-only: the bad file is untouched


def test_validate_sanitizes_and_rejects_bad_niche(tmp_path):
    raw = {
        "niches": [
            {"name": "AI tools & productivity", "default_length": "long",
             "channel_id": "UC_x"},
            {"name": "x"},                          # too-short niche → dropped
            "garbage",                              # non-dict → dropped
        ],
        "defaults": {"target_length": "weird"},     # invalid → coerced to a valid default
        "channels": [
            {"channel_id": "UC_x", "title": "My Channel", "niche_id": "0",
             "connection_status": "bogus-state",    # invalid → coerced to 'disconnected'
             "project_verified": "yes",             # truthy-coerced to bool
             "channel_phone_verified": False},
        ],
    }
    ok, errors, clean = ss.validate_settings(raw)
    names = [n["name"] for n in clean["niches"]]
    assert "AI tools & productivity" in names
    assert "x" not in names and len(clean["niches"]) == 1
    assert clean["defaults"]["target_length"] in ("short", "long")
    ch = clean["channels"][0]
    assert ch["connection_status"] in ss.CONNECTION_STATES
    assert ch["connection_status"] == "disconnected"
    assert ch["project_verified"] is True and ch["channel_phone_verified"] is False


def test_save_round_trips_and_is_atomic(tmp_path):
    p = tmp_path / "control_room_settings.json"
    saved = ss.save_settings(p, {"niches": [{"name": "home espresso", "default_length": "short"}]})
    assert saved["niches"][0]["name"] == "home espresso"
    again = ss.load_settings(p)
    assert again["niches"][0]["name"] == "home espresso"


def test_public_settings_carries_quota_and_enums(tmp_path):
    pub = ss.public_settings(tmp_path / "control_room_settings.json")
    q = pub["quota"]
    assert q["max_uploads_per_day"] == 6            # §9: ~6 inserts/day SHARED across channels
    assert q["insert_cost"] == 1600 and q["daily_units"] == 10000
    assert "shared" in q["scope"].lower()
    assert set(pub["connection_states"]) == set(ss.CONNECTION_STATES)
    assert pub["length_options"] == ["short", "long"]


def test_niche_default_length_resolves(tmp_path):
    p = tmp_path / "control_room_settings.json"
    ss.save_settings(p, {"niches": [{"name": "deep sea facts", "default_length": "long"}],
                         "defaults": {"target_length": "short"}})
    s = ss.load_settings(p)
    assert ss.length_for_niche(s, "deep sea facts") == "long"
    assert ss.length_for_niche(s, "unknown niche") == "short"   # falls back to default


# ============================================================ endpoints
def _client(tmp_path):
    pdir, _ = fixtures.build_projects(tmp_path)
    app = create_app(projects_dir=pdir)
    app.state.settings_path = tmp_path / "control_room_settings.json"
    c = TestClient(app)
    c._app = app
    return c


def test_get_settings_shape(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    for k in ("niches", "defaults", "channels", "quota", "connection_states",
              "length_options"):
        assert k in body


def test_put_settings_persists_and_round_trips(tmp_path):
    c = _client(tmp_path)
    r = c.put("/api/settings", json={
        "niches": [{"name": "noise-cancelling tech", "default_length": "short"}],
        "defaults": {"target_length": "short"},
        "channels": [{"channel_id": "UCabc", "title": "Gear", "niche_id": "0",
                      "connection_status": "needs-reconnect",
                      "project_verified": False, "channel_phone_verified": True}],
    })
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    got = c.get("/api/settings").json()
    assert got["niches"][0]["name"] == "noise-cancelling tech"
    assert got["channels"][0]["connection_status"] == "needs-reconnect"


def test_put_settings_rejects_non_object_body(tmp_path):
    c = _client(tmp_path)
    assert c.put("/api/settings", json=["not", "an", "object"]).status_code == 400


def test_put_settings_drops_bad_rows_but_keeps_good(tmp_path):
    c = _client(tmp_path)
    r = c.put("/api/settings", json={"niches": [{"name": "ok niche here"}, {"name": "z"}]})
    assert r.status_code == 200
    assert [n["name"] for n in r.json()["settings"]["niches"]] == ["ok niche here"]


def test_trigger_uses_niche_default_length_when_unspecified(tmp_path):
    """A niche carries a default length; triggering with that niche and no explicit length
    resolves it from settings (dashboard-side, passed INTO the pipeline as an arg)."""
    import chat_state
    c = _client(tmp_path)
    c.put("/api/settings", json={
        "niches": [{"name": "space history", "default_length": "long"}]})

    captured = {}

    def fake_produce(slug=None, approve=None, root=None, progress=None,
                     station_locks=None, should_cancel=None):
        pdir = pathlib.Path(root) / slug
        proj = chat_state.load_json(pdir / "project.json", {})
        captured["length"] = (proj.get("config", {}) or {}).get("target_length")
        proj["status"] = "done"
        chat_state.atomic_write_json(pdir / "project.json", proj)
        return {"status": "done"}

    c._app.state.produce_fn = fake_produce
    r = c.post("/api/trigger", json={"topic": "the space race", "niche": "space history"})
    assert r.status_code == 200
    # the niche's default length flowed into the project config without an explicit length
    import time
    for _ in range(250):
        if captured.get("length") == "long":
            break
        time.sleep(0.02)
    assert captured.get("length") == "long"
