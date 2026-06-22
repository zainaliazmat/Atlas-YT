"""Offline proof for Vera's reference engine + the merging rubric store.

Run (from the project folder):  python -m pytest tests/test_reference_engine.py

NO ffmpeg, NO cv2, NO network: `analyze_video` is the single external seam and it is
monkeypatched throughout, so we assert the PURE plumbing and the HARD invariants only:
  - _band / _t: single-value soft-padding vs. multi-value spread; null handling
  - _dig: nested traversal, tolerant of missing keys
  - _open_questions: the pace/talk/style questions, each tagged with what it `sets`
  - build_targets/build_judged: the rubric shape; judged seam pending/scored/degraded
  - merge_rubric: feeding MORE references TIGHTENS the bands (the whole point of merge)
  - build_standard: durable, atomic, merges across separate invocations; missing files
    are skipped with a note, never a crash
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import reference_engine as engine  # noqa: E402
import rubric_store  # noqa: E402


# ----------------------------------------------------------------------
# A tiny stub analysis: the exact shape analyze_video returns, parameterized.
# ----------------------------------------------------------------------
def _analysis(name, *, avg_shot, cuts_per_min, motion, saturation, brightness,
              lufs, speech, duration, fps, palette=None):
    return {
        "video": name,
        "container": {"duration_sec": duration, "fps": fps, "width": 1920,
                      "height": 1080, "has_audio": True},
        "pacing": {"shot_count": 10, "avg_shot_sec": avg_shot,
                   "cuts_per_min": cuts_per_min},
        "motion_score": motion,
        "color": {"palette": palette or [{"hex": "#102030", "weight": 1.0}],
                  "saturation": saturation, "brightness": brightness},
        "audio": {"integrated_lufs": lufs, "loudness_range": 5.0, "true_peak_db": -1.0,
                  "speech_ratio": speech, "avg_pause_sec": 0.4},
        "frames": [f"/frames/{name}_f0.jpg", f"/frames/{name}_f1.jpg"],
    }


# ============================ pure helpers ==================================
def test_band_single_value_pads_soft():
    lo, hi = engine._band([2.0])
    assert lo < 2.0 < hi  # one value -> soft band, not a point


def test_band_zero_value_pads_with_floor():
    # abs(0)*pad == 0, so the floor (0.1) kicks in instead of a zero-width band.
    assert engine._band([0.0]) == [-0.1, 0.1]


def test_band_spread_is_min_max():
    assert engine._band([1.0, 3.0, 2.0]) == [1.0, 3.0]


def test_band_ignores_non_numbers_and_empties():
    assert engine._band([]) is None
    assert engine._band([None, "x"]) is None


def test_t_central_value_and_band():
    t = engine._t([2.0, 4.0])
    assert t["value"] == 3.0
    assert t["band"] == [2.0, 4.0]


def test_t_all_missing_is_null_target():
    assert engine._t([None, None]) == {"value": None, "band": None}


def test_dig_walks_and_tolerates_missing():
    obj = {"a": {"b": {"c": 7}}}
    assert engine._dig(obj, ["a", "b", "c"]) == 7
    assert engine._dig(obj, ["a", "x", "c"]) is None
    assert engine._dig(None, ["a"]) is None


def test_open_questions_tag_what_they_set():
    targets = engine.build_targets([_analysis(
        "a", avg_shot=2.0, cuts_per_min=28.0, motion=0.05, saturation=0.5,
        brightness=0.5, lufs=-14.0, speech=0.7, duration=60.0, fps=30.0)])
    qs = engine._open_questions(targets)
    by = {q["id"]: q for q in qs}
    assert by["pace"]["sets"] == "pacing.avg_shot_sec"
    assert by["talk"]["sets"] == "audio.speech_ratio"
    assert by["style"]["sets"] == "judged.style_match"
    assert "2.0s" in by["pace"]["plain"] or "2.0" in by["pace"]["plain"]
    assert "70%" in by["talk"]["plain"]


# ============================ build_targets / judged ========================
def test_build_targets_shape_and_values():
    a = _analysis("a", avg_shot=2.0, cuts_per_min=28.0, motion=0.05, saturation=0.5,
                  brightness=0.5, lufs=-14.0, speech=0.7, duration=60.0, fps=30.0)
    t = engine.build_targets([a])
    assert t["pacing"]["avg_shot_sec"]["value"] == 2.0
    assert t["motion"]["kinetic_score"]["value"] == 0.05
    assert t["audio"]["integrated_lufs"]["value"] == -14.0
    assert t["structure"]["duration_sec"]["value"] == 60.0
    # palette samples are carried through per video for the judged/CEO eye
    assert t["color"]["palette_samples"][0][0]["hex"] == "#102030"


def test_build_judged_pending_without_seam():
    j = engine.build_judged([_analysis(
        "a", avg_shot=2.0, cuts_per_min=28.0, motion=0.05, saturation=0.5,
        brightness=0.5, lufs=-14.0, speech=0.7, duration=60.0, fps=30.0)])
    assert j["status"] == "pending"
    # the style-profile needs — alignment is intentionally NOT among them
    assert "visual_narration_alignment" not in j["needs"]
    assert "visual_style" in j["needs"]
    assert j["frames"] == ["/frames/a_f0.jpg", "/frames/a_f1.jpg"]


def test_build_judged_scored_with_seam():
    seam = lambda frames: {"visual_style": "editorial", "frames_seen": len(frames)}
    j = engine.build_judged([_analysis(
        "a", avg_shot=2.0, cuts_per_min=28.0, motion=0.05, saturation=0.5,
        brightness=0.5, lufs=-14.0, speech=0.7, duration=60.0, fps=30.0)], seam)
    assert j["status"] == "scored"
    assert j["assessment"]["visual_style"] == "editorial"


def test_build_judged_seam_failure_degrades_not_crashes():
    def boom(frames):
        raise RuntimeError("vision brain offline")
    j = engine.build_judged([_analysis(
        "a", avg_shot=2.0, cuts_per_min=28.0, motion=0.05, saturation=0.5,
        brightness=0.5, lufs=-14.0, speech=0.7, duration=60.0, fps=30.0)], boom)
    assert j["status"] == "draft"
    assert "vision brain offline" in j["error"]


# ============================ the MERGE (band-tightening) ===================
def test_merge_widens_band_then_recomputes_over_union():
    # Two references that DISAGREE on shot length -> the merged band spans both.
    a = _analysis("a", avg_shot=2.0, cuts_per_min=30.0, motion=0.05, saturation=0.5,
                  brightness=0.5, lufs=-14.0, speech=0.7, duration=60.0, fps=30.0)
    b = _analysis("b", avg_shot=3.0, cuts_per_min=20.0, motion=0.07, saturation=0.6,
                  brightness=0.5, lufs=-13.0, speech=0.8, duration=70.0, fps=30.0)

    first = rubric_store.merge_rubric(None, [a])
    # one video -> soft, padded band around 2.0 (not [2.0, 2.0])
    band1 = first["targets"]["pacing"]["avg_shot_sec"]["band"]
    assert band1[0] < 2.0 < band1[1]
    assert first["source_videos"] == ["a"]

    merged = rubric_store.merge_rubric(first, [b])
    # union of analyses -> band is the real spread [2.0, 3.0], value the mean
    assert merged["source_videos"] == ["a", "b"]
    assert merged["targets"]["pacing"]["avg_shot_sec"]["band"] == [2.0, 3.0]
    assert merged["targets"]["pacing"]["avg_shot_sec"]["value"] == 2.5
    assert len(merged["raw"]) == 2  # raw analyses accumulate for future merges


def test_merge_tightens_band_when_references_agree():
    # Three references that AGREE closely -> a tight band, the shared DNA.
    refs = [
        _analysis("a", avg_shot=2.0, cuts_per_min=30.0, motion=0.05, saturation=0.5,
                  brightness=0.5, lufs=-14.0, speech=0.7, duration=60.0, fps=30.0),
        _analysis("b", avg_shot=2.1, cuts_per_min=29.0, motion=0.05, saturation=0.51,
                  brightness=0.5, lufs=-14.1, speech=0.71, duration=61.0, fps=30.0),
        _analysis("c", avg_shot=1.9, cuts_per_min=31.0, motion=0.05, saturation=0.49,
                  brightness=0.5, lufs=-13.9, speech=0.69, duration=59.0, fps=30.0),
    ]
    one = rubric_store.merge_rubric(None, [refs[0]])
    three = rubric_store.merge_rubric(None, refs)
    w1 = one["targets"]["pacing"]["avg_shot_sec"]["band"]
    w3 = three["targets"]["pacing"]["avg_shot_sec"]["band"]
    assert (w3[1] - w3[0]) < (w1[1] - w1[0])  # agreeing refs -> tighter than one padded clip


def test_merge_persists_ceo_prefs_new_over_old():
    a = _analysis("a", avg_shot=2.0, cuts_per_min=30.0, motion=0.05, saturation=0.5,
                  brightness=0.5, lufs=-14.0, speech=0.7, duration=60.0, fps=30.0)
    first = rubric_store.merge_rubric(None, [a], ceo_prefs={"pace": "keep snappy"})
    assert first["ceo_prefs"]["pace"] == "keep snappy"
    # new answers win; empty values don't clobber
    merged = rubric_store.merge_rubric(first, [a],
                                       ceo_prefs={"pace": "more breathing room", "talk": ""})
    assert merged["ceo_prefs"]["pace"] == "more breathing room"
    assert "talk" not in merged["ceo_prefs"]


# ============================ build_standard (durable I/O) ==================
def test_build_standard_merges_across_invocations(tmp_path, monkeypatch):
    made = {}

    def fake_analyze(path, frames_dir=None):
        # deterministic per-path stub; records that frames_dir was offered
        made[path] = frames_dir
        idx = len(made)
        return _analysis(pathlib.Path(path).name, avg_shot=1.0 + idx, cuts_per_min=30.0,
                         motion=0.05, saturation=0.5, brightness=0.5, lufs=-14.0,
                         speech=0.7, duration=60.0, fps=30.0)

    monkeypatch.setattr(engine, "analyze_video", fake_analyze)

    # create real files so validate_videos accepts them
    v1 = tmp_path / "ref1.mp4"; v1.write_bytes(b"x")
    v2 = tmp_path / "ref2.mp4"; v2.write_bytes(b"x")
    root = tmp_path / "standards"

    r1 = rubric_store.build_standard("house", [str(v1)], root=root)
    assert r1["source_videos"] == ["ref1.mp4"]
    assert rubric_store.rubric_path("house", root).exists()

    # second invocation MERGES into the same standard (durable across calls)
    r2 = rubric_store.build_standard("house", [str(v2)], root=root)
    assert r2["source_videos"] == ["ref1.mp4", "ref2.mp4"]
    assert len(r2["raw"]) == 2


def test_build_standard_skips_missing_files_with_a_note(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "analyze_video",
                        lambda p, frames_dir=None: _analysis(
                            pathlib.Path(p).name, avg_shot=2.0, cuts_per_min=30.0,
                            motion=0.05, saturation=0.5, brightness=0.5, lufs=-14.0,
                            speech=0.7, duration=60.0, fps=30.0))
    real = tmp_path / "real.mp4"; real.write_bytes(b"x")
    root = tmp_path / "standards"
    r = rubric_store.build_standard("house", [str(real), str(tmp_path / "ghost.mp4")],
                                    root=root)
    assert r["source_videos"] == ["real.mp4"]
    assert "not found" in r["notes"] and "ghost.mp4" in r["notes"]


def test_validate_videos_splits_existing_and_missing(tmp_path):
    real = tmp_path / "real.mp4"; real.write_bytes(b"x")
    existing, missing = rubric_store.validate_videos([str(real), str(tmp_path / "no.mp4")])
    assert existing == [str(real)]
    assert missing == [str(tmp_path / "no.mp4")]
    # a single path (not a list) is accepted too
    existing, _ = rubric_store.validate_videos(str(real))
    assert existing == [str(real)]
