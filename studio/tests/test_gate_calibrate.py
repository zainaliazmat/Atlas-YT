"""Offline calibration proof: the gate must discriminate flat / templated drafts
(LOW_EV → BLOCKED) from hand-crafted references (HIGH_EV → PASS) using only
the deterministic structural dimensions — no render, cv2, or LLM required."""
from studio.gate.types import load_thresholds
from studio.gate import scorecard

T = load_thresholds()

# the flat draft: one repeated beat, a dropped attributed quote, dead air, quiet audio
LOW_EV = {
    "index_html": "".join(
        f"<section id='s{i}' class='scene clip'><div class='lead'>X</div>"
        f"<div class='fx'></div></section><script>makeOutlineDraw({{mount:'#s{i} .fx'}});</script>"
        for i in range(1, 10)),
    "script": {"scenes": [
        {"scene_no": 5, "on_screen_text": '"Behavioral cocaine." — Aza Raskin', "claims": []}]},
    "scenes": [{"scene_no": i, "on_screen_text": ""} for i in range(1, 10)],
    "global": {"motion_energy": 0.9, "cut_rhythm": 11.0},
    "motion": {"any_flag": True, "scenes": [
        {"scene_no": n, "flags": (["trailing_static"] if n in (3, 6, 8) else [])} for n in range(1, 10)]},
    "loudness": {"integrated_lufs": -22.0, "true_peak_dbtp": -3.0, "clipping": False},
    "polish_vs_reference": {"rate": 0.0, "n": 5}, "frames": [],
}

# the reference: distinct beats per scene, content present, alive, on-target audio
HIGH_EV = {
    "index_html": "".join(
        f"<section id='s{i}' class='scene clip'><div class='lead'>L{i}</div>{extra}</section>"
        for i, extra in enumerate(
            ["<span class='count-host'></span>", "<div class='fx'>portrait</div>",
             "<div class='cards'></div>", "<div class='fx'>phone</div>",
             "<div class='cards'>quote</div>", "<div class='shatter'></div>",
             "<div class='strike'></div>", "<div class='checklist'></div>",
             "<div class='signature'></div>"], start=1))
    + "<script>countUp();makeOrbitCluster();quoteCards();</script>",
    "script": {"scenes": []},
    "scenes": [{"scene_no": i, "on_screen_text": ""} for i in range(1, 10)],
    "global": {"motion_energy": 5.5, "cut_rhythm": 4.0},
    "motion": {"any_flag": False, "scenes": [{"scene_no": n, "flags": []} for n in range(1, 10)]},
    "loudness": {"integrated_lufs": -14.0, "true_peak_dbtp": -2.0, "clipping": False},
    "polish_vs_reference": {"rate": 1.0, "n": 5}, "frames": [],
}


def test_gate_discriminates_low_from_high():
    low = scorecard.score(evidence=LOW_EV, pdir=None, thresholds=T,
                          inspect_fn=lambda p: None, polish=False)
    high = scorecard.score(evidence=HIGH_EV, pdir=None, thresholds=T,
                           inspect_fn=lambda p: None, polish=False)
    assert low["verdict"] == "BLOCKED", low["reasons"]
    assert high["verdict"] == "PASS", high["reasons"]
    # and the block reasons are specific/actionable
    blob = " ".join(low["reasons"]).lower()
    assert "templated" in blob          # motion_variety
    assert "quote" in blob              # content_fidelity
    assert "dead air" in blob           # dead_air
