"""The frozen reference_rubric contract validates a real rubric and REJECTS malformed ones.

Pure-unit: just the jsonschema-backed validator, no engine, no I/O.
"""
import contracts


def _valid_rubric():
    return {
        "schema_version": "reference_rubric/1.0",
        "source_videos": ["ref1.mp4", "ref2.mp4"],
        "targets": {
            "pacing": {
                "avg_shot_sec": {"value": 2.5, "band": [2.0, 3.0]},
                "cuts_per_min": {"value": 25.0, "band": [20.0, 30.0]},
                "shot_count": {"value": 10, "band": [8, 12]},
            },
            "motion": {"kinetic_score": {"value": 0.06, "band": [0.05, 0.07]}},
            "color": {
                "saturation": {"value": 0.55, "band": [0.5, 0.6]},
                "brightness": {"value": 0.5, "band": None},
                "palette_samples": [[{"hex": "#102030", "weight": 1.0}]],
            },
            "audio": {
                "integrated_lufs": {"value": -13.5, "band": [-14.0, -13.0]},
                "speech_ratio": {"value": 0.75, "band": [0.7, 0.8]},
            },
            "structure": {
                "duration_sec": {"value": 65.0, "band": [60.0, 70.0]},
                "fps": {"value": 30.0, "band": None},
            },
        },
        "judged": {"status": "pending",
                   "needs": ["visual_style", "typography_character"],
                   "frames": ["/frames/a_f0.jpg"]},
        "open_questions": [
            {"id": "pace", "sets": "pacing.avg_shot_sec", "plain": "snappy or roomy?"}],
        "ceo_prefs": {},
        "raw": [],
    }


def test_reference_rubric_is_a_known_loadable_contract():
    assert "reference_rubric" in contracts.known_contracts()
    ok, errors = contracts.validate("reference_rubric", {})  # exercises the validator
    assert isinstance(ok, bool) and isinstance(errors, list)


def test_valid_rubric_passes():
    ok, errors = contracts.validate("reference_rubric", _valid_rubric())
    assert ok, errors


def test_missing_schema_version_is_rejected():
    bad = _valid_rubric()
    del bad["schema_version"]
    ok, errors = contracts.validate("reference_rubric", bad)
    assert not ok
    assert any("schema_version" in e for e in errors)


def test_missing_targets_is_rejected():
    bad = _valid_rubric()
    del bad["targets"]
    ok, errors = contracts.validate("reference_rubric", bad)
    assert not ok
    assert any("targets" in e for e in errors)


def test_targets_without_pacing_is_rejected():
    bad = _valid_rubric()
    del bad["targets"]["pacing"]
    ok, errors = contracts.validate("reference_rubric", bad)
    assert not ok
    assert any("pacing" in e for e in errors)


def test_bad_judged_status_is_rejected():
    bad = _valid_rubric()
    bad["judged"]["status"] = "finalized"  # not in the enum
    ok, errors = contracts.validate("reference_rubric", bad)
    assert not ok
    assert any("finalized" in e for e in errors)


def test_open_question_missing_required_field_is_rejected():
    bad = _valid_rubric()
    del bad["open_questions"][0]["sets"]
    ok, errors = contracts.validate("reference_rubric", bad)
    assert not ok
    assert any("sets" in e for e in errors)


def test_contract_is_additively_extensible():
    # Unknown extra keys are allowed (additionalProperties: true) — so a future,
    # bumped schema_version can ADD fields (a new metric group, a richer style profile)
    # without breaking older readers.
    ext = _valid_rubric()
    ext["schema_version"] = "reference_rubric/1.1"
    ext["targets"]["texture"] = {"grain_score": {"value": 0.2, "band": [0.1, 0.3]}}
    ext["judged"]["assessment"] = {"visual_style": "editorial", "mood": "calm"}
    ext["coach_notes"] = ["a brand-new top-level field"]
    ok, errors = contracts.validate("reference_rubric", ext)
    assert ok, errors
