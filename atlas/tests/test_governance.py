"""The governance seam the chat UI renders — pure, file-backed, no orchestration.

Everything routes through the EXISTING CEO surface: boundary (CEO_DIR, journal,
request queue, STOP kill-switch) and ceo.state. The web UI is a thin renderer over
these functions; testing them here keeps the Playwright smoke test small.
"""
import json
import os

import boundary
from ceo import governance as gov
from ceo import state as ceo_state


def _seed_request(ceo_dir, **fields):
    """Append a request straight to the queue (as request_from_ceo would)."""
    rec = {"ts": 1.0, "kind": "approval", "what": "do a thing", "why": "because",
           "how_to_provide": "approve"}
    rec.update(fields)
    p = ceo_dir / "requests.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


import pytest


@pytest.fixture
def ceo_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "CEO_DIR", tmp_path / "ceo")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(tmp_path / ".env"))
    return tmp_path / "ceo"


# ----------------------------------------------------------------------
# Digest panel
# ----------------------------------------------------------------------
def test_digest_panel_shows_state_journal_milestones_budget_killswitch(ceo_tmp):
    boundary.ceo_log("started the espresso video")
    boundary.ceo_log("fact-check passed clean")
    panel = gov.digest_panel()
    assert "CEO Digest" in panel
    assert "fact-check passed clean" in panel          # latest journal
    assert "Monetization eligibility" in panel         # a default milestone
    assert "Budget" in panel and "Kill switch" in panel


# ----------------------------------------------------------------------
# Request queue: pending, resolve, classify by checkpoint
# ----------------------------------------------------------------------
def test_pending_requests_excludes_resolved(ceo_tmp):
    _seed_request(ceo_tmp, what="ask one")
    _seed_request(ceo_tmp, what="ask two")
    assert len(gov.pending_requests()) == 2
    gov.approve_request(0, note="ok")
    pend = gov.pending_requests()
    assert len(pend) == 1 and pend[0]["what"] == "ask two"


def test_checkpoint_type_classification(ceo_tmp):
    assert gov.checkpoint_type({"kind": "approval",
                                "what": "make 'X' PUBLIC on YouTube"}) == "publish"
    assert gov.checkpoint_type({"kind": "budget", "what": "$50 for footage"}) == "spend"
    assert gov.checkpoint_type({"kind": "approval",
                                "what": "promote a new agent glint"}) == "create-agent"
    assert gov.checkpoint_type({"kind": "approval",
                                "what": "apply a registry.py change"}) == "core-edit"
    assert gov.checkpoint_type({"kind": "api_key", "what": "a key"}) == "api_key"


def test_approve_and_decline_record_resolution_and_journal(ceo_tmp):
    _seed_request(ceo_tmp, what="publish ask")
    gov.approve_request(0, note="ship it")
    res = gov.load_resolutions()
    assert res[0]["decision"] == "approved"
    # journaled through the SAME boundary seam
    assert any("APPROVED" in l for l in (ceo_tmp / "journal.jsonl").read_text().splitlines())


# ----------------------------------------------------------------------
# Inline fulfillment: key -> .env, file -> placed, info -> noted
# ----------------------------------------------------------------------
def test_provide_api_key_lands_in_env_and_never_logs_value(ceo_tmp, tmp_path):
    _seed_request(ceo_tmp, kind="api_key", what="YT key",
                  how_to_provide="set YT_API_KEY in env")
    gov.provide_api_key(0, "YT_API_KEY", "sk-secret-value-123456")
    env = (tmp_path / ".env").read_text()
    assert "YT_API_KEY=sk-secret-value-123456" in env
    # the secret VALUE must never appear in the journal
    journal = (ceo_tmp / "journal.jsonl").read_text()
    assert "sk-secret-value-123456" not in journal
    assert "YT_API_KEY" in journal
    assert 0 in gov.load_resolutions()


def test_suggested_env_var_parsed_from_request(ceo_tmp):
    req = {"kind": "api_key", "how_to_provide": "add it to .env as YT_API_KEY please"}
    assert gov.suggested_env_var(req) == "YT_API_KEY"


def test_provide_asset_places_file(ceo_tmp):
    _seed_request(ceo_tmp, kind="asset", what="a logo")
    gov.provide_asset(0, "logo.png", b"\x89PNG")
    placed = ceo_tmp / "provided_assets" / "logo.png"
    assert placed.read_bytes() == b"\x89PNG"
    assert gov.load_resolutions()[0]["decision"] == "fulfilled"


# ----------------------------------------------------------------------
# Kill switch + budget meter
# ----------------------------------------------------------------------
def test_kill_switch_toggle(ceo_tmp):
    assert gov.kill_switch_active() is False
    gov.set_kill_switch(True)
    assert gov.kill_switch_active() is True and (ceo_tmp / "STOP").exists()
    gov.set_kill_switch(False)
    assert gov.kill_switch_active() is False and not (ceo_tmp / "STOP").exists()


def test_budget_meter_reads_state(ceo_tmp):
    st = ceo_state.load()
    st["budget"] = {"ceiling_usd": 50.0, "spent_usd": 12.5}
    ceo_state.save(st)
    m = gov.budget_meter()
    assert m["ceiling"] == 50.0 and m["spent"] == 12.5 and m["remaining"] == 37.5
