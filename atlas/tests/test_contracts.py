"""The frozen contracts validate good artifacts and REJECT malformed ones.

Pure-unit: just the jsonschema-backed validator, no pipeline, no I/O.
"""
import contracts


def _valid_factcheck(verdict="pass"):
    return {"schema_version": "1.0", "verdict": verdict,
            "summary": {"verified": 2, "flagged": 0, "unverifiable": 0},
            "claims": [{"claim_id": "c1", "status": "verified"}]}


def _valid_research_brief():
    return {"schema_version": "1.0", "topic": "espresso",
            "overview": "x",
            "verified_facts": [{"claim": "y", "confidence": "high"}],
            "sources": [{"url": "https://e.org"}]}


def test_every_contract_is_a_loadable_schema():
    # Each known contract compiles as a Draft 2020-12 schema (check_schema runs on load).
    for name in contracts.known_contracts():
        ok, errors = contracts.validate(name, {})  # empty obj: exercises the validator
        assert isinstance(ok, bool)
        assert isinstance(errors, list)


def test_valid_artifacts_pass():
    ok, errors = contracts.validate("factcheck_report", _valid_factcheck())
    assert ok, errors
    ok, errors = contracts.validate("research_brief", _valid_research_brief())
    assert ok, errors


def test_research_brief_accepts_a_thematic_anchor():
    brief = _valid_research_brief()
    brief["thematic_anchor"] = {
        "thesis_statement": "We are about to mine the deep ocean before we have even seen it.",
        "supporting_pillar_1": "Under 0.001% of the deep seafloor has been directly observed.",
        "supporting_pillar_2": "31 ISA exploration licenses already cover the deep sea.",
        "counter_intuitive_angle": "We assume mapping precedes extraction; here it's reversed.",
        "emotional_payload": "the quiet dread of a frontier sold off before it's been seen",
        "confidence": "high",
    }
    ok, errors = contracts.validate("research_brief", brief)
    assert ok, errors


def test_research_brief_rejects_a_half_built_anchor():
    # If thematic_anchor is present it must carry all five legs of the argument.
    brief = _valid_research_brief()
    brief["thematic_anchor"] = {"thesis_statement": "An argument with no evidence."}
    ok, errors = contracts.validate("research_brief", brief)
    assert not ok


def test_research_brief_without_anchor_still_validates():
    # Backward-compatible: an older brief with no anchor remains valid.
    ok, errors = contracts.validate("research_brief", _valid_research_brief())
    assert ok, errors


def test_missing_required_field_is_rejected():
    bad = _valid_factcheck()
    del bad["verdict"]
    ok, errors = contracts.validate("factcheck_report", bad)
    assert not ok
    assert any("verdict" in e for e in errors)


def test_bad_enum_value_is_rejected():
    bad = _valid_factcheck(verdict="maybe")
    ok, errors = contracts.validate("factcheck_report", bad)
    assert not ok
    assert any("maybe" in e for e in errors)


def test_style_guide_requires_signature_highlight():
    ok, _ = contracts.validate("style_guide",
                               {"schema_version": "1.0",
                                "palette": {"signature_highlight": "#FFD000"}})
    assert ok
    ok, errors = contracts.validate("style_guide",
                                    {"schema_version": "1.0", "palette": {}})
    assert not ok
    assert any("signature_highlight" in e for e in errors)


def test_contracts_are_additively_extensible():
    # Unknown extra keys are allowed (additionalProperties: true) — so the Art
    # Director / Composition Engineer can ADD fields under a bumped schema_version.
    # The Art Director (Iris) landed the real render-detail fields under 1.1:
    # `fps` + `textures` on the style guide, per-scene `effects` on the storyboard.
    art = {"schema_version": "1.1",
           "palette": {"signature_highlight": "#FFD000"},
           "fps": 30,                              # 1.1 field (real name)
           "textures": ["paper",                   # bare-string form still accepted
                        {"name": "grain", "params": {"intensity": 0.3}}]}  # {name,params} form
    ok, errors = contracts.validate("style_guide", art)
    assert ok, errors

    board = {"schema_version": "1.1",
             "scenes": [{"scene_no": 1, "layout": "split-screen",
                         "effects": ["push-in",                       # bare-string form
                                     {"name": "highlighter-FFD000"}]}]}  # {name,params} form
    ok, errors = contracts.validate("storyboard", board)
    assert ok, errors


def _valid_motion_mood_board():
    return {
        "schema_version": "1.0",
        "video_level": {
            "global_tempo": "brisk_and_urgent",
            "global_texture": "grain",
            "global_texture_justification": "grain evokes archival memory of a future "
                                            "that hasn't happened yet",
            "dominant_motion_philosophy": "motion is punctuation, not decoration",
        },
        "beat_map": [
            {"beat_id": "b-hook", "arc_phase": "hook", "primary_emotion": "curiosity",
             "intensity": 9, "pacing_profile": "rapid_staccato",
             "dominant_effect": "stutter-12fps", "secondary_effect": "none",
             "transition_in": "cut", "layout_family": "centered-statement",
             "scene_duration_target_sec": 8.0,
             "motion_parameter_overrides": {"stutter-12fps": {"apply_to": "entire_beat"}},
             "visual_mood_ref": "the cold open of Fincher's Social Network"},
            {"beat_id": "b-peak", "arc_phase": "peak", "primary_emotion": "awe",
             "intensity": 10, "pacing_profile": "slow_reveal",
             "dominant_effect": "highlighter-FFD000", "transition_in": "dip-to-black",
             "layout_family": "big-number", "scene_duration_target_sec": 15.0},
        ],
        "signature_beat_placement": {
            "beat_id": "b-peak", "target_element": "41%",
            "justification": "the thesis lands hardest on this number",
        },
    }


def test_motion_mood_board_valid_artifact_passes():
    ok, errors = contracts.validate("motion_mood_board", _valid_motion_mood_board())
    assert ok, errors


def test_motion_mood_board_rejects_off_vocabulary_effect():
    bad = _valid_motion_mood_board()
    bad["beat_map"][0]["dominant_effect"] = "explode"   # not a HyperFrames effect
    ok, errors = contracts.validate("motion_mood_board", bad)
    assert not ok
    assert any("explode" in e for e in errors)


def test_motion_mood_board_rejects_off_vocabulary_layout():
    bad = _valid_motion_mood_board()
    bad["beat_map"][1]["layout_family"] = "carousel"
    ok, errors = contracts.validate("motion_mood_board", bad)
    assert not ok


def test_motion_mood_board_beat_requires_core_fields():
    bad = _valid_motion_mood_board()
    del bad["beat_map"][0]["dominant_effect"]
    ok, errors = contracts.validate("motion_mood_board", bad)
    assert not ok
    assert any("dominant_effect" in e for e in errors)


def test_motion_mood_board_without_optional_blocks_still_validates():
    # Backward-compatible: a minimal mood board (just the envelope) is valid.
    ok, errors = contracts.validate("motion_mood_board", {"schema_version": "1.0"})
    assert ok, errors


def test_unknown_contract_name_raises():
    import pytest
    with pytest.raises(KeyError):
        contracts.validate("not_a_contract", {})
