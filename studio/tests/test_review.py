"""Offline tests for the in-loop multi-critic review (studio.review).

Every LLM seam (vision critics, the HTML editor) and every toolchain seam (gate,
re-render) is INJECTED with a fake, so the whole pipeline — evidence → 7 critics →
synthesize → auto-apply+guardrail+re-render → persist — is exercised end-to-end with no
network, no Chrome, and no ffmpeg. The deterministic cores (frame-sampling plan, technical
scan, dedupe/rank/conflict, scene-block splicing, state round-trip) are tested directly.
"""

from __future__ import annotations

import json

from studio.review import (apply as ap, critics as cr, evidence as ev,
                           state as st, synthesize as syn, vision as vis)


# ======================================================================
# evidence.sample_timestamps — midpoint per scene + a transition per cut
# ======================================================================
def test_sample_timestamps_midpoints_and_transitions():
    windows = [{"scene_no": 1, "start": 0.0, "cut": 4.0},
               {"scene_no": 2, "start": 4.0, "cut": 10.0}]
    plan = ev.sample_timestamps(windows)
    mids = [p for p in plan if p["kind"] == "mid"]
    trans = [p for p in plan if p["kind"] == "transition"]
    assert [p["t"] for p in mids] == [2.0, 7.0]          # scene midpoints
    assert len(trans) == 1 and trans[0]["to_scene"] == 2  # one seam, 1->2
    assert plan == sorted(plan, key=lambda p: p["t"])     # time-ordered


# ======================================================================
# critics.technical_scan — deterministic determinism/seekability grep
# ======================================================================
def test_technical_scan_flags_nondeterminism_and_timeline():
    html = ('<script>const x = Math.random(); const t = Date.now();'
            'window.__timelines["v"] = tl;</script>')
    scan = cr.technical_scan(html)
    assert scan["nondeterminism"]["math_random"] == 1
    assert scan["nondeterminism"]["date_now"] == 1
    assert scan["registers_timeline"] is True


def test_technical_scan_clean_html():
    scan = cr.technical_scan('<div>no rng here</div>')
    assert scan["nondeterminism"] == {}
    assert scan["registers_timeline"] is False


# ======================================================================
# critics.run_critics — 7 lenses, injected vision_fn, parsed + tagged
# ======================================================================
def _evidence_stub():
    return {"slug": "t", "video": "/x.mp4", "reference": "dark-truth-social",
            "render_duration_sec": 41.0, "global": {"motion_energy": 3.0},
            "loudness": {"integrated_lufs": -22.0, "true_peak_dbtp": -2.0, "clipping": False},
            "polish_vs_reference": {"rate": 0.33},
            "index_html": '<div class="scene clip">x</div>',
            "script": {"hook": "h", "cta": "c", "scenes": []},
            "frames": [{"kind": "mid", "scene_no": 1, "t": 2.0, "path": None}],
            "scenes": [{"scene_no": 1, "start": 0.0, "cut": 4.0, "duration_sec": 4.0,
                        "on_screen_text": "T", "narration": "n",
                        "motion": {"motion_energy": 0.1, "trailing_static_sec": 2.0,
                                   "animating_at_cut": False, "flags": ["trailing_static"],
                                   "status": "FLAG"}}]}


def test_run_critics_runs_all_lenses_and_tags_findings():
    seen = []

    def fake_vision(system, user, images):
        seen.append(system[:20])
        return ('[{"severity":"Major","scene":1,"issue":"frozen tail",'
                '"evidence":"tail_static 2s","fix":"extend motion","effort":"S"}]')

    findings = cr.run_critics(_evidence_stub(), vision_fn=fake_vision)
    assert len(seen) == len(cr.LENSES) == 7          # all seven lenses called
    assert len(findings) == 7                         # each returned one finding
    assert {f["lens"] for f in findings} == {l["key"] for l in cr.LENSES}
    assert all(f["severity"] == "Major" for f in findings)


def test_run_critics_degrades_when_a_lens_raises():
    def boom(system, user, images):
        if "MOTION" in system:
            raise RuntimeError("lens down")
        return "[]"

    findings = cr.run_critics(_evidence_stub(), vision_fn=boom)
    assert findings == []   # no crash; the raising lens yields nothing


