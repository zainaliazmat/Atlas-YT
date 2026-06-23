"""Offline, deterministic tests for the band-calibration PROPOSER (step 1).

These never decode real media: `measure_all` / `list_references` / Vera are
injected so the proposal LOGIC is exercised deterministically. The one hard
invariant under test is the privilege asymmetry: calibrate proposes, it never
writes rubric.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import rubric
from eval import calibrate


# --- band math --------------------------------------------------------------

def test_band_from_values_is_shared_range_padded():
    b = calibrate._band_from_values([10.0, 12.0, 14.0])
    assert b["raw_min"] == 10.0 and b["raw_max"] == 14.0
    assert b["min"] < 10.0 and b["max"] > 14.0          # lightly padded outward
    assert b["value"] == 12.0 and b["n"] == 3


def test_band_from_values_single_value_gets_soft_pad():
    b = calibrate._band_from_values([-14.0])
    assert b["min"] < -14.0 < b["max"]                   # degenerate span still padded


def test_proposed_for_comparator_shapes():
    band = {"min": 1.0, "max": 5.0, "value": 3.0, "raw_min": 1.2, "raw_max": 4.8}
    rng = calibrate._proposed_for_comparator("range", band)
    assert rng["min"] == 1.0 and rng["max"] == 5.0
    lte = calibrate._proposed_for_comparator("lte", band)
    assert "max" in lte and "min" not in lte
    gte = calibrate._proposed_for_comparator("gte", band)
    assert "min" in gte and "max" not in gte


# --- the proposal, with injected measurements -------------------------------

_FAKE = {
    "short_explainer.mp4": {
        "render:final_loudness": -14.2, "render:final_peak": -1.4,
        "audiomix:integrated_loudness": -14.2, "audiomix:true_peak": -1.4,
        "compose:motion_energy": 6.0, "render:final_runtime": 72.0, "_duration_sec": 72.0,
    },
    "long_a.mp4": {
        "render:final_loudness": -13.5, "render:final_peak": -1.1,
        "audiomix:integrated_loudness": -13.5, "audiomix:true_peak": -1.1,
        "compose:motion_energy": 9.0, "render:final_runtime": 350.0, "_duration_sec": 350.0,
    },
    "over_long.mp4": {
        "render:final_loudness": -13.9, "render:final_peak": -1.0,
        "audiomix:integrated_loudness": -13.9, "audiomix:true_peak": -1.0,
        "compose:motion_energy": 12.0, "render:final_runtime": 2100.0, "_duration_sec": 2100.0,
    },
}


# our own short-form output, lower-motion than the references (design-space gap)
_OWN = {
    "proj_a": {"compose:motion_energy": 1.7, "render:final_loudness": -14.0,
               "render:final_peak": -1.5, "render:final_runtime": 70.0},
}


def _patch(monkeypatch, tmp_path):
    monkeypatch.setattr(calibrate, "list_references",
                        lambda *a, **k: [Path(n) for n in _FAKE])
    monkeypatch.setattr(calibrate, "measure_all", lambda paths, use_cache=True: dict(_FAKE))
    monkeypatch.setattr(calibrate, "measure_own_output", lambda use_cache=True: dict(_OWN))
    monkeypatch.setattr(calibrate, "_vera_crosscheck", lambda: {})
    monkeypatch.setattr(calibrate, "PROPOSAL_FILE", tmp_path / "rubric.proposal.json")


def test_build_proposal_structure(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    p = calibrate.build_proposal(use_cache=False)
    assert p["n_references"] == 3
    by_id = {b["band_id"]: b for b in p["bands"]}

    # loudness/peak are DELIVERY STANDARDS — measured for evidence, kept (not learned)
    loud = by_id["render:final_loudness"]
    assert loud["source"] == "delivery-standard" and loud["confidence"] == "keep-current"
    assert loud["proposed"]["recommendation"] == "KEEP current standard"
    # the proposed loudness equals the current standard (never widened from noise)
    assert loud["proposed"].get("min") == loud["current"].get("min")

    # motion energy is an AESTHETIC RATE — proposed, low confidence (design-space)
    motion = by_id["compose:motion_energy"]
    assert motion["source"] == "aesthetic-rate" and motion["confidence"] == "low"
    # our own output (1.7) falls below the reference band → rationale flags divergence
    assert "OUTSIDE" in motion["rationale"]

    # own-output baseline is carried for the comparison
    assert p["own_output_baseline"]["proj_a"]["compose:motion_energy"] == 1.7

    # length bands are split into short/long profiles, never collapsed
    rt = by_id["render:final_runtime"]
    assert rt["source"] == "format-profile"
    assert rt["proposed"]["short"] == [60, 90] and rt["proposed"]["long"] == [300, 480]

    # format split classifies the references by duration
    split = p["reference_format_split"]
    assert split["short_<=120s"] == ["short_explainer.mp4"]
    assert split["long_120-480s"] == ["long_a.mp4"]
    assert split["over_long_>480s"] == ["over_long.mp4"]

    # structural/editorial bands are explicitly NOT calibrated
    assert "script:hook_strength" in p["not_calibrated_needs_ceo_interview"]
    assert "narration:speech_cadence" in p["not_calibrated_needs_ceo_interview"]


def test_write_proposal_does_not_touch_rubric(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    rubric_path = Path(rubric.__file__).parent / "rubric.json"
    before = rubric_path.read_bytes()
    p = calibrate.build_proposal(use_cache=False)
    out = calibrate.write_proposal(p, path=tmp_path / "rubric.proposal.json")
    # the proposal lands in its own file...
    assert out.is_file() and out.name == "rubric.proposal.json"
    json.loads(out.read_text())                          # valid JSON
    # ...and rubric.json is byte-for-byte untouched (privilege asymmetry).
    assert rubric_path.read_bytes() == before


def test_rubric_has_no_write_path():
    # structural guarantee the proposer relies on: the rubric package exposes no
    # save/write/dump, so there is simply no code path to mutate the standard.
    for forbidden in ("save", "write", "dump", "set_band", "update"):
        assert not hasattr(rubric, forbidden), f"rubric unexpectedly exposes {forbidden}()"
