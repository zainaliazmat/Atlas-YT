"""Tests for burned-in whisper-synced captions (GOLDEN_REFERENCE.md §8).

Captions are grouped from vo.words.json into short lower-third phrases, emitted as
``.vo-cap clip`` divs on their OWN track index (2), ``data-layout-ignore`` so they
never enter the layout audit, as a ROOT-level ``.vo-cap-layer`` sibling (outside the
scenes) so they survive every cut and never tangle with a scene's metadata block.
Busy lower-centre scenes get the ``.vo-cap-low`` variant. Mirrors the reference.
"""

from __future__ import annotations

import json
import re

from studio.compose._captions import group_captions


WORDS = [  # "Before this video ends, you will unlock your phone. Log off now."
    {"id": "w0", "text": "Before", "start": 0.0, "end": 0.3},
    {"id": "w1", "text": "this", "start": 0.3, "end": 0.6},
    {"id": "w2", "text": "video", "start": 0.6, "end": 0.9},
    {"id": "w3", "text": "ends,", "start": 0.9, "end": 1.2},
    {"id": "w4", "text": "you", "start": 1.2, "end": 1.4},
    {"id": "w5", "text": "will", "start": 1.4, "end": 1.6},
    {"id": "w6", "text": "unlock", "start": 1.6, "end": 1.9},
    {"id": "w7", "text": "your", "start": 1.9, "end": 2.1},
    {"id": "w8", "text": "phone.", "start": 2.1, "end": 2.5},
    {"id": "w9", "text": "Log", "start": 2.6, "end": 2.8},
    {"id": "w10", "text": "off", "start": 2.8, "end": 3.0},
    {"id": "w11", "text": "now.", "start": 3.0, "end": 3.3},
]


def test_group_captions_covers_every_word_in_order():
    phrases = group_captions(WORDS)
    joined = " ".join(p["text"] for p in phrases).split()
    assert joined == [w["text"] for w in WORDS]


def test_group_captions_breaks_on_sentence_punctuation():
    phrases = group_captions(WORDS)
    # a phrase must end on the word "phone." (sentence end), not run past it
    ends = [p["text"].split()[-1] for p in phrases]
    assert "phone." in ends
    assert "now." in ends


def test_group_captions_respects_max_words():
    phrases = group_captions(WORDS, max_words=4)
    assert all(len(p["text"].split()) <= 4 for p in phrases)


def test_consecutive_captions_never_overlap_on_their_track():
    """Captions share one track index (2); word-contiguous phrases must still leave a
    gap so they never overlap (HyperFrames errors on same-track overlap)."""
    phrases = group_captions(WORDS)
    for a, b in zip(phrases, phrases[1:]):
        assert a["start"] + a["duration"] <= b["start"] - 0.03


def test_group_captions_timings_from_words():
    phrases = group_captions(WORDS)
    first = phrases[0]
    assert first["start"] == 0.0  # phrase starts at its first word
    # duration is derived from the words and never runs past the last word's end
    # (it may be trimmed shorter to leave the no-overlap gap)
    last_word_end = next(w["end"] for w in WORDS
                         if w["text"] == first["text"].split()[-1])
    assert 0 < first["duration"] <= last_word_end - first["start"] + 1e-6


# --- compose integration --------------------------------------------------------
SCRIPT = {
    "schema_version": "studio-1", "working_title": "Dark Truth",
    "scenes": [
        {"scene_no": 1, "beat": "hook", "point": "automatic",
         "narration": "Before this video ends you will unlock your phone.",
         "on_screen_text": "YOU WILL", "duration_est_sec": 6, "claims": []},
        {"scene_no": 2, "beat": "scale", "point": "platforms",
         "narration": "Five billion of us scroll the feed every day.",
         "on_screen_text": "5B USERS", "duration_est_sec": 5, "claims": []},
    ],
}


def _project_with_vo(tmp_path, monkeypatch):
    from studio import config as cfg, pipeline, vo
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(cfg, "ASSET_LIBRARY_DIR", tmp_path / "lib")
    slug = "cap1"
    pdir = pipeline.scaffold_project(slug)
    (pdir / "script.json").write_text(json.dumps(SCRIPT), encoding="utf-8")
    (pdir / "research_brief.json").write_text(
        json.dumps({"topic": "t", "verified_facts": [], "sources": []}), encoding="utf-8")
    grid = vo.retimer_windows([3.3, 4.0], old_grid=[6, 5])
    # word-level transcript spanning both scenes (global timeline)
    words = WORDS + [
        {"id": "w12", "text": "Five", "start": 3.4, "end": 3.7},
        {"id": "w13", "text": "billion", "start": 3.7, "end": 4.1},
        {"id": "w14", "text": "scroll", "start": 4.1, "end": 4.6},
        {"id": "w15", "text": "the", "start": 4.6, "end": 4.8},
        {"id": "w16", "text": "feed.", "start": 4.8, "end": 5.2},
    ]
    (pdir / "assets/audio").mkdir(parents=True, exist_ok=True)
    (pdir / "assets/audio/vo.words.json").write_text(json.dumps(words), encoding="utf-8")
    manifest = {
        "schema_version": "studio-vo-1", "voice": "am_onyx", "grid": grid,
        "scenes": [{"scene_no": i + 1, "src": f"assets/audio/vo/s{i+1}.wav",
                    "vo_dur": d, "speed": 0.95, "narration": SCRIPT["scenes"][i]["narration"],
                    "track_index": vo.VO_TRACKS[i % 2]} for i, d in enumerate([3.3, 4.0])],
        "vo_mp3": "assets/audio/vo.mp3", "words_json": "assets/audio/vo.words.json",
        "total_duration_sec": grid["total"], "audio": [], "bed": "none", "sfx": [],
    }
    (pdir / "vo.grid.json").write_text(json.dumps(manifest), encoding="utf-8")
    return slug, pdir


def test_compose_burns_in_caption_layer(tmp_path, monkeypatch):
    from studio.compose import compose
    slug, pdir = _project_with_vo(tmp_path, monkeypatch)
    html = compose(slug, pack_id="dark-truth-social").read_text(encoding="utf-8")
    assert 'class="vo-cap-layer"' in html
    caps = re.findall(r'<div class="vo-cap[^"]*clip[^"]*"[^>]*>', html)
    assert caps, "no caption divs emitted"
    # captions on their own track index (2), excluded from the layout audit
    for tag in caps:
        assert 'data-track-index="2"' in tag
        assert "data-layout-ignore" in tag


def test_captions_are_root_level_not_nested_in_scenes(tmp_path, monkeypatch):
    """The caption layer must be a root sibling, never inside a <section> scene, so it
    can't overlap / inherit a scene's metadata block stacking."""
    from studio.compose import compose
    slug, pdir = _project_with_vo(tmp_path, monkeypatch)
    html = compose(slug, pack_id="dark-truth-social").read_text(encoding="utf-8")
    # everything from the first <section to the last </section> is scene markup
    scene_region = html[html.index("<section"):html.rindex("</section>") + len("</section>")]
    assert "vo-cap-layer" not in scene_region
    assert "vo-cap-layer" in html  # but it does exist, at root


def test_busy_lower_center_scene_uses_caption_low_variant(tmp_path, monkeypatch):
    """Scene 2 is a platforms/orbit beat occupying the lower-centre, so its captions
    drop to the .vo-cap-low position (dodging the busy region)."""
    from studio.compose import compose
    slug, pdir = _project_with_vo(tmp_path, monkeypatch)
    html = compose(slug, pack_id="dark-truth-social").read_text(encoding="utf-8")
    assert "vo-cap-low" in html