def test_normalize_tolerates_wrapped_and_junk():
    assert cr._normalize({"findings": [{"issue": "x", "severity": "blocker"}]}, "motion") \
        == [{"lens": "motion", "severity": "Blocker", "scene": None, "issue": "x",
             "evidence": "", "fix": "", "effort": "M"}]
    assert cr._normalize("not json", "motion") == []
    assert cr._normalize([{"severity": "Major"}], "motion") == []  # no issue text → dropped


# ======================================================================
# synthesize — dedupe across lenses, rank, conflicts
# ======================================================================
def test_synthesize_merges_cross_lens_dupes_and_ranks():
    findings = [
        {"lens": "motion", "severity": "Major", "scene": 4, "issue": "text card frozen too long",
         "evidence": "tail 3s", "fix": "add motion", "effort": "S"},
        {"lens": "legibility", "severity": "Minor", "scene": 4, "issue": "frozen card text long",
         "evidence": "static", "fix": "animate the card", "effort": "S"},
        {"lens": "technical", "severity": "Blocker", "scene": None, "issue": "Math.random used",
         "evidence": "1 hit", "fix": "seed it", "effort": "M"},
    ]
    out = syn.synthesize(findings)
    fixes = out["fixes"]
    # the two scene-4 findings merge into one, keeping the higher (Major) severity + both lenses
    scene4 = [f for f in fixes if f["scene"] == 4]
    assert len(scene4) == 1
    assert scene4[0]["severity"] == "Major"
    assert set(scene4[0]["lenses"]) == {"motion", "legibility"}
    # Blocker ranks first, gets R01
    assert fixes[0]["severity"] == "Blocker" and fixes[0]["id"] == "R01"
    assert out["counts"] == {"Blocker": 1, "Major": 1}


def test_synthesize_flags_opposing_fixes_on_same_scene():
    findings = [
        {"lens": "motion", "severity": "Major", "scene": 2, "issue": "scene drags",
         "evidence": "", "fix": "trim the scene to be shorter", "effort": "S"},
        {"lens": "narrative", "severity": "Major", "scene": 2, "issue": "needs room to breathe",
         "evidence": "", "fix": "extend the scene, hold longer", "effort": "S"},
    ]
    out = syn.synthesize(findings)
    assert out["conflicts"], "opposite extend/trim on the same scene must be flagged"
    assert out["conflicts"][0]["scene"] == 2


# ======================================================================
# apply.scene_block_spans — splice the Nth scene block correctly
# ======================================================================
def test_scene_block_spans_handles_nested_divs():
    html = ('<body><div class="scene clip" id="s1"><div class="inner">a</div></div>'
            '<div class="scene clip" id="s2">b</div></body>')
    spans = ap.scene_block_spans(html)
    assert len(spans) == 2
    assert html[spans[0][0]:spans[0][1]].endswith("</div></div>")  # closes the outer div
    assert 'id="s2"' in html[spans[1][0]:spans[1][1]]
    assert ap.nth_scene_block(html, 2) == spans[1]
    assert ap.nth_scene_block(html, 3) is None


def test_scene_block_spans_handles_section_tag_with_children():
    # the real composition uses <section class="scene clip"> with nested <div> children
    html = ('<main><section id="s1" class="scene clip" data-start="0">'
            '<div class="scene-content"><div class="lead">A</div></div></section>'
            '<section id="s2" class="scene clip"><div>B</div></section></main>')
    spans = ap.scene_block_spans(html)
    assert len(spans) == 2
    b1 = html[spans[0][0]:spans[0][1]]
    assert b1.startswith('<section id="s1"') and b1.endswith("</section>")
    assert "<div class=\"lead\">A</div>" in b1
    assert 'id="s2"' in html[spans[1][0]:spans[1][1]]


# ======================================================================
# apply.select_auto_fixes — Blocker/Major with a scene, minus conflicts
# ======================================================================
def test_select_auto_fixes_excludes_conflicted_and_sceneless():
    synthesis = {
        "fixes": [
            {"id": "R01", "severity": "Blocker", "scene": 1, "fix": "a"},
            {"id": "R02", "severity": "Major", "scene": 2, "fix": "trim"},
            {"id": "R03", "severity": "Major", "scene": 2, "fix": "extend"},
            {"id": "R04", "severity": "Minor", "scene": 3, "fix": "polish"},
            {"id": "R05", "severity": "Blocker", "scene": None, "fix": "global"},
        ],
        "conflicts": [{"scene": 2, "between": ["R02", "R03"]}],
    }
    auto, escalate = ap.select_auto_fixes(synthesis)
    assert [f["id"] for f in auto] == ["R01"]                 # only the clean Blocker
    assert {f["id"] for f in escalate} == {"R02", "R03", "R04", "R05"}


