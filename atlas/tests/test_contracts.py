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


def test_unknown_contract_name_raises():
    import pytest
    with pytest.raises(KeyError):
        contracts.validate("not_a_contract", {})
