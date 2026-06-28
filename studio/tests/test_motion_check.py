"""Offline tests for studio.review.motion_check — THE NO-DEAD-AIR GATE.

The cv2 frame-diff pass (which needs a real render) is injected as a synthetic
per-frame motion series, so the GATE LOGIC is unit-tested with no video: it must
flag (a) a trailing STATIC hold before a cut, (b) a silent gap between scenes, and
(c) a scene still animating at its cut — the golden-reference "looks alive on every
frame / no dead air" rule, automated. This replaces "trust the enum" with
"measure the motion."
"""

from __future__ import annotations

import json

from studio.review import motion_check as mc


def _series(samples):
    """[(t, diff), ...] -> the frame-diff series shape the gate consumes."""
    return [{"t": round(t, 4), "diff": float(d)} for t, d in samples]


def _steady(start, end, diff, step=0.25):
    out, t = [], start
    while t < end - 1e-9:
        out.append((round(t, 4), diff))
        t += step
    return out


# ======================================================================
# scene_windows — [start, cut] per scene from the VO grid
# ======================================================================
def test_scene_windows_from_grid_uses_next_start_as_cut():
    from studio import vo
    grid = vo.retimer_windows([5.0, 4.0, 6.0], old_grid=[6, 4, 10])
    vo_grid = {"grid": grid,
               "scenes": [{"scene_no": i + 1, "vo_dur": d} for i, d in enumerate([5.0, 4.0, 6.0])]}
    w = mc.scene_windows(vo_grid)
    assert [x["start"] for x in w] == [0.0, 5.0, 9.0]
    # each cut is the NEXT scene's start; the last cut is the composition total
    assert w[0]["cut"] == 5.0 and w[1]["cut"] == 9.0
    assert w[2]["cut"] == grid["total"]


# ======================================================================
# evaluate_scene_motion — the three dead-air flags
# ======================================================================
def test_flags_trailing_static_hold_before_cut():
    windows = [{"scene_no": 1, "start": 0.0, "cut": 5.0}]
    # alive until 4.0, then frozen (diff 0) for the last ~1s before the cut
    series = _series(_steady(0.0, 4.0, 5.0) + _steady(4.0, 5.0, 0.0))
    rep = mc.evaluate_scene_motion(windows, series)
    s1 = rep["scenes"][0]
    assert s1["trailing_static_sec"] > 0.5
    assert "trailing_static" in s1["flags"] and s1["status"] == "FLAG"


def test_passes_a_scene_that_stays_alive_through_its_cut():
    windows = [{"scene_no": 1, "start": 0.0, "cut": 5.0}]
    # low but never-zero motion all the way to the cut (grain-alive), settled (< cut eps)
    series = _series(_steady(0.0, 5.0, 3.0))
    rep = mc.evaluate_scene_motion(windows, series)
    s1 = rep["scenes"][0]
    assert s1["flags"] == [] and s1["status"] == "PASS"
    assert rep["any_flag"] is False


def test_flags_scene_still_animating_at_its_cut():
    windows = [{"scene_no": 1, "start": 0.0, "cut": 5.0}]
    # motion is still HOT right at the cut (a big move chopped by the cut)
    series = _series(_steady(0.0, 4.75, 4.0) + [(4.9, 14.0)])
    rep = mc.evaluate_scene_motion(windows, series, cut_motion_eps=8.0)
    s1 = rep["scenes"][0]
    assert s1["animating_at_cut"] is True
    assert "animating_at_cut" in s1["flags"]


def test_flags_a_silent_gap_between_scenes():
    # scene 1 ends (cut) at 4.5 but scene 2 doesn't start until 5.0 -> 0.5s of silence
    windows = [{"scene_no": 1, "start": 0.0, "cut": 4.5},
               {"scene_no": 2, "start": 5.0, "cut": 10.0}]
    series = _series(_steady(0.0, 10.0, 3.0))
    rep = mc.evaluate_scene_motion(windows, series)
    s1 = rep["scenes"][0]
    assert s1["gap_after"] > 0.4
    assert "silent_gap" in s1["flags"] and rep["any_flag"] is True


