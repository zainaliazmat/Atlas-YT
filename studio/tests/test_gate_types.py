from studio.gate.types import DimResult, ComplianceResult, load_thresholds, band_score


def test_band_score_maps_and_clamps():
    assert band_score(0, 0, 10) == 0.0
    assert band_score(10, 0, 10) == 5.0
    assert band_score(5, 0, 10) == 2.5
    assert band_score(-3, 0, 10) == 0.0      # clamp low
    assert band_score(99, 0, 10) == 5.0      # clamp high


def test_dimresult_passed_is_caller_set():
    d = DimResult(name="motion_energy", score=2.0, floor=3.0, passed=False,
                  diagnostics=["too static"], detail={})
    assert d.name == "motion_energy" and d.passed is False


def test_thresholds_load_has_every_dimension_floor():
    t = load_thresholds()
    for dim in ("motion_energy", "motion_variety", "content_fidelity",
                "dead_air", "pacing", "audio", "polish_vs_reference"):
        assert dim in t["dimensions"], f"missing threshold block for {dim}"
        assert "floor" in t["dimensions"][dim]
