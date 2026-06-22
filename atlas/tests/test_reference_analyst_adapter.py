"""Vera's adapter — driven OFFLINE via a fake store/engine (no ffmpeg, no cv2, no API).

These mock the engine seams (`reference_analyst._reference_store` / `_reference_engine`
/ `_vision_fn`) so they run with no FFmpeg and no network, and assert the adapter's
boundary contract:

- it parses 'videos' leniently (a single path, a comma/space list, or a JSON array),
- it validates existence and degrades (skips missing files with a note), never crashes,
- the rubric it returns VALIDATES against the frozen `reference_rubric` contract,
- it stamps `schema_version` at the boundary (from the engine's RUBRIC_VERSION),
- the digest is compact: a targets summary + the open_questions,
- 'ceo_prefs' round-trips into the store call (persistence is the store's job).
"""
import pathlib

import pytest

import contracts
from adapters import reference_analyst as ra
from registry import get_entry


# ----------------------------------------------------------------------
# A tiny fake of Vera's merging store: validate_videos + build_standard.
# build_standard returns a contract-valid rubric and records how it was called.
# ----------------------------------------------------------------------
def _rubric_for(videos):
    names = [pathlib.Path(v).name for v in videos]
    return {
        # NOTE: deliberately NO schema_version here — the adapter must stamp it.
        "source_videos": names,
        "targets": {
            "pacing": {"avg_shot_sec": {"value": 2.2, "band": [2.0, 2.4]},
                       "cuts_per_min": {"value": 27.0, "band": [25.0, 29.0]}},
            "motion": {"kinetic_score": {"value": 0.06, "band": [0.05, 0.07]}},
            "color": {"saturation": {"value": 0.55, "band": [0.5, 0.6]},
                      "brightness": {"value": 0.5, "band": None},
                      "palette_samples": []},
            "audio": {"integrated_lufs": {"value": -13.5, "band": [-14.0, -13.0]},
                      "speech_ratio": {"value": 0.72, "band": [0.7, 0.75]}},
            "structure": {"duration_sec": {"value": 62.0, "band": [60.0, 64.0]},
                          "fps": {"value": 30.0, "band": None}},
        },
        "judged": {"status": "pending", "needs": ["visual_style"], "frames": []},
        "open_questions": [
            {"id": "pace", "sets": "pacing.avg_shot_sec",
             "plain": "It cuts about every 2.2s. Snappy, or more breathing room?"}],
        "ceo_prefs": {},
        "raw": [],
    }


class _FakeStore:
    def __init__(self):
        self.calls = []

    def validate_videos(self, video_paths):
        if isinstance(video_paths, (str, pathlib.Path)):
            video_paths = [video_paths]
        existing, missing = [], []
        for p in video_paths:
            (existing if pathlib.Path(p).is_file() else missing).append(str(p))
        return existing, missing

    def build_standard(self, standard, videos, *, vision_fn=None, ceo_prefs=None):
        self.calls.append({"standard": standard, "videos": list(videos),
                           "vision_fn": vision_fn, "ceo_prefs": ceo_prefs})
        return _rubric_for(videos)


class _FakeEngine:
    RUBRIC_VERSION = "reference_rubric/1.0"


