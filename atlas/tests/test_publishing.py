"""Closing the loop to GATED publishing.

- compliance.check: a hard gate that BLOCKS unless licenses are allowlisted +
  attributed + local, no real-person likeness, fact-check passed, music/SFX
  licensed, and the advertiser-friendly + originality checklist passes.
- youtube: a thin API seam — credentials from env ONLY (never code), upload as
  unlisted by default, analytics fetch.
- publish.prepare_publish: gate → unlisted upload → compliance report → a CEO
  approval request. Nothing goes public without the human yes.
"""
import json
import os

import pytest

import boundary
import compliance
import publish
import youtube


# ----------------------------------------------------------------------
# Build a synthetic project dir with tunable compliance posture.
# ----------------------------------------------------------------------
def _mk_project(tmp_path, *, asset_license="CC0", asset_status="cleared",
                make_asset_file=True, asset_flag="", attribution="Jane Doe via Wikimedia",
                factcheck="pass", music_status="cleared", music_license="CC-BY 4.0",
                narration="A calm, original explainer about how rainbows form."):
    pdir = tmp_path / "projects" / "vid1"
    (pdir / "assets").mkdir(parents=True)
    (pdir / "audio").mkdir(parents=True)
    (pdir / "video.mp4").write_bytes(b"\x00\x00")

    uri = "assets/a1.jpg"
    if make_asset_file:
        (pdir / uri).write_bytes(b"\xff\xd8")

    (pdir / "project.json").write_text(json.dumps(
        {"slug": "vid1", "title": "How Rainbows Form", "topic": "rainbows",
         "niche": "everyday science"}))
    (pdir / "script.json").write_text(json.dumps(
        {"working_title": "How Rainbows Form", "hook": "Ever wonder why?",
         "cta": "Subscribe for more.",
         "scenes": [{"scene_no": 1, "narration": narration, "on_screen_text": "Rainbows"}]}))
    (pdir / "factcheck_report.json").write_text(json.dumps(
        {"schema_version": "1.0", "verdict": factcheck,
         "summary": {"verified": 3, "flagged": 0, "unverifiable": 0}, "claims": []}))
    (pdir / "asset_manifest.json").write_text(json.dumps(
        {"schema_version": "1.0", "assets": [
            {"asset_id": "a1", "scene_no": 1, "type": "image", "uri": uri,
             "license": asset_license, "attribution": attribution,
             "status": asset_status, "flag": asset_flag}]}))
    (pdir / "audio" / "audio_manifest.json").write_text(json.dumps(
        {"schema_version": "1.1", "total_duration_sec": 60, "tracks": [
            {"role": "narration", "uri": "audio/vo.wav", "status": "cleared",
             "license": "n/a (Kokoro TTS)"},
            {"role": "music", "uri": "audio/bed.mp3", "status": music_status,
             "license": music_license, "attribution": "Kevin MacLeod (incompetech)"},
            {"role": "sfx", "uri": "audio/accent.wav", "status": "cleared",
             "license": "CC0 1.0"}]}))
    return pdir


# ----------------------------------------------------------------------
# 1. Compliance gate
# ----------------------------------------------------------------------
def test_compliance_passes_a_clean_project(tmp_path):
    rep = compliance.check(_mk_project(tmp_path))
    assert rep["passed"] is True, rep["blockers"]
    assert "PASS" in compliance.format_report(rep)


def test_compliance_blocks_non_allowlisted_license(tmp_path):
    rep = compliance.check(_mk_project(tmp_path, asset_license="Pixabay License"))
    assert rep["passed"] is False
    assert any("license" in b.lower() for b in rep["blockers"])


def test_compliance_blocks_placeholder_status(tmp_path):
    rep = compliance.check(_mk_project(tmp_path, asset_status="placeholder"))
    assert rep["passed"] is False


def test_compliance_blocks_missing_local_file(tmp_path):
    rep = compliance.check(_mk_project(tmp_path, make_asset_file=False))
    assert rep["passed"] is False
    assert any("local file" in b.lower() or "file" in b.lower() for b in rep["blockers"])


def test_compliance_blocks_on_factcheck_block(tmp_path):
    rep = compliance.check(_mk_project(tmp_path, factcheck="block"))
    assert rep["passed"] is False
    assert any("fact" in b.lower() for b in rep["blockers"])


def test_compliance_blocks_unlicensed_music(tmp_path):
    rep = compliance.check(_mk_project(tmp_path, music_status="placeholder",
                                       music_license="unlicensed (placeholder)"))
    assert rep["passed"] is False
    assert any("music" in b.lower() or "sfx" in b.lower() or "audio" in b.lower()
               for b in rep["blockers"])


def test_compliance_blocks_real_person_likeness(tmp_path):
    rep = compliance.check(_mk_project(
        tmp_path, asset_flag="identifiable people / trademarks NOT cleared"))
    assert rep["passed"] is False
    assert any("likeness" in b.lower() or "person" in b.lower() or "people" in b.lower()
               for b in rep["blockers"])


def test_compliance_requires_attribution_for_cc_by(tmp_path):
    rep = compliance.check(_mk_project(tmp_path, asset_license="CC-BY 4.0",
                                       attribution=""))
    assert rep["passed"] is False
    assert any("attribut" in b.lower() for b in rep["blockers"])