# ======================================================================
# apply.apply_fixes — full path with injected editor / gate / render / re-measure
# ======================================================================
def _project_with_html(cfg, tmp_path, slug, html):
    cfg_dir = tmp_path / "projects"
    cfg.PROJECTS_DIR = cfg_dir  # monkeypatched by caller normally; set directly here
    pdir = cfg_dir / slug
    pdir.mkdir(parents=True)
    (pdir / "index.html").write_text(html, encoding="utf-8")
    return pdir


def test_apply_fixes_edits_gates_rerenders_and_reports_before_after(tmp_path, monkeypatch):
    from studio import config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")
    slug = "demo"
    pdir = cfg.PROJECTS_DIR / slug
    pdir.mkdir(parents=True)
    html = '<div class="scene clip" id="s1">OLD</div><div class="scene clip" id="s2">B</div>'
    (pdir / "index.html").write_text(html, encoding="utf-8")

    synthesis = {"fixes": [{"id": "R01", "severity": "Major", "scene": 1,
                            "issue": "frozen", "evidence": "tail 2s",
                            "fix": "add motion", "effort": "S"}],
                 "conflicts": []}
    evidence_before = {"scenes": [{"scene_no": 1, "duration_sec": 4.0,
                                   "motion": {"motion_energy": 0.1, "trailing_static_sec": 2.0,
                                              "animating_at_cut": False, "status": "FLAG"}}]}

    def fake_editor(block, fix, scene):
        return block.replace("OLD", "NEW-animated")

    def fake_gate(p):
        return {"ok": True}

    def fake_render(p):
        return {"ok": True, "video": str(p / "renders" / "draft.mp4")}

    def fake_evidence_fn(s, video=None):
        return {"scenes": [{"scene_no": 1, "duration_sec": 4.0,
                            "motion": {"motion_energy": 5.0, "trailing_static_sec": 0.0,
                                       "animating_at_cut": False, "status": "PASS"}}]}

    res = ap.apply_fixes(slug, synthesis, evidence_before, editor_fn=fake_editor,
                         gate_fn=fake_gate, render_fn=fake_render, evidence_fn=fake_evidence_fn)
    assert [a["id"] for a in res["applied"]] == ["R01"]
    assert "NEW-animated" in (pdir / "index.html").read_text()
    assert res["reverted"] is False
    ba = res["before_after"]["1"]
    assert ba["before"]["status"] == "FLAG" and ba["after"]["status"] == "PASS"


def test_apply_fixes_reverts_when_gate_regresses(tmp_path, monkeypatch):
    from studio import config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")
    slug = "demo2"
    pdir = cfg.PROJECTS_DIR / slug
    pdir.mkdir(parents=True)
    html = '<div class="scene clip" id="s1">OLD</div>'
    (pdir / "index.html").write_text(html, encoding="utf-8")
    synthesis = {"fixes": [{"id": "R01", "severity": "Blocker", "scene": 1,
                            "issue": "x", "evidence": "", "fix": "f", "effort": "S"}],
                 "conflicts": []}

    # gate is called twice: baseline (passes) then post-edit (fails) → a true regression
    calls = {"n": 0}

    def regressing_gate(p):
        calls["n"] += 1
        return {"ok": True} if calls["n"] == 1 else {"ok": False}

    res = ap.apply_fixes(slug, synthesis, {"scenes": []},
                         editor_fn=lambda b, f, s: b.replace("OLD", "BROKEN"),
                         gate_fn=regressing_gate, render_fn=lambda p: {"ok": True})
    assert res["reverted"] is True
    assert res["applied"] == []
    assert (pdir / "index.html").read_text() == html   # pristine restored