@pytest.fixture
def fake(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(ra, "_reference_store", lambda: store)
    monkeypatch.setattr(ra, "_reference_engine", lambda: _FakeEngine())
    monkeypatch.setattr(ra, "_vision_fn", lambda: None)  # objective-only in tests
    return store


def _adapter():
    return ra.ReferenceAnalystAdapter(get_entry("reference_analyst"))


# ----------------------------------------------------------------------
# The happy path: a validated, stamped rubric + a compact digest
# ----------------------------------------------------------------------
def test_run_job_returns_validated_stamped_digest(tmp_path, fake):
    v1 = tmp_path / "ref1.mp4"; v1.write_bytes(b"x")
    v2 = tmp_path / "ref2.mp4"; v2.write_bytes(b"x")

    out = _adapter().run_job("build_rubric", None,
                             videos=f"{v1}, {v2}", ceo_prefs="")
    assert out["ok"] is True
    # digest is compact: targets summary + the open question (not the raw rubric)
    assert "Rubric for standard 'default'" in out["text"]
    assert "avg shot (s): 2.2" in out["text"]
    assert "[pace]" in out["text"]

    # the store actually received BOTH parsed paths
    assert fake.calls and fake.calls[0]["videos"] == [str(v1), str(v2)]


def test_returned_rubric_validates_and_is_stamped(tmp_path, fake, monkeypatch):
    # Re-run the build directly to inspect the stamped rubric the adapter validates.
    v1 = tmp_path / "ref1.mp4"; v1.write_bytes(b"x")
    rubric = ra.run_build_rubric([str(v1)], ceo_prefs=None)
    assert rubric["schema_version"] == "reference_rubric/1.0"  # stamped at the boundary
    ok, errors = contracts.validate("reference_rubric", rubric)
    assert ok, errors


def test_videos_parse_json_array(tmp_path, fake):
    v1 = tmp_path / "a.mp4"; v1.write_bytes(b"x")
    v2 = tmp_path / "b.mp4"; v2.write_bytes(b"x")
    _adapter().run_job("build_rubric", None,
                       videos=f'["{v1}", "{v2}"]', ceo_prefs="")
    assert fake.calls[0]["videos"] == [str(v1), str(v2)]


def test_ceo_prefs_round_trip_into_the_store(tmp_path, fake):
    v1 = tmp_path / "a.mp4"; v1.write_bytes(b"x")
    _adapter().run_job("build_rubric", None, videos=str(v1),
                       ceo_prefs='{"pace": "keep snappy"}')
    assert fake.calls[0]["ceo_prefs"] == {"pace": "keep snappy"}


# ----------------------------------------------------------------------
# Graceful degradation: missing files, no files, wrong job
# ----------------------------------------------------------------------
def test_missing_files_are_skipped_not_crashed(tmp_path, fake):
    real = tmp_path / "real.mp4"; real.write_bytes(b"x")
    ghost = tmp_path / "ghost.mp4"
    out = _adapter().run_job("build_rubric", None,
                             videos=f"{real} {ghost}", ceo_prefs="")
    assert out["ok"] is True
    assert out["missing"] == [str(ghost)]
    # only the existing file reached the store
    assert fake.calls[0]["videos"] == [str(real)]


def test_no_existing_files_degrades_cleanly(tmp_path, fake):
    out = _adapter().run_job("build_rubric", None,
                             videos=str(tmp_path / "nope.mp4"), ceo_prefs="")
    assert out["ok"] is False
    assert "exist locally" in out["text"]
    assert not fake.calls  # never reached the engine


def test_no_videos_is_a_clean_refusal(fake):
    out = _adapter().run_job("build_rubric", None, videos="", ceo_prefs="")
    assert out["ok"] is False
    assert "No reference video paths" in out["text"]


def test_unknown_job_name(fake):
    out = _adapter().run_job("do_magic", None, videos="x")
    assert out["ok"] is False
    assert "no job named" in out["text"]


def test_contract_failure_is_reported_not_raised(tmp_path, monkeypatch):
    # A store that emits a malformed rubric (missing targets) must surface a clean
    # validation failure at the boundary, not crash the meeting.
    class _BadStore(_FakeStore):
        def build_standard(self, standard, videos, *, vision_fn=None, ceo_prefs=None):
            return {"source_videos": [], "judged": {}, "open_questions": []}

    bad = _BadStore()
    monkeypatch.setattr(ra, "_reference_store", lambda: bad)
    monkeypatch.setattr(ra, "_reference_engine", lambda: _FakeEngine())
    monkeypatch.setattr(ra, "_vision_fn", lambda: None)

    v1 = tmp_path / "a.mp4"; v1.write_bytes(b"x")
    out = _adapter().run_job("build_rubric", None, videos=str(v1), ceo_prefs="")
    assert out["ok"] is False
    assert "failed contract validation" in out["text"]
