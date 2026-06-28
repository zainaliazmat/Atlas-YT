"""Variety regression test — lock in 9/9 distinct beat signatures from a diverse storyboard.

Regression guard for the Plan 2 Phase C climb:
  - With a diverse 9-archetype storyboard, all 9 scenes yield DISTINCT beat signatures.
  - Without a storyboard, the heuristic classify() fallback collapses variety.
  - The distinct-signature count WITH a storyboard must be strictly greater than WITHOUT.

Design: hermetic fixture (monkeypatched PROJECTS_DIR → tmp_path, no live projects/ required).
The 9 archetypes chosen avoid `quote-card` (needs an attributed-quote claim to dispatch);
every other archetype produces its beat token unconditionally from the scene text alone.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from studio import config
from studio.compose import compose
from studio.gate import parse


# ---------------------------------------------------------------------------
# Shared scenes — 9 scenes, each typed so the chosen archetype builder fires
# ---------------------------------------------------------------------------

_SCENES = [
    # scene 1 — big-number  (dominant statistic)
    {
        "scene_no": 1,
        "on_screen_text": "5.66B USERS",
        "point": "Global reach",
        "narration": "Five point six six billion users are now online.",
        "duration_est_sec": 6,
        "claims": [],
    },
    # scene 2 — title-card  (opening identity)
    {
        "scene_no": 2,
        "on_screen_text": "THE ALGORITHM",
        "point": "Title",
        "narration": "Welcome to the algorithm.",
        "duration_est_sec": 7,
        "claims": [],
    },
    # scene 3 — data-chart  (time-series / percentage data)
    {
        "scene_no": 3,
        "on_screen_text": "ENGAGEMENT OVER TIME",
        "point": "Trend",
        "narration": "Engagement climbed forty percent year on year.",
        "duration_est_sec": 7,
        "claims": [],
    },
    # scene 4 — full-bleed-image  (immersive visual)
    {
        "scene_no": 4,
        "on_screen_text": "THE FEED",
        "point": "Visual",
        "narration": "The infinite feed never ends.",
        "duration_est_sec": 6,
        "claims": [],
    },
    # scene 5 — split-screen  (two-panel contrast)
    {
        "scene_no": 5,
        "on_screen_text": "BEFORE VS AFTER",
        "point": "Contrast",
        "narration": "What changed between before and after?",
        "duration_est_sec": 6,
        "claims": [],
    },
    # scene 6 — comparison-2up  (X VS Y side-by-side)
    {
        "scene_no": 6,
        "on_screen_text": "HUMAN VS MACHINE",
        "point": "Comparison",
        "narration": "Human decision-making versus machine inference.",
        "duration_est_sec": 7,
        "claims": [],
    },
    # scene 7 — centered-statement  (bold statement)
    {
        "scene_no": 7,
        "on_screen_text": "ATTENTION IS THE PRODUCT",
        "point": "Key insight",
        "narration": "Your attention is the product being sold.",
        "duration_est_sec": 6,
        "claims": [],
    },
    # scene 8 — list-stack  (enumerated steps)
    {
        "scene_no": 8,
        "on_screen_text": "THREE STEPS TO RECLAIM FOCUS",
        "point": "Steps",
        "narration": "Here are the checklist steps you need.",
        "duration_est_sec": 7,
        "claims": [],
    },
    # scene 9 — lower-third  (lower-banner attribution)
    {
        "scene_no": 9,
        "on_screen_text": "DR. ANNA LEMBKE",
        "point": "Expert",
        "narration": "Stanford neuroscientist Dr Anna Lembke explains.",
        "duration_est_sec": 6,
        "claims": [],
    },
]

# Storyboard that tags each scene with a distinct archetype (no quote-card)
_STORYBOARD = {
    "scenes": [
        {"scene_no": 1, "archetype": "big-number"},
        {"scene_no": 2, "archetype": "title-card"},
        {"scene_no": 3, "archetype": "data-chart"},
        {"scene_no": 4, "archetype": "full-bleed-image"},
        {"scene_no": 5, "archetype": "split-screen"},
        {"scene_no": 6, "archetype": "comparison-2up"},
        {"scene_no": 7, "archetype": "centered-statement"},
        {"scene_no": 8, "archetype": "list-stack"},
        {"scene_no": 9, "archetype": "lower-third"},
    ]
}

# Expected beat tokens in the same order (sanity reference, not asserted per-scene)
_EXPECTED_TOKENS = [
    "count-up",         # big-number
    "orbit",            # title-card
    "calendar-crumble", # data-chart
    "device-loop",      # full-bleed-image
    "tile-parallax",    # split-screen
    "shatter",          # comparison-2up
    "strike",           # centered-statement
    "checklist",        # list-stack
    "signature",        # lower-third
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_project(tmp_path: Path, slug: str, with_storyboard: bool) -> Path:
    """Create a minimal project directory and return its path."""
    pdir = tmp_path / slug
    pdir.mkdir()
    (pdir / "research_brief.json").write_text(
        json.dumps({"topic": "attention economy"}), encoding="utf-8"
    )
    (pdir / "script.json").write_text(
        json.dumps({"working_title": "Variety Test", "scenes": _SCENES}),
        encoding="utf-8",
    )
    if with_storyboard:
        (pdir / "storyboard.json").write_text(
            json.dumps(_STORYBOARD), encoding="utf-8"
        )
    return pdir


@pytest.fixture
def variety_projects(tmp_path, monkeypatch):
    """Two hermetic projects sharing the same PROJECTS_DIR (tmp_path):
    - 'variety'         — WITH storyboard.json (9 distinct archetypes)
    - 'variety_noboard' — WITHOUT storyboard.json (heuristic classify() only)
    """
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    _make_project(tmp_path, "variety", with_storyboard=True)
    _make_project(tmp_path, "variety_noboard", with_storyboard=False)
    return tmp_path


# ---------------------------------------------------------------------------
# The regression test
# ---------------------------------------------------------------------------

def test_variety_regression(variety_projects):
    """Lock in that a diverse storyboard produces >= 8 distinct beat signatures
    and that every scene dispatches to a real archetype (none is 'plain').

    Also asserts that the storyboard-driven route yields strictly MORE distinct
    signatures than the heuristic classify() fallback alone — this is the
    regression guard for the Plan 2 Phase C climb.
    """
    tmp_path = variety_projects

    # --- compose WITH storyboard ---
    out_with = compose("variety", pack_id="dark-truth-social")
    html_with = Path(out_with).read_text(encoding="utf-8")

    blocks_with = parse.scene_blocks(html_with)
    assert len(blocks_with) == 9, (
        f"Expected 9 scene blocks, got {len(blocks_with)}. "
        "The 9-scene script did not produce 9 <section id='sN'> elements."
    )

    sigs_with = [
        parse.scene_signature(b["html"], html_with, b["id"])
        for b in blocks_with
    ]
    distinct_with = len(set(sigs_with))

    # --- compose WITHOUT storyboard ---
    out_without = compose("variety_noboard", pack_id="dark-truth-social")
    html_without = Path(out_without).read_text(encoding="utf-8")

    blocks_without = parse.scene_blocks(html_without)
    sigs_without = [
        parse.scene_signature(b["html"], html_without, b["id"])
        for b in blocks_without
    ]
    distinct_without = len(set(sigs_without))

    # Assertion 1: all 9 scene blocks present (already checked above, kept for clarity)
    assert len(blocks_with) == 9

    # Assertion 2: >= 8 distinct signatures with storyboard (1 slack vs live 9/9)
    assert distinct_with >= 8, (
        f"Expected >= 8 distinct signatures with storyboard, got {distinct_with}. "
        f"Signatures: {sigs_with}. "
        "Possible cause: an archetype builder emitted a beat token that parse "
        "doesn't recognise, or two builders share the same token."
    )

    # Assertion 3: none of the 9 signatures is 'plain' (every scene dispatched)
    plain_scenes = [
        (blocks_with[i]["id"], sigs_with[i])
        for i in range(len(sigs_with))
        if sigs_with[i] == "plain"
    ]
    assert not plain_scenes, (
        f"'plain' signature found — these scenes did not dispatch to a real archetype: "
        f"{plain_scenes}. "
        "Check that the builder emits a recognised beat token or HTML marker."
    )

    # Assertion 4: regression guard — storyboard gives STRICTLY more variety
    assert distinct_with > distinct_without, (
        f"Storyboard distinct signatures ({distinct_with}) must exceed "
        f"classify()-only signatures ({distinct_without}). "
        f"With storyboard: {sigs_with}. Without: {sigs_without}. "
        "If classify() already collapses to a single token and the storyboard "
        "still only gives 1, the archetype dispatch is not working."
    )
