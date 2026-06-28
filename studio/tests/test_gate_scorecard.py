# studio/tests/test_gate_scorecard.py
from studio.gate.types import load_thresholds, DimResult, ComplianceResult
from studio.gate import scorecard

T = load_thresholds()


def test_compliance_failure_blocks_even_if_dims_pass():
    dims = [DimResult("motion_variety", 5.0, 3.0, True, [], {})]
    comp = [ComplianceResult("determinism", False, "Math.random present")]
    sc = scorecard.build_scorecard(dims, comp, T)
    assert sc["verdict"] == "BLOCKED"
    assert any("random" in r.lower() for r in sc["reasons"])


def test_dim_below_floor_blocks_with_reason():
    dims = [DimResult("motion_variety", 1.0, 3.0, False, ["8/9 scenes share the 'underline' beat → templated"], {}),
            DimResult("audio", 5.0, 3.0, True, [], {})]
    comp = [ComplianceResult("determinism", True, "")]
    sc = scorecard.build_scorecard(dims, comp, T)
    assert sc["verdict"] == "BLOCKED"
    assert any("templated" in r for r in sc["reasons"])


def test_all_pass_is_pass():
    dims = [DimResult("motion_variety", 5.0, 3.0, True, [], {}),
            DimResult("polish_vs_reference", None, 2.5, None, ["inconclusive"], {})]  # None = non-blocking
    comp = [ComplianceResult("determinism", True, ""), ComplianceResult("overflow", None, "unavailable")]
    sc = scorecard.build_scorecard(dims, comp, T)
    assert sc["verdict"] == "PASS"


def test_unavailable_required_compliance_blocks_when_threshold_flips_it_on():
    # warn-only by default (overflow_blocks=false) → an unavailable overflow check PASSes
    # (see test_all_pass_is_pass). But if a CEO flips overflow_blocks=true in thresholds,
    # the same unavailable check must BLOCK (not silently pass) and surface the reason.
    t_strict = {"dimensions": dict(T["dimensions"]),
                "compliance": {"overflow_blocks": True, "likeness_blocks": False}}
    dims = [DimResult("motion_variety", 5.0, 3.0, True, [], {})]
    comp = [ComplianceResult("determinism", True, ""),
            ComplianceResult("overflow", None, "hyperframes inspect unavailable")]
    sc = scorecard.build_scorecard(dims, comp, t_strict)
    assert sc["verdict"] == "BLOCKED"
    assert any("overflow" in r.lower() and "required" in r.lower() for r in sc["reasons"])

    # and with the SHIPPED warn-only thresholds the identical input still PASSes
    t_warn = {"dimensions": dict(T["dimensions"]),
              "compliance": {"overflow_blocks": False, "likeness_blocks": False}}
    assert scorecard.build_scorecard(dims, comp, t_warn)["verdict"] == "PASS"


def test_score_with_explicit_paths_uses_injected_evidence():
    # the reference path: no studio project, evidence injected directly
    ev = {"index_html": "<section id='s1' class='scene clip'><div class='lead'>A</div></section>",
          "global": {"motion_energy": 6.0, "cut_rhythm": 4.0},
          "motion": {"any_flag": False, "scenes": [{"scene_no": 1, "flags": []}]},
          "loudness": {"integrated_lufs": -14.0, "true_peak_dbtp": -2.0, "clipping": False},
          "polish_vs_reference": {"rate": None, "n": 0},
          "script": {"scenes": []}, "frames": []}
    sc = scorecard.score(evidence=ev, pdir=None, thresholds=T,
                         inspect_fn=lambda p: None, polish=False)
    assert sc["verdict"] in ("PASS", "BLOCKED") and "dimensions" in sc
