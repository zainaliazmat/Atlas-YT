"""Offline tests for studio.vo — the Kokoro VO flow + the VO-lock re-timer bridge.

The TTS/concat/transcribe TOOLCHAIN is REUSED from audio-designer/hf_audio.py and is
injected here as fakes (no Node, no ffmpeg, no whisper). What these tests pin is the
studio-specific orchestration: the VO-driven grid (NS = prefix-sum of clip durations,
ND = clip + tail), the alternating-track audio layout, the per-scene overrun speed
bump, the voice audition, the word-level fallback, and the no-silent-gaps invariant.

The reference numbers in test_retimer_matches_golden_reference are lifted verbatim
from reference/dark-truth-social/index.html (OS/OD/NS/ND) so the bridge reproduces
the proven golden-reference grid exactly.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from studio import vo


# --- injected toolchain fakes (stand in for hf_audio.tts / concat / transcribe) ----
def make_fake_tts(rate: float = 0.4, fail_voices=()):
    """A deterministic tts seam: duration = words * rate / speed. Writes a stub wav
    and records every call so the speed-bump / audition behaviour is inspectable."""
    calls: list[dict] = []

    def _tts(text, out, *, voice="af_heart", speed=1.0):
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if voice in fail_voices:
            calls.append({"text": text, "voice": voice, "speed": speed, "ok": False})
            return {"ok": False, "duration": None, "output": None, "error": "no such voice"}
        out.write_bytes(b"RIFFstubwav")
        dur = round(max(1, len(str(text).split())) * rate / speed, 3)
        calls.append({"text": text, "out": str(out), "voice": voice, "speed": speed,
                      "dur": dur, "ok": True})
        return {"ok": True, "duration": dur, "output": str(out), "error": None}

    _tts.calls = calls
    return _tts


def fake_concat(wavs, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"CONCAT")
    return {"ok": True, "output": str(out), "error": None}


def fake_encode(in_path, out_path):
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"ID3mp3")
    return {"ok": True, "output": str(out), "error": None}


SCRIPT = {
    "schema_version": "studio-1",
    "working_title": "Dark Truth",
    "scenes": [
        {"scene_no": 1, "beat": "hook", "point": "automatic",
         "narration": "Before this video ends you will unlock your phone four more times.",
         "on_screen_text": "YOU WILL UNLOCK", "duration_est_sec": 6, "claims": []},
        {"scene_no": 2, "beat": "title", "point": "thesis",
         "narration": "This is the dark truth behind social media.",
         "on_screen_text": "DARK TRUTH", "duration_est_sec": 4, "claims": []},
        {"scene_no": 3, "beat": "machine", "point": "designed to hook",
         "narration": "The endless feed is a slot machine for your brain.",
         "on_screen_text": "IT IS THE PRODUCT", "duration_est_sec": 10, "claims": []},
    ],
}


# ======================================================================
# retimer_windows — the pure VO-lock grid math
# ======================================================================
def test_retimer_matches_golden_reference():
    """The grid bridge reproduces reference/dark-truth-social NS/ND exactly:
    NS = prefix-sum of (3-dp-rounded) clip durations; ND = clip + 0.4s tail."""
    vo_durs = [5.866667, 3.392, 10.986667, 11.2, 12.181333,
               10.816, 12.565333, 11.306667, 6.144]
    old_grid = [18, 14, 28, 32, 36, 34, 26, 32, 20]
    g = vo.retimer_windows(vo_durs, old_grid=old_grid)

    assert g["OS"] == [0, 18, 32, 60, 92, 128, 162, 188, 220]
    assert g["OD"] == [18, 14, 28, 32, 36, 34, 26, 32, 20]
    assert g["NS"] == [0, 5.867, 9.259, 20.246, 31.446,
                       43.627, 54.443, 67.008, 78.315]
    assert g["ND"] == [6.267, 3.792, 11.387, 11.6, 12.581,
                       11.216, 12.965, 11.707, 6.544]
    assert g["total"] == 84.859


def test_retimer_has_no_silent_gaps():
    """VO clips are placed back-to-back: every scene starts exactly when the
    previous clip ends, so there is zero silent gap between narration clips."""
    vo_durs = [5.866667, 3.392, 10.986667, 11.2]
    g = vo.retimer_windows(vo_durs, old_grid=[6, 4, 10, 11])
    rounded = [round(d, 3) for d in vo_durs]
    for i in range(len(rounded) - 1):
        gap = g["NS"][i + 1] - (g["NS"][i] + rounded[i])
        assert abs(gap) < 1e-6, f"silent gap of {gap}s before scene {i + 2}"


def test_retimer_tail_overlaps_next_scene_window():
    """Each scene WINDOW (ND) runs 0.4s past where the next scene's VO starts, so
    the transition has both scenes live across the seam."""
    vo_durs = [5.0, 4.0, 6.0]
    g = vo.retimer_windows(vo_durs, old_grid=[5, 4, 6], tail=0.4)
    for i in range(len(vo_durs) - 1):
        window_end = g["NS"][i] + g["ND"][i]
        assert round(window_end - g["NS"][i + 1], 3) == 0.4


# ======================================================================
# voice audition — am_onyx is the authoritative default
# ======================================================================
def test_audition_picks_first_working_voice_as_default(tmp_path):
    tts = make_fake_tts()
    voice = vo.audition_voice(tmp_path, tts_fn=tts)
    assert voice == "am_onyx"
    # a sample was actually synthesized for the audition (not just guessed)
    assert any(c.get("voice") == "am_onyx" and c.get("ok") for c in tts.calls)


def test_audition_falls_through_to_next_when_a_voice_fails(tmp_path):
    tts = make_fake_tts(fail_voices=("am_onyx",))
    voice = vo.audition_voice(tmp_path, tts_fn=tts)
    assert voice == "am_michael"


# ======================================================================
# record_vo — per-scene VO + stitched vo.mp3 + word-level transcript + grid
# ======================================================================
def test_record_vo_produces_per_scene_clips_and_grid(tmp_path):
    tts = make_fake_tts()
    res = vo.record_vo(SCRIPT, tmp_path, tts_fn=tts, concat_fn=fake_concat,
                       encode_fn=fake_encode, transcribe_fn=None)
    assert res["voice"] == "am_onyx"
    # one s{n}.wav per scene, named s1..s3
    for n in (1, 2, 3):
        assert (tmp_path / f"assets/audio/vo/s{n}.wav").exists()
    assert res["vo_mp3"] == "assets/audio/vo.mp3"
    assert (tmp_path / "assets/audio/vo.mp3").exists()
    # grid wired straight from the clip durations
    assert len(res["grid"]["NS"]) == 3
    assert res["grid"]["NS"][0] == 0
    assert res["total_duration_sec"] == res["grid"]["total"]


def test_record_vo_speeds_up_a_scene_that_overruns_its_window(tmp_path):
    # rate 1.0/word, scene 2's window is 4s but its 8-word line is ~8s at 0.95 ->
    # must bump speed above the 0.95 base so the clip fits closer to the window.
    tts = make_fake_tts(rate=1.0)
    vo.record_vo(SCRIPT, tmp_path, tts_fn=tts, concat_fn=fake_concat,
                 encode_fn=fake_encode, transcribe_fn=None)
    s2_calls = [c for c in tts.calls if c.get("ok") and "dark truth behind" in c["text"].lower()]
    assert s2_calls, "scene 2 was never synthesized"
    assert max(c["speed"] for c in s2_calls) > vo.DEFAULT_SPEED, \
        "overrunning scene was not sped up"


def test_record_vo_writes_word_level_json_without_whisper(tmp_path):
    """whisper.cpp is absent in this env (transcribe -> skipped); vo.words.json must
    still exist with the right [{id,text,start,end}] shape via the deterministic
    fallback (words spread across each scene's VO window)."""
    tts = make_fake_tts()
    res = vo.record_vo(SCRIPT, tmp_path, tts_fn=tts, concat_fn=fake_concat,
                       encode_fn=fake_encode, transcribe_fn=None)
    words_path = tmp_path / res["words_json"]
    assert words_path.exists()
    words = json.loads(words_path.read_text())
    assert words and all({"id", "text", "start", "end"} <= set(w) for w in words)
    # monotonic, within the VO runtime
    starts = [w["start"] for w in words]
    assert starts == sorted(starts)
    assert words[-1]["end"] <= res["total_duration_sec"] + 1e-6


# ======================================================================
# mix — mood bed ducked under VO + SFX on transition beats
# ======================================================================
def _fake_obtain(kind, tags, constraints=None):
    name = (tags or ["x"])[0]
    return SimpleNamespace(kind=kind, name=name,
                           entry={"file": f"audio/{name}.mp3"}, embed="audio")


def _fake_materialize(ref):
    return f"assets/audio/{ref.name}.mp3"


def _vo_result_for_mix():
    vo_durs = [5.0, 4.0, 6.0]
    grid = vo.retimer_windows(vo_durs, old_grid=[6, 4, 10])
    scenes = [{"scene_no": i + 1, "src": f"assets/audio/vo/s{i+1}.wav",
               "vo_dur": d, "track_index": vo.VO_TRACKS[i % 2]}
              for i, d in enumerate([5.0, 4.0, 6.0])]
    return {"grid": grid, "scenes": scenes, "vo_durs": vo_durs,
            "total_duration_sec": grid["total"]}


def test_mix_places_ducked_bed_and_one_accent_per_seam(tmp_path):
    res = vo.mix(SCRIPT, _vo_result_for_mix(), tmp_path,
                 obtain_fn=_fake_obtain, materialize_fn=_fake_materialize)
    rows = res["audio"]
    bed = [r for r in rows if r["track"] == vo.BED_TRACK]
    assert len(bed) == 1 and bed[0]["volume"] < 1.0, "bed not present / not ducked"
    assert bed[0]["start"] == 0
    sfx = [r for r in rows if r["track"] == vo.SFX_TRACK]
    sfx_starts = [round(r["start"], 3) for r in sfx]
    # every transition seam carries an accent (a whoosh, or a content cue at that start)
    seams = {round(s, 3) for s in _vo_result_for_mix()["grid"]["NS"][1:]}
    assert seams <= set(sfx_starts), "a transition seam has no accent"
    # but never TWO accents stacked on the same track at the same instant
    assert len(sfx_starts) == len(set(sfx_starts)), "two SFX hits collide on one beat"


def test_mix_places_a_content_cue_for_a_slot_machine_scene(tmp_path):
    res = vo.mix(SCRIPT, _vo_result_for_mix(), tmp_path,
                 obtain_fn=_fake_obtain, materialize_fn=_fake_materialize)
    srcs = " ".join(r["src"] for r in res["audio"])
    # scene 3 narration ("slot machine for your brain") earns a slot-reel cue
    assert "slot" in srcs


# ======================================================================
# produce_vo — the orchestrator: artifacts + vo.grid.json, no gaps end-to-end
# ======================================================================
def test_compose_consumes_vo_grid_for_timing_and_audio(tmp_path, monkeypatch):
    """When vo.grid.json is present the Composer conforms the composition to the real
    VO: scene windows become NS/ND, and the audio layer is the VO-driven manifest
    (alternating-track VO + ducked bed + SFX) — the GOLDEN_REFERENCE.md §2 VO-lock."""
    from studio import config as cfg, pipeline
    from studio.compose import compose

    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(cfg, "ASSET_LIBRARY_DIR", tmp_path / "lib")
    slug = "vg1"
    pdir = pipeline.scaffold_project(slug)
    (pdir / "script.json").write_text(json.dumps(SCRIPT), encoding="utf-8")
    (pdir / "research_brief.json").write_text(
        json.dumps({"topic": "t", "verified_facts": [], "sources": []}), encoding="utf-8")

    grid = vo.retimer_windows([5.0, 4.0, 6.0], old_grid=[6, 4, 10])
    manifest = {
        "schema_version": "studio-vo-1", "voice": "am_onyx", "grid": grid,
        "scenes": [{"scene_no": i + 1, "src": f"assets/audio/vo/s{i+1}.wav",
                    "vo_dur": d, "speed": 0.95, "narration": "x",
                    "track_index": vo.VO_TRACKS[i % 2]}
                   for i, d in enumerate([5.0, 4.0, 6.0])],
        "vo_mp3": "assets/audio/vo.mp3", "words_json": "assets/audio/vo.words.json",
        "total_duration_sec": grid["total"],
        "audio": [
            {"role": "vo", "src": "assets/audio/vo/s1.wav", "start": grid["NS"][0],
             "dur": 5.0, "track": 9, "volume": 1.0},
            {"role": "vo", "src": "assets/audio/vo/s2.wav", "start": grid["NS"][1],
             "dur": 4.0, "track": 10, "volume": 1.0},
            {"role": "vo", "src": "assets/audio/vo/s3.wav", "start": grid["NS"][2],
             "dur": 6.0, "track": 9, "volume": 1.0},
            {"role": "music", "src": "assets/audio/bed.mp3", "start": 0,
             "dur": grid["total"], "track": 8, "volume": 0.18},
            {"role": "sfx", "src": "assets/audio/whoosh.mp3", "start": grid["NS"][1],
             "dur": 0.6, "track": 7, "volume": 0.5},
        ],
        "bed": "cleared", "sfx": ["whoosh"],
    }
    (pdir / "vo.grid.json").write_text(json.dumps(manifest), encoding="utf-8")

    from studio.compose import _fmt

    html = compose(slug, pack_id="dark-truth-social").read_text(encoding="utf-8")

    # scene windows are the VO-driven NS/ND, not the duration_est grid
    import re as _re
    s1 = _re.search(r'<section id="s1"[^>]*>', html).group(0)
    assert f'data-start="{_fmt(grid["NS"][0])}"' in s1
    assert f'data-duration="{_fmt(grid["ND"][0])}"' in s1
    s2 = _re.search(r'<section id="s2"[^>]*>', html).group(0)
    assert f'data-start="{_fmt(grid["NS"][1])}"' in s2
    # VO audio on alternating track indices
    assert 'src="assets/audio/vo/s1.wav"' in html and 'data-track-index="9"' in html
    assert 'src="assets/audio/vo/s2.wav"' in html and 'data-track-index="10"' in html
    # ducked bed on track 8, sfx on track 7
    assert 'src="assets/audio/bed.mp3"' in html and 'data-track-index="8"' in html
    assert 'data-track-index="7"' in html
    # the real VO grid reaches the re-timer
    assert f'var NS = [{", ".join(_fmt(x) for x in grid["NS"])}]' in html


def test_produce_vo_writes_grid_manifest_with_alternating_vo_tracks(tmp_path):
    tts = make_fake_tts()
    res = vo.produce_vo(SCRIPT, tmp_path, pack=None, tts_fn=tts,
                        concat_fn=fake_concat, encode_fn=fake_encode,
                        transcribe_fn=None, obtain_fn=_fake_obtain,
                        materialize_fn=_fake_materialize)
    grid_file = tmp_path / "vo.grid.json"
    assert grid_file.exists()
    manifest = json.loads(grid_file.read_text())
    vo_rows = [r for r in manifest["audio"] if r["track"] in vo.VO_TRACKS]
    assert [r["track"] for r in vo_rows] == [9, 10, 9]  # alternation
    # the VO rows tile the timeline with no silent gap
    vo_rows.sort(key=lambda r: r["start"])
    for a, b in zip(vo_rows, vo_rows[1:]):
        assert abs((a["start"] + a["dur"]) - b["start"]) < 1e-6
    assert res["voice"] == "am_onyx"
