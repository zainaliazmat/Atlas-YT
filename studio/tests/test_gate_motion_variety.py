# studio/tests/test_gate_motion_variety.py
from studio.gate.types import load_thresholds
from studio.gate import parse, dimensions as D

T = load_thresholds()

# 3 scenes that all share ONE beat (the current-engine failure) vs 3 distinct.
SAMEY = """
<section id="s1" class="scene clip"><div class="lead">A</div><div class="fx"></div></section>
<section id="s2" class="scene clip"><div class="lead">B</div><div class="fx"></div></section>
<section id="s3" class="scene clip"><div class="lead">C</div><div class="fx"></div></section>
<script>
makeOutlineDraw({ mount: "#s1 .fx" }); makeOutlineDraw({ mount: "#s2 .fx" });
makeOutlineDraw({ mount: "#s3 .fx" });
</script>"""

VARIED = """
<section id="s1" class="scene clip"><div class="lead">A</div><span class="count-host"></span></section>
<section id="s2" class="scene clip"><div class="lead">B</div><div class="fx"></div></section>
<section id="s3" class="scene clip"><div class="lead">C</div><div class="cards"></div></section>
<script>
countUp({ mount: "#s1 .count-host" });
makeOrbitCluster({ mount: "#s2 .fx" });
quoteCards({ mount: "#s3 .cards" });
</script>"""


def test_scene_blocks_finds_all():
    blocks = parse.scene_blocks(SAMEY)
    assert [b["scene_no"] for b in blocks] == [1, 2, 3]


def test_samey_scores_low_with_dominant_signature_diag():
    ev = {"index_html": SAMEY, "scenes": [{"scene_no": i} for i in (1, 2, 3)]}
    r = D.score_motion_variety(ev, T)
    assert r.passed is False
    assert any("share" in d.lower() or "templated" in d.lower() for d in r.diagnostics)


def test_varied_scores_high():
    ev = {"index_html": VARIED, "scenes": [{"scene_no": i} for i in (1, 2, 3)]}
    r = D.score_motion_variety(ev, T)
    assert r.passed is True and r.score >= T["dimensions"]["motion_variety"]["floor"]


def test_no_html_is_none():
    r = D.score_motion_variety({"index_html": "", "scenes": []}, T)
    assert r.score is None and r.passed is None
