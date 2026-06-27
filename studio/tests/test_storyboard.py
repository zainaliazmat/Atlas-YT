"""Tests for studio.storyboard.tag_archetypes (Phase B Task B2)."""
from studio import storyboard


def test_tag_uses_iris_layout_when_in_vocab():
    script = {"scenes": [{"scene_no": 1, "on_screen_text": "X", "claims": []},
                         {"scene_no": 2, "on_screen_text": "Y", "claims": []}]}
    fake_iris = lambda s, p: {"scenes": [{"scene_no": 1, "layout": "quote-card"},
                                         {"scene_no": 2, "layout": "big-number"}]}
    board = storyboard.tag_archetypes(script, None, iris_fn=fake_iris)
    tags = {s["scene_no"]: s["archetype"] for s in board["scenes"]}
    assert tags == {1: "quote-card", 2: "big-number"}


def test_tag_falls_back_to_classify_on_unknown_or_iris_failure():
    script = {"scenes": [{"scene_no": 1, "on_screen_text": "141 users", "claims": []}]}
    def boom(s, p):
        raise RuntimeError("iris down")
    board = storyboard.tag_archetypes(script, None, iris_fn=boom)
    # classify() sees a number → big-number
    assert board["scenes"][0]["archetype"] == "big-number"


def test_tag_falls_back_to_classify_when_iris_returns_out_of_vocab_layout():
    """Path (b): Iris returns successfully but with a layout NOT in ARCHETYPES.ARCHETYPES.

    The guard `layout if layout in A.ARCHETYPES else A.classify(sc)` must discard
    the bogus layout and delegate to classify(). This test is distinct from the
    exception-raising test above: iris_fn does NOT raise — it returns a board whose
    scene carries an invented layout string that is outside the closed vocab.

    Determinacy: on_screen_text "42 billion" contains a digit, so classify() returns
    "big-number" regardless of the bogus iris layout.
    """
    script = {"scenes": [{"scene_no": 1, "on_screen_text": "42 billion", "claims": []}]}

    def iris_returns_bogus_layout(s, p):
        # Returns successfully, but 'invented-layout-xyz' is NOT in ARCHETYPES
        return {"scenes": [{"scene_no": 1, "layout": "invented-layout-xyz"}]}

    board = storyboard.tag_archetypes(script, None, iris_fn=iris_returns_bogus_layout)
    # The bogus layout must be rejected; classify() sees a digit → "big-number"
    assert board["scenes"][0]["archetype"] == "big-number"