# ----------------------------------------------------------------------
# 2. YouTube seam — credentials from env only
# ----------------------------------------------------------------------
def test_youtube_credentials_missing_raises(monkeypatch):
    for k in youtube.ENV_VARS:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(youtube.MissingCredentials):
        youtube.credentials()


def test_youtube_upload_uses_injected_api_unlisted():
    class _FakeApi:
        def insert_video(self, **kw):
            return {"id": "yt_abc123", "privacyStatus": kw["privacy"]}

    res = youtube.upload(title="t", description="d", tags=[], video_path="/x.mp4",
                         api=_FakeApi())
    assert res["video_id"] == "yt_abc123" and res["privacy"] == "unlisted"


# ----------------------------------------------------------------------
# 3. publish.prepare_publish — the gated, human-checkpointed flow
# ----------------------------------------------------------------------
@pytest.fixture
def ceo_tmp(tmp_path, monkeypatch):
    import projects
    from ceo import state as ceo_state
    monkeypatch.setattr(boundary, "CEO_DIR", tmp_path / "ceo")
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path / "projects")
    # register the project in CEO state so analytics/status can attach
    st = ceo_state.load()
    ceo_state.add_video(st, slug="vid1", channel="main", topic="rainbows",
                        status="produced")
    return tmp_path


class _FakeUploader:
    def __init__(self):
        self.called = False

    def __call__(self, *, title, description, tags, video_path, privacy="unlisted"):
        self.called = True
        return {"video_id": "yt_live_1", "privacy": privacy,
                "url": "https://youtu.be/yt_live_1", "status": "uploaded"}


def test_prepare_publish_blocks_failing_video(ceo_tmp, monkeypatch):
    import projects
    _mk_project(ceo_tmp, asset_license="Pixabay License")  # -> projects/vid1
    up = _FakeUploader()
    res = publish.prepare_publish("vid1", uploader=up)
    assert res["passed"] is False and res["uploaded"] is False
    assert up.called is False                      # never uploaded
    assert res["approval"] is None                 # no go-public ask for a blocked video
    # a compliance report was still written for the human
    assert (projects.PROJECTS_DIR / "vid1" / "compliance_report.txt").exists()
    # no go-public approval landed in the queue
    reqs = (ceo_tmp / "ceo" / "requests.jsonl")
    if reqs.exists():
        assert not any(json.loads(l)["kind"] == "approval"
                       and "public" in json.loads(l)["what"].lower()
                       for l in reqs.read_text().splitlines())


def test_prepare_publish_passing_uploads_unlisted_and_asks(ceo_tmp):
    import projects
    from ceo import state as ceo_state
    _mk_project(ceo_tmp)                            # clean -> passes
    up = _FakeUploader()
    res = publish.prepare_publish("vid1", uploader=up)

    assert res["passed"] is True and res["uploaded"] is True
    assert res["privacy"] == "unlisted"            # NOT public
    assert res["video_id"] == "yt_live_1"
    # a human-readable compliance report was written
    assert (projects.PROJECTS_DIR / "vid1" / "compliance_report.txt").exists()
    # a CEO approval request to GO PUBLIC was filed
    assert res["approval"]["kind"] == "approval"
    reqs = (ceo_tmp / "ceo" / "requests.jsonl").read_text().splitlines()
    assert any(json.loads(l)["kind"] == "approval" for l in reqs)
    # state reflects the unlisted upload (still not public)
    v = next(v for v in ceo_state.load()["videos"] if v["slug"] == "vid1")
    assert v["video_id"] == "yt_live_1" and v["status"] != "public"


def test_prepare_publish_never_sets_public(ceo_tmp):
    _mk_project(ceo_tmp)
    res = publish.prepare_publish("vid1", uploader=_FakeUploader())
    assert res["privacy"] in ("unlisted", "private")
    assert res["privacy"] != "public"


# ----------------------------------------------------------------------
# 4. Analytics feed back into the CEO loop
# ----------------------------------------------------------------------
def test_ingest_analytics_updates_state(ceo_tmp):
    from ceo import state as ceo_state
    st = ceo_state.load()
    ceo_state.update_video(st, "vid1", video_id="yt_live_1", status="public")

    def _fake_fetch(video_id, **kw):
        return {"views": 5000, "watch_time_min": 8000, "rpm_usd": 4.0,
                "estimated_revenue_usd": 20.0}

    res = publish.ingest_analytics("vid1", fetch=_fake_fetch)
    assert res["views"] == 5000
    v = next(v for v in ceo_state.load()["videos"] if v["slug"] == "vid1")
    assert v["metrics"]["views"] == 5000 and v["metrics"]["rpm_usd"] == 4.0


# ----------------------------------------------------------------------
# 5. Tool registration + CEO-cycle wiring
# ----------------------------------------------------------------------
def test_build_server_registers_publishing_tools():
    import registry
    import tools
    from progress import list_progress
    _s, allowed = tools.build_server(registry.build_adapters(), list_progress()[0])
    for name in ("check_compliance", "youtube_upload", "youtube_analytics"):
        assert f"mcp__atlas__{name}" in allowed


def test_cycle_publishes_an_evaluated_video():
    from ceo import cycle
    st = {"niches": ["x"], "backlog": [],
          "videos": [{"slug": "v1", "status": "produced", "evaluated": True}]}
    a = cycle.choose_action(st)
    assert a["kind"] == "publish_video" and a["target"]["slug"] == "v1"
