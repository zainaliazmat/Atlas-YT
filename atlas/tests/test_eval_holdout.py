"""Offline tests for the held-out generalization guard. The engine re-run is
injected (rerun_fn), so no LLM/network is touched."""
from __future__ import annotations

from pathlib import Path

import rubric
from eval import holdout
from eval.types import Measurement


def _m(stage, prop, value):
    b = rubric.band(stage, prop)
    return Measurement(artifact=b["artifact"], stage=stage, owner=b["owner"], prop=prop,
                       value=value, kind=b["kind"], rolls_up_to=tuple(b["rolls_up_to"]),
                       unit=b.get("unit", ""))


def test_split_is_disjoint_and_resolves():
    opt = {p.name for p in holdout.optimize_projects()}
    held = {p.name for p in holdout.heldout_projects()}
    assert opt and held                      # both resolve to real dirs
    assert opt.isdisjoint(held)              # never optimize against held-out


def test_verify_unsupported_stage_short_circuits():
    out = holdout.verify_generalization("x", stage="audiomix")
    assert out["supported"] is False


def test_verify_generalizes_when_no_regression(monkeypatch, tmp_path):
    # one fake held-out project with an existing good script
    proj = tmp_path / "held"
    proj.mkdir()
    (proj / "research_brief.json").write_text("{}")
    (proj / "script.json").write_text("{}")

    # existing artifact measures: info_density passes (3.0), scene_count passes (11)
    import eval.holdout as H
    monkeypatch.setattr(H.text_an, "analyze",
                        lambda ctx: [_m("script", "info_density", 3.0),
                                     _m("script", "scene_count", 11)])
    # candidate (with addendum) keeps both passing -> generalizes
    rerun = lambda brief, add: [_m("script", "info_density", 2.5),
                                _m("script", "scene_count", 11)]
    out = holdout.verify_generalization("add", rerun_fn=rerun, projects=[proj])
    assert out["supported"] is True and out["generalizes"] is True
    assert out["regressions"] == []


def test_verify_flags_heldout_regression(monkeypatch, tmp_path):
    proj = tmp_path / "held"
    proj.mkdir()
    (proj / "research_brief.json").write_text("{}")
    (proj / "script.json").write_text("{}")

    import eval.holdout as H
    monkeypatch.setattr(H.text_an, "analyze",
                        lambda ctx: [_m("script", "info_density", 3.0),
                                     _m("script", "scene_count", 11)])
    # candidate breaks scene_count (50 is outside [8,14]) -> regression -> reject
    rerun = lambda brief, add: [_m("script", "info_density", 2.5),
                                _m("script", "scene_count", 50)]
    out = holdout.verify_generalization("add", rerun_fn=rerun, projects=[proj])
    assert out["generalizes"] is False
    assert any("scene_count" in r for r in out["regressions"])


def test_band_margin_filters_borderline_noise_flip(monkeypatch, tmp_path):
    # info_density band is [1.5, 4.0]; a held-out re-gen at 4.1 just-barely fails.
    proj = tmp_path / "held"
    proj.mkdir()
    (proj / "research_brief.json").write_text("{}")
    (proj / "script.json").write_text("{}")
    import eval.holdout as H
    monkeypatch.setattr(H.text_an, "analyze",
                        lambda ctx: [_m("script", "info_density", 3.0)])
    rerun = lambda brief, add: [_m("script", "info_density", 4.1)]   # 0.1 over the edge
    # strict (margin 0) -> counts as a regression
    strict = holdout.verify_generalization("add", rerun_fn=rerun, projects=[proj])
    assert strict["generalizes"] is False
    # with a noise margin (10% of the 2.5-wide band = 0.25) -> borderline flip ignored
    tol = holdout.verify_generalization("add", rerun_fn=rerun, projects=[proj], band_margin=0.10)
    assert tol["generalizes"] is True

    # a CLEAR held-out failure (way out of band) is still caught even with a margin
    rerun_bad = lambda brief, add: [_m("script", "info_density", 9.0)]
    bad = holdout.verify_generalization("add", rerun_fn=rerun_bad, projects=[proj], band_margin=0.10)
    assert bad["generalizes"] is False