def test_apply_fixes_does_not_revert_when_baseline_already_gate_red(tmp_path, monkeypatch):
    # a draft that was ALREADY gate-red must not block edits on a failure they didn't add
    from studio import config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")
    slug = "demo3"
    pdir = cfg.PROJECTS_DIR / slug
    pdir.mkdir(parents=True)
    (pdir / "index.html").write_text('<section class="scene clip" id="s1">OLD</section>',
                                     encoding="utf-8")
    synthesis = {"fixes": [{"id": "R01", "severity": "Major", "scene": 1, "issue": "x",
                            "evidence": "", "fix": "f", "effort": "S"}], "conflicts": []}
    res = ap.apply_fixes(slug, synthesis, {"scenes": []},
                         editor_fn=lambda b, f, s: b.replace("OLD", "NEW"),
                         gate_fn=lambda p: {"ok": False},   # red both before and after
                         render_fn=lambda p: {"ok": True}, do_render=False)
    assert res["reverted"] is False
    assert [a["id"] for a in res["applied"]] == ["R01"]
    assert "NEW" in (pdir / "index.html").read_text()


# ======================================================================
# state — round-trip + audit append
# ======================================================================
def test_state_records_review_and_round_trips(tmp_path, monkeypatch):
    from studio import config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")
    slug = "s"
    (cfg.PROJECTS_DIR / slug).mkdir(parents=True)
    evidence = {"video": "/v.mp4", "reference": "ref", "render_duration_sec": 41.0,
                "loudness": {"integrated_lufs": -22.0},
                "polish_vs_reference": {"rate": 0.5}, "scenes": []}
    synthesis = {"fixes": [{"id": "R01", "severity": "Major"}], "conflicts": [],
                 "counts": {"Major": 1}}
    apply_result = {"applied": [{"id": "R01", "scene": 1}], "escalated": [],
                    "before_after": {}}
    entry = st.record_review(slug, ts=123.0, evidence=evidence, synthesis=synthesis,
                             apply_result=apply_result, mode="auto")
    assert entry["polish_rate"] == 0.5 and entry["counts"] == {"Major": 1}
    reloaded = st.load_state(slug)
    assert len(reloaded["reviews"]) == 1
    assert reloaded["reviews"][0]["ts"] == 123.0
    # a second review appends, not overwrites
    st.record_review(slug, ts=456.0, evidence=evidence, synthesis=synthesis,
                     apply_result=apply_result, mode="stop")
    assert len(st.load_state(slug)["reviews"]) == 2


def test_load_state_fresh_when_missing(tmp_path, monkeypatch):
    from studio import config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")
    (cfg.PROJECTS_DIR / "none").mkdir(parents=True)
    assert st.load_state("none") == {"slug": "none", "reviews": []}


# ======================================================================
# vision.extract_json — tolerate fences + preamble
# ======================================================================
def test_extract_json_handles_fences_and_prose():
    assert vis.extract_json('```json\n[{"a":1}]\n```') == [{"a": 1}]
    assert vis.extract_json('here you go: {"a": 2} done') == {"a": 2}
    assert vis.extract_json("nothing here") is None


# ======================================================================
# orchestrator — end-to-end with all seams faked (no network/toolchain)
# ======================================================================
def test_review_orchestrator_end_to_end_stop_mode(tmp_path, monkeypatch):
    from studio import config as cfg
    import studio.review as review_mod
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")
    slug = "e2e"
    pdir = cfg.PROJECTS_DIR / slug
    pdir.mkdir(parents=True)
    vo_grid = {"grid": {"NS": [0.0, 4.0], "total": 10.0},
               "scenes": [{"scene_no": 1, "narration": "n1", "on_screen_text": "A"},
                          {"scene_no": 2, "narration": "n2", "on_screen_text": "B"}]}
    (pdir / "vo.grid.json").write_text(json.dumps(vo_grid), encoding="utf-8")
    (pdir / "index.html").write_text('<div class="scene clip">a</div>', encoding="utf-8")
    (pdir / "script.json").write_text(json.dumps({"hook": "h", "cta": "c", "scenes": []}),
                                      encoding="utf-8")

    def fake_vision(system, user, images):
        return ('[{"severity":"Major","scene":1,"issue":"frozen","evidence":"e",'
                '"fix":"add motion","effort":"S"}]')

    # stop mode: no apply, no render; everything escalates
    rep = review_mod.review(slug, mode="stop", video=None, vision_fn=fake_vision, polish=True)
    assert rep["mode"] == "stop"
    assert rep["apply"] is None
    assert rep["synthesis"]["fixes"], "critics produced findings"
    # persisted
    saved = st.load_state(slug)
    assert len(saved["reviews"]) == 1
    out = review_mod.format_report(rep)
    assert "MULTI-CRITIC REVIEW" in out and slug in out