def test_contiguous_scenes_report_no_gap():
    windows = [{"scene_no": 1, "start": 0.0, "cut": 5.0},
               {"scene_no": 2, "start": 5.0, "cut": 10.0}]
    series = _series(_steady(0.0, 10.0, 3.0))
    rep = mc.evaluate_scene_motion(windows, series)
    assert all("silent_gap" not in s["flags"] for s in rep["scenes"])


# ======================================================================
# the PASS/FLAG table + the orchestrator (series injected, no cv2)
# ======================================================================
def test_format_table_marks_pass_and_flag_per_scene():
    rep = {"slug": "x", "any_flag": True, "global": {}, "scenes": [
        {"scene_no": 1, "motion_energy": 3.1, "trailing_static_sec": 0.0,
         "animating_at_cut": False, "gap_after": 0.0, "flags": [], "status": "PASS"},
        {"scene_no": 2, "motion_energy": 0.1, "trailing_static_sec": 1.2,
         "animating_at_cut": False, "gap_after": 0.0, "flags": ["trailing_static"],
         "status": "FLAG"},
    ]}
    table = mc.format_table(rep)
    assert "PASS" in table and "FLAG" in table
    assert "trailing_static" in table


def test_motion_check_orchestrates_with_injected_series(tmp_path, monkeypatch):
    from studio import config as cfg, vo
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")
    slug = "m1"
    pdir = cfg.PROJECTS_DIR / slug
    pdir.mkdir(parents=True)
    grid = vo.retimer_windows([5.0, 4.0], old_grid=[6, 4])
    vo_grid = {"grid": grid,
               "scenes": [{"scene_no": i + 1, "vo_dur": d} for i, d in enumerate([5.0, 4.0])],
               "total_duration_sec": grid["total"]}
    (pdir / "vo.grid.json").write_text(json.dumps(vo_grid), encoding="utf-8")

    # scene 1 freezes before its cut; scene 2 stays alive -> exactly one FLAG
    def fake_series(_video):
        return _series(_steady(0.0, 4.0, 5.0) + _steady(4.0, 5.0, 0.0)
                       + _steady(5.0, grid["total"], 3.0))

    rep = mc.motion_check(slug, video="/nonexistent.mp4", series_fn=fake_series,
                          reuse_global=False)
    assert rep["any_flag"] is True
    statuses = {s["scene_no"]: s["status"] for s in rep["scenes"]}
    assert statuses[1] == "FLAG" and statuses[2] == "PASS"


# ======================================================================
# CLI: `python -m studio.run review --motion <slug>` FLAGS (non-zero) on dead air
# ======================================================================
def _flagged(slug, **_):
    return {"slug": slug, "any_flag": True, "video": None, "global": {}, "scenes": [
        {"scene_no": 1, "status": "FLAG", "flags": ["trailing_static"],
         "motion_energy": 0.1, "trailing_static_sec": 1.0, "animating_at_cut": False,
         "gap_after": 0.0, "start": 0.0, "cut": 5.0}]}


def _clean(slug, **_):
    return {"slug": slug, "any_flag": False, "video": None, "global": {}, "scenes": [
        {"scene_no": 1, "status": "PASS", "flags": [], "motion_energy": 3.0,
         "trailing_static_sec": 0.0, "animating_at_cut": False, "gap_after": 0.0,
         "start": 0.0, "cut": 5.0}]}


def test_cli_review_motion_returns_nonzero_when_flagged(monkeypatch, capsys):
    from studio import run
    monkeypatch.setattr(mc, "motion_check", _flagged)
    rc = run.main(["review", "--motion", "foo"])
    out = capsys.readouterr().out
    assert rc == 1, "the pipeline must FLAG (non-zero exit) on dead air"
    assert "FLAG" in out


def test_cli_review_motion_returns_zero_when_clean(monkeypatch, capsys):
    from studio import run
    monkeypatch.setattr(mc, "motion_check", _clean)
    rc = run.main(["review", "--motion", "foo"])
    assert rc == 0
    assert "no dead air" in capsys.readouterr().out
