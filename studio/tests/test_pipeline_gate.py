# studio/tests/test_pipeline_gate.py
from studio import pipeline


def _fake_scorecard(verdict, reasons):
    return {"verdict": verdict, "reasons": reasons, "overall": 2.0,
            "dimensions": [], "compliance": []}


def test_blocked_scorecard_prevents_final_render(monkeypatch, tmp_path):
    # a BLOCKED gate must not ship, even with explicit final approval.
    calls = {"render_final": 0}

    def fake_render(pdir, final=False):
        if final:
            calls["render_final"] += 1
        return {"ok": True, "video": str(tmp_path / "out.mp4")}

    state = pipeline.produce(
        {"topic": "t"}, "gate-block-test",
        approve={"final"}, gates=True,
        run_config={"pack_id": "clean-explainer", "voice": "am_onyx", "render_budget_sec": 999},
        research_fn=lambda topic, angle: {"topic": "t", "verified_facts": [], "sources": []},
        script_fn=lambda b: {"scenes": [{"scene_no": 1, "narration": "n",
                                         "on_screen_text": "o", "claims": []}]},
        factcheck_fn=lambda s, b: {"verdict": "pass", "summary": {}, "claims": []},
        vo_fn=lambda s, d, **kw: {"total_duration_sec": 30, "grid": {"NS": [0], "total": 30}},
        compose_fn=lambda slug, pack_id: (pipeline.project_dir(slug) / "index.html").write_text(
            '<div id="root"><script>window.__timelines["x"]=1;</script></div>'),
        render_fn=fake_render,
        review_fn=lambda slug, mode: {"synthesis": {"fixes": [], "counts": {}}, "apply": {"applied": []}},
        motion_fn=lambda slug: {"any_flag": False},
        gate_fn=lambda slug: _fake_scorecard("BLOCKED", ["motion_variety 1/5: 8/9 scenes share 'underline'"]),
    )
    assert state["status"] == "blocked_at_gate"
    assert calls["render_final"] == 0
    assert any("templated" in r or "share" in r for r in state["gates"]["final"]["details"]["reasons"])


def test_passing_scorecard_allows_final(monkeypatch, tmp_path):
    def fake_render(pdir, final=False):
        return {"ok": True, "video": str(tmp_path / "out.mp4")}
    state = pipeline.produce(
        {"topic": "t"}, "gate-pass-test",
        approve={"final"}, gates=True,
        run_config={"pack_id": "clean-explainer", "voice": "am_onyx", "render_budget_sec": 999},
        research_fn=lambda topic, angle: {"topic": "t", "verified_facts": [], "sources": []},
        script_fn=lambda b: {"scenes": [{"scene_no": 1, "narration": "n",
                                         "on_screen_text": "o", "claims": []}]},
        factcheck_fn=lambda s, b: {"verdict": "pass", "summary": {}, "claims": []},
        vo_fn=lambda s, d, **kw: {"total_duration_sec": 30, "grid": {"NS": [0], "total": 30}},
        compose_fn=lambda slug, pack_id: (pipeline.project_dir(slug) / "index.html").write_text(
            '<div id="root"><script>window.__timelines["x"]=1;</script></div>'),
        render_fn=fake_render,
        review_fn=lambda slug, mode: {"synthesis": {"fixes": [], "counts": {}}, "apply": {"applied": []}},
        motion_fn=lambda slug: {"any_flag": False},
        gate_fn=lambda slug: _fake_scorecard("PASS", []),
    )
    assert state["status"] == "complete"
