"""Offline proof for Cadence's audio engine — NO network, NO API keys, NO toolchain.

Run (from the project folder):  ../venv/bin/python tests/test_audio_engine.py
Or:                             ../venv/bin/python -m pytest tests/test_audio_engine.py

Every seam (tts / concat / transcribe / the SourceClient / the FFmpeg mix / SFX synth)
is INJECTED or monkeypatched, so we assert the engine's PLUMBING and HARD INVARIANTS
only — never a real render or request:
  - validate_script rejects malformed input BEFORE any (expensive) tts/download
  - normalize_license + classify: the audio license truth table (CC0/PDM/PD accept,
    BY/BY-SA accept, NC/ND + no-known + NoC-US + Sampling+ + unknown reject)
  - record_narration: per-scene scene-offset math (cumulative GLOBAL start/end) from
    fixture tts durations; transcript shape; total = sum(durations); raises on bad
    script / tts failure (no partial artifact)
  - rank_candidates: license-first, reproducible; rejects + off-allowlist dropped
  - source_bed: cleared bed carries license+attribution; un-attributable BY skipped;
    nothing-clears -> flagged placeholder whose uri resolves to a LOCAL file
  - signature-SFX anchor math: at_sec == the signature scene's first segment start
  - mix_audio end-to-end (fake client + fake mix): the master-bridge (narration uri ==
    master; vo_uri/master_uri set); the THREE total_duration_sec agree; a placeholder
    bed is in the manifest but NOT in the mix recipe; license+attribution enforced in
    code (a music/sfx track missing either raises)
  - build_mix_recipe (PURE): the documentary filtergraph (sidechain duck, accent delay,
    trim-to-total) for each cleared-track set
  - sfx_kit + audio_sources parsers (canned dicts)
  - the emitted transcript + manifest validate against atlas's frozen contracts, AND a
    1.0 stub-shaped manifest STILL validates against the 1.1 schema (validate() is not
    version-aware -> the new fields are optional)

HONEST NOTE: real tts (Kokoro), real archive responses, real whisper.cpp word-timing,
real audio downloads, and the real FFmpeg mix are MANUAL/integration checks (a real
`run.py narrate` / `run.py mix`, or the full pipeline through atlas). Only the plumbing,
the invariants, and the per-source parsers (canned dicts) are unit-tested here.
"""
import pathlib
import sys
import tempfile
import threading
import time
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import audio_engine as engine  # noqa: E402
import audio_sources  # noqa: E402
import hf_audio  # noqa: E402
import sfx_kit  # noqa: E402
from audio_sources import AudioCandidate  # noqa: E402

# Make atlas's frozen contracts importable. APPEND (not insert-at-0): atlas/ also ships
# chat.py / llm.py / chat_state.py, and we must NOT let those shadow our own modules.
_ATLAS = pathlib.Path(__file__).resolve().parent.parent.parent / "atlas"
sys.path.append(str(_ATLAS))
import contracts  # noqa: E402


# ======================================================================
# Helpers
# ======================================================================
def acand(source, title, license_raw, *, author="", url=None, dur=120.0, ext="mp3",
          license_url=""):
    url = url or f"https://example.test/{source}/{title.replace(' ', '-')}.{ext}"
    return AudioCandidate(source=source, title=title, author=author,
                          source_url=f"https://example.test/{source}/page",
                          license_raw=license_raw, download_url=url, ext=ext,
                          kind="music", duration=dur, extra={"license_url": license_url})


SCRIPT = {
    "schema_version": "1.0", "working_title": "Gravity",
    "scenes": [
        {"scene_no": 1, "point": "p1", "narration": "Here is the first thing."},
        {"scene_no": 2, "point": "p2", "narration": "And here is the second thing."},
        {"scene_no": 3, "point": "p3", "narration": "Finally the third thing."},
    ],
}
STORYBOARD = {"schema_version": "1.1", "scenes": [
    {"scene_no": 1, "layout": "x", "signature_beat": False},
    {"scene_no": 2, "layout": "x", "signature_beat": True},
    {"scene_no": 3, "layout": "x", "signature_beat": False},
]}
STYLE = {"schema_version": "1.0", "palette": {"signature_highlight": "#FFD000"},
         "reference_note": "editorial explainer / Vox-style", "dos": ["one point per scene"]}

# Deterministic per-scene tts durations (the fixture the offset math hangs on).
_DURS = {1: 2.0, 2: 3.0, 3: 4.0}


def fake_tts(text, out):
    """A network-free tts: writes a tiny stub wav, reports a fixed per-scene duration."""
    p = pathlib.Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"RIFFstub")
    # scene number is in the filename scene-NN.wav
    n = int(pathlib.Path(out).stem.split("-")[1])
    return {"ok": True, "duration": _DURS[n], "output": str(out), "error": None}


def fake_concat(wavs, out):
    p = pathlib.Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"RIFFconcat")
    return {"ok": True, "output": str(out), "error": None}


def fake_mix(recipe):
    p = pathlib.Path(recipe["output"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"RIFFmaster")
    return {"ok": True, "output": str(p), "error": None}


class FakeClient:
    """Network-free SourceClient: per-source candidate lists + controllable downloads."""

    def __init__(self, by_source=None, *, unavailable=(), bad_downloads=(), downloads=None):
        self.by_source = by_source or {}
        self.unavailable = set(unavailable)
        self.bad_downloads = set(bad_downloads)
        self.downloads = downloads or {}

    def available(self, source):
        if source.name in self.unavailable:
            return False
        return source.keyless  # keyed sources are "unavailable" unless explicitly set

    def search(self, source, query, filters):
        return list(self.by_source.get(source.name, []))

    def download(self, url):
        if url in self.bad_downloads:
            raise RuntimeError("dead media url")
        return self.downloads.get(url, b"FAKE-AUDIO-BYTES")


# ======================================================================
# 1. Input validation
# ======================================================================
def test_validate_script_rejects_bad_input():
    assert engine.validate_script("nope")[0] is False
    assert engine.validate_script({})[0] is False
    assert engine.validate_script({"scenes": []})[0] is False
    assert engine.validate_script({"scenes": [{"scene_no": 1, "narration": "  "}]})[0] is False
    ok, _ = engine.validate_script(SCRIPT)
    assert ok is True


# ======================================================================
# 2. License truth table
# ======================================================================
def test_license_truth_table():
    accept = ["CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/",
              "Public Domain Mark 1.0", "public domain", "CC BY 4.0", "CC BY-SA 4.0",
              "cc-by 3.0"]
    reject = ["CC BY-NC 4.0", "CC BY-ND 4.0", "CC BY-NC-SA 4.0",
              "No known copyright restrictions", "No Copyright - United States",
              "Sampling Plus 1.0", "All rights reserved", "", "weird-unknown-string"]
    for s in accept:
        assert engine.is_acceptable(s), f"should accept {s!r} ({engine.normalize_license(s)})"
    for s in reject:
        assert not engine.is_acceptable(s), f"should reject {s!r} ({engine.normalize_license(s)})"
    # BY-SA records share-alike; CC0 needs no attribution.
    assert engine.classify("by-sa").share_alike is True
    assert engine.classify("cc0").requires_attribution is False
    assert engine.classify("by").requires_attribution is True


# ======================================================================
# 3. record_narration — scene-offset math + transcript shape
# ======================================================================
def test_record_narration_offsets_and_total():
    with tempfile.TemporaryDirectory() as d:
        out = engine.record_narration(SCRIPT, pdir=d, tts_fn=fake_tts, concat_fn=fake_concat)
        tr = out["transcript"]
        segs = tr["segments"]
        assert [s["scene_no"] for s in segs] == [1, 2, 3]
        # cumulative GLOBAL offsets from the fixture durations 2,3,4
        assert segs[0]["start_sec"] == 0.0 and segs[0]["end_sec"] == 2.0
        assert segs[1]["start_sec"] == 2.0 and segs[1]["end_sec"] == 5.0
        assert segs[2]["start_sec"] == 5.0 and segs[2]["end_sec"] == 9.0
        assert tr["total_duration_sec"] == 9.0 == out["total_duration_sec"]
        assert out["narration_wav"] == "audio/narration.wav"
        assert (pathlib.Path(d) / "audio" / "narration.wav").exists()


def test_record_narration_raises_on_bad_script():
    try:
        engine.record_narration({"scenes": []}, pdir="/tmp/x", tts_fn=fake_tts)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_record_narration_raises_on_tts_failure():
    def boom(text, out):
        return {"ok": False, "error": "kokoro exploded"}
    with tempfile.TemporaryDirectory() as d:
        try:
            engine.record_narration(SCRIPT, pdir=d, tts_fn=boom, concat_fn=fake_concat)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "tts failed" in str(exc)


def test_transcribe_enrichment_is_optional_and_safe():
    def boom_transcribe(path):
        raise RuntimeError("whisper.cpp not found")
    with tempfile.TemporaryDirectory() as d:
        out = engine.record_narration(SCRIPT, pdir=d, tts_fn=fake_tts,
                                      concat_fn=fake_concat, transcribe_fn=boom_transcribe)
        assert out["transcript"]["total_duration_sec"] == 9.0  # survived a failed enrich


# --- Parallelization safety: ordering is by SCENE INDEX, never completion order ---
def test_parallel_preserves_order_when_later_scenes_finish_first():
    """The determinism lock: synthesize concurrently, but the transcript offsets,
    total, and WAV concat order MUST be byte-identical to the sequential version even
    when LATER scenes complete FIRST. We force out-of-order completion by sleeping
    LONGEST on scene 1 and shortest on scene 3, and a barrier to prove the calls run
    concurrently (so a sequential impl on the same fake would deadlock/serialize and
    scene-1's slow sleep would gate everything — but order is asserted by index).

    Reported durations are the SAME fixture (2,3,4) so expected offsets are known-good.

    Ordering must hold for ANY concurrency width, so we pin the pool to 3 here (the
    production width is CPU-bounded by _tts_workers; that bound is tested separately).
    """
    _orig_workers = engine._tts_workers
    engine._tts_workers = lambda n: 3
    barrier = threading.Barrier(3, timeout=5.0)
    # finish order (by sleep): scene 3 first, then 2, then 1 last.
    sleeps = {1: 0.15, 2: 0.08, 3: 0.0}
    completion_order: list[int] = []
    lock = threading.Lock()

    def ooo_tts(text, out):
        n = int(pathlib.Path(out).stem.split("-")[1])
        barrier.wait()                    # all three must be in-flight at once => concurrent
        time.sleep(sleeps[n])
        with lock:
            completion_order.append(n)
        p = pathlib.Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RIFFstub")
        return {"ok": True, "duration": _DURS[n], "output": str(out), "error": None}

    captured = {}

    def capture_concat(wavs, out):
        captured["wav_order"] = [pathlib.Path(w).name for w in wavs]
        return fake_concat(wavs, out)

    try:
        with tempfile.TemporaryDirectory() as d:
            out = engine.record_narration(SCRIPT, pdir=d, tts_fn=ooo_tts,
                                          concat_fn=capture_concat)
    finally:
        engine._tts_workers = _orig_workers
    # Proof the calls really overlapped and finished out of original order.
    assert completion_order == [3, 2, 1], completion_order
    # Despite that, the transcript is IDENTICAL to the sequential known-good output.
    segs = out["transcript"]["segments"]
    assert [s["scene_no"] for s in segs] == [1, 2, 3]
    assert (segs[0]["start_sec"], segs[0]["end_sec"]) == (0.0, 2.0)
    assert (segs[1]["start_sec"], segs[1]["end_sec"]) == (2.0, 5.0)
    assert (segs[2]["start_sec"], segs[2]["end_sec"]) == (5.0, 9.0)
    assert out["total_duration_sec"] == 9.0
    # And the concat order is by scene index, NOT completion order.
    assert captured["wav_order"] == ["scene-01.wav", "scene-02.wav", "scene-03.wav"]


def test_parallel_output_is_identical_to_sequential_reference():
    """Equivalence lock: the parallel path's transcript dict equals the dict a strict
    sequential prefix-sum produces for the same inputs (segments + total, byte-equal)."""
    # Known-good sequential reference computed independently of the engine.
    t = 0.0
    ref_segments = []
    for sc in SCRIPT["scenes"]:
        n = sc["scene_no"]
        dur = _DURS[n]
        ref_segments.append({"scene_no": n, "start_sec": round(t, 3),
                             "end_sec": round(t + dur, 3), "text": sc["narration"]})
        t += dur
    with tempfile.TemporaryDirectory() as d:
        out = engine.record_narration(SCRIPT, pdir=d, tts_fn=fake_tts, concat_fn=fake_concat)
    assert out["transcript"]["segments"] == ref_segments
    assert out["transcript"]["total_duration_sec"] == round(t, 3)


def test_parallel_midlist_failure_raises_no_partial_master():
    """A failure on a MIDDLE scene must raise (matching sequential) and ship NO partial
    artifact: no narration.wav, and concat is never invoked."""
    concat_called = {"n": 0}

    def counting_concat(wavs, out):
        concat_called["n"] += 1
        return fake_concat(wavs, out)

    def fail_on_scene_2(text, out):
        n = int(pathlib.Path(out).stem.split("-")[1])
        if n == 2:
            return {"ok": False, "duration": 0.0, "output": str(out),
                    "error": "kokoro exploded on scene 2"}
        p = pathlib.Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RIFFstub")
        return {"ok": True, "duration": _DURS[n], "output": str(out), "error": None}

    with tempfile.TemporaryDirectory() as d:
        raised = False
        try:
            engine.record_narration(SCRIPT, pdir=d, tts_fn=fail_on_scene_2,
                                    concat_fn=counting_concat)
        except RuntimeError as exc:
            raised = True
            assert "scene 2" in str(exc)
        assert raised, "expected RuntimeError on mid-list tts failure"
        # No partial master: concat never ran, no narration.wav exists.
        assert concat_called["n"] == 0
        assert not (pathlib.Path(d) / "audio" / "narration.wav").exists()


# ======================================================================
# 4. Ranking
# ======================================================================
def test_rank_is_license_first_and_drops_rejects_and_offlist():
    cands = [
        acand("openverse_audio", "by track", "CC BY 4.0", author="A", dur=100),
        acand("openverse_audio", "cc0 track", "CC0 1.0", dur=90),
        acand("openverse_audio", "nc track", "CC BY-NC 4.0", dur=200),     # rejected
        acand("rogue_source", "off allowlist", "CC0 1.0", dur=300),        # dropped
    ]
    ranked = engine.rank_candidates(cands)
    assert [c.title for c in ranked] == ["cc0 track", "by track"]  # PD before BY; NC/off gone


def test_rank_prefers_longer_within_same_license():
    cands = [acand("openverse_audio", "short", "CC0 1.0", dur=30),
             acand("openverse_audio", "long", "CC0 1.0", dur=240)]
    assert [c.title for c in engine.rank_candidates(cands)][0] == "long"


# ======================================================================
# 5. source_bed
# ======================================================================
def test_source_bed_clears_cc0_with_attribution():
    client = FakeClient({"openverse_audio": [
        acand("openverse_audio", "Calm Bed", "CC0 1.0", author="Composer",
              license_url="https://creativecommons.org/publicdomain/zero/1.0/")]})
    with tempfile.TemporaryDirectory() as d:
        bed = engine.source_bed(STYLE, client=client, pdir=d, query="calm")
        assert bed["status"] == "cleared"
        t = bed["track"]
        assert t["role"] == "music" and t["status"] == "cleared"
        assert t["license"] and t["attribution"]          # license + attribution present
        assert t["ducking"] == "narration"
        assert (pathlib.Path(d) / bed["path"]).exists()   # downloaded LOCAL


def test_source_bed_skips_unattributable_by_then_placeholder():
    # An accept-licensed BY with no author -> can't complete attribution -> skipped ->
    # nothing else clears -> a flagged placeholder excluded from the master.
    client = FakeClient({"openverse_audio": [
        acand("openverse_audio", "Anon BY", "CC BY 4.0", author="")]})
    with tempfile.TemporaryDirectory() as d:
        bed = engine.source_bed(STYLE, client=client, pdir=d, query="x")
        assert bed["status"] == "placeholder"
        assert bed["path"] is None
        assert bed["track"]["status"] == "placeholder" and bed["track"].get("flag")
        assert (pathlib.Path(d) / bed["track"]["uri"]).exists()  # local silent stub


def test_source_bed_offline_is_placeholder_not_crash():
    with tempfile.TemporaryDirectory() as d:
        bed = engine.source_bed(STYLE, client=FakeClient({}), pdir=d, query="x")
        assert bed["status"] == "placeholder"


# ======================================================================
# 6. Signature SFX anchor
# ======================================================================
def test_signature_scene_and_at_sec():
    transcript = {"segments": [
        {"scene_no": 1, "start_sec": 0.0, "end_sec": 2.0},
        {"scene_no": 2, "start_sec": 2.0, "end_sec": 5.0},
        {"scene_no": 3, "start_sec": 5.0, "end_sec": 9.0}]}
    assert engine.signature_scene(STORYBOARD) == 2
    assert engine.signature_at_sec(transcript, 2) == 2.0   # the cut INTO scene 2
    assert engine.signature_scene({"scenes": []}) is None


def test_place_signature_sfx_anchors_on_the_cut(monkeypatch):
    transcript = {"segments": [
        {"scene_no": 1, "start_sec": 0.0, "end_sec": 2.0},
        {"scene_no": 2, "start_sec": 2.0, "end_sec": 5.0}]}
    monkeypatch.setattr(sfx_kit, "ensure_sfx",
                        lambda name, path, **k: (pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True),
                                                 pathlib.Path(path).write_bytes(b"sfx"),
                                                 {"ok": True, "path": str(path), "error": None})[-1])
    with tempfile.TemporaryDirectory() as d:
        sfx = engine.place_signature_sfx(STORYBOARD, transcript, STYLE, pdir=d)
        assert sfx is not None
        assert sfx["role"] == "sfx" and sfx["scene_no"] == 2 and sfx["at_sec"] == 2.0
        assert sfx["status"] == "cleared" and sfx["license_code"] == "cc0"
        # No signature beat -> omitted (silence beats a mis-placed hit).
        assert engine.place_signature_sfx({"scenes": []}, transcript, STYLE, pdir=d) is None


# ======================================================================
# 7. mix_audio end-to-end (the master-bridge, total agreement, enforcement)
# ======================================================================
def _mix(d, *, by_source=None, storyboard=None, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(sfx_kit, "ensure_sfx",
                            lambda name, path, **k: (pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True),
                                                     pathlib.Path(path).write_bytes(b"sfx"),
                                                     {"ok": True, "path": str(path), "error": None})[-1])
    transcript = engine.record_narration(SCRIPT, pdir=d, tts_fn=fake_tts,
                                         concat_fn=fake_concat)["transcript"]
    client = FakeClient(by_source or {})
    return engine.mix_audio(SCRIPT, STYLE, storyboard, transcript, pdir=d, client=client,
                            mood_query_fn=lambda sg, sc: "calm bed", mix_fn=fake_mix)


def test_mix_master_bridge_and_total_agreement(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        res = _mix(d, by_source={"openverse_audio": [
            acand("openverse_audio", "Bed", "CC0 1.0", author="C")]},
            storyboard=STORYBOARD, monkeypatch=monkeypatch)
        m = res["manifest"]
        narr = next(t for t in m["tracks"] if t["role"] == "narration")
        # The master-bridge: the muxed (narration) uri IS the master; VO back-referenced.
        assert narr["uri"] == "audio/master.wav" == m["master_uri"]
        assert narr["vo_uri"] == "audio/narration.wav" == m["vo_uri"]
        assert narr["ducking"] is False
        # THREE total_duration_sec agree: transcript, manifest, and the trim target.
        assert m["total_duration_sec"] == 9.0
        assert res["master_wav"] == "audio/master.wav"
        # bed ducked under VO; one sfx accent present and anchored.
        bed = next(t for t in m["tracks"] if t["role"] == "music")
        assert bed["ducking"] == "narration" and bed["status"] == "cleared"
        sfx = next(t for t in m["tracks"] if t["role"] == "sfx")
        assert sfx["at_sec"] == 2.0


def test_mix_placeholder_bed_in_manifest_but_not_in_master(monkeypatch):
    # Capture the recipe the engine builds so we can prove the placeholder isn't baked.
    seen = {}
    real_build = hf_audio.build_mix_recipe

    def spy_build(vo, total, **kw):
        recipe = real_build(vo, total, **kw)
        seen["inputs"] = recipe["inputs"]
        seen["bed"] = kw.get("bed")
        return recipe
    monkeypatch.setattr(hf_audio, "build_mix_recipe", spy_build)
    with tempfile.TemporaryDirectory() as d:
        res = _mix(d, by_source={}, storyboard=None, monkeypatch=monkeypatch)  # nothing clears
        m = res["manifest"]
        music = next(t for t in m["tracks"] if t["role"] == "music")
        assert music["status"] == "placeholder"            # IN the manifest, flagged
        assert seen["bed"] is None                         # but NOT passed to the mix
        assert not any("bed_placeholder" in p or "/bed." in p for p in seen["inputs"])


def test_mix_enforces_license_attribution_in_code(monkeypatch):
    # A cleared music track stripped of its attribution must be refused (not just schema).
    bad = {"schema_version": "1.1", "total_duration_sec": 5.0, "tracks": [
        {"role": "narration", "uri": "audio/master.wav", "gain_db": 0, "ducking": False},
        {"role": "music", "uri": "audio/bed.mp3", "gain_db": -20, "ducking": "narration",
         "status": "cleared", "license": "CC0 1.0", "attribution": ""}]}
    try:
        engine._enforce_clearance(bad)
        assert False, "expected RuntimeError on missing attribution"
    except RuntimeError as exc:
        assert "license+attribution" in str(exc)


# ======================================================================
# 8. build_mix_recipe (PURE — the documentary filtergraph)
# ======================================================================
def test_mix_recipe_vo_only():
    r = hf_audio.build_mix_recipe("vo.wav", 9.0, out_path="m.wav")
    assert r["inputs"] == ["vo.wav"]
    assert "sidechaincompress" not in r["filter_complex"]
    # No bed -> no asplit duck-key (a dangling [vokey] would crash FFmpeg); and with a
    # single source there is no amix (amix=inputs=1 is rejected by FFmpeg).
    assert "asplit" not in r["filter_complex"]
    assert "amix" not in r["filter_complex"]
    assert "atrim=0:9.0" in r["filter_complex"]          # trimmed to total


def test_mix_recipe_vo_sfx_no_bed():
    # VO + accent but no bed: 2 sources -> amix, still no duck key (no bed to duck).
    r = hf_audio.build_mix_recipe("vo.wav", 9.0, out_path="m.wav",
                                  sfx={"path": "sfx.wav", "gain_db": -8, "at_sec": 3.456})
    assert r["inputs"] == ["vo.wav", "sfx.wav"]
    assert "asplit" not in r["filter_complex"] and "sidechaincompress" not in r["filter_complex"]
    assert "amix=inputs=2" in r["filter_complex"]
    assert "adelay=3456|3456" in r["filter_complex"]


def test_mix_recipe_vo_bed_ducks():
    r = hf_audio.build_mix_recipe("vo.wav", 12.0, out_path="m.wav",
                                  bed={"path": "bed.mp3", "gain_db": -20})
    assert r["inputs"] == ["vo.wav", "bed.mp3"]
    assert "sidechaincompress" in r["filter_complex"]    # bed ducked under VO
    assert "-stream_loop" in " ".join(r["args"])         # bed looped to cover duration
    assert "amix=inputs=2" in r["filter_complex"]


def test_mix_recipe_vo_bed_sfx_delayed():
    r = hf_audio.build_mix_recipe("vo.wav", 12.0, out_path="m.wav",
                                  bed={"path": "bed.mp3", "gain_db": -20},
                                  sfx={"path": "sfx.wav", "gain_db": -8, "at_sec": 2.5})
    assert r["inputs"] == ["vo.wav", "bed.mp3", "sfx.wav"]
    assert "adelay=2500|2500" in r["filter_complex"]     # accent on the cut at 2.5s
    assert "amix=inputs=3" in r["filter_complex"]


def test_mix_recipe_loudness_normalized_to_target():
    # Every mix branch must END with a loudness-normalization stage so the master hits
    # the YouTube target (~-14 LUFS). Shipped masters measured ~-22 LUFS (8 too quiet);
    # the peak limiter alone never raised quiet content to a target loudness.
    for kw in ({},
               {"sfx": {"path": "s.wav", "gain_db": -8, "at_sec": 1.0}},
               {"bed": {"path": "b.mp3", "gain_db": -20}},
               {"bed": {"path": "b.mp3", "gain_db": -20},
                "sfx": {"path": "s.wav", "gain_db": -8, "at_sec": 2.5}}):
        r = hf_audio.build_mix_recipe("vo.wav", 9.0, out_path="m.wav", **kw)
        fc = r["filter_complex"]
        loud = (f"loudnorm=I={hf_audio.TARGET_LUFS}:TP={hf_audio.TARGET_TP}:"
                f"LRA={hf_audio.TARGET_LRA}")
        assert loud in fc, f"missing loudnorm-to-target for {kw}: {fc}"
        # loudnorm is the FINAL filter feeding [master] (final normalization wins).
        last_stage = fc.split("[master]")[0].split(",")[-1]
        assert last_stage.startswith("loudnorm"), f"loudnorm not last for {kw}: {last_stage}"
    assert hf_audio.TARGET_LUFS == -14.0   # YouTube integrated-loudness standard


def test_mix_recipe_vo_filters_default_is_unchanged():
    # The emotional-EQ hook is purely additive: absent vo_filters -> byte-identical graph.
    base = hf_audio.build_mix_recipe("vo.wav", 9.0, out_path="m.wav")
    same = hf_audio.build_mix_recipe("vo.wav", 9.0, out_path="m.wav", vo_filters="")
    assert base["filter_complex"] == same["filter_complex"]


def test_mix_recipe_vo_filters_color_the_audible_vo_not_the_duck_key():
    fx = "equalizer=f=180:t=q:w=1:g=2,aecho=0.8:0.7:45:0.18"
    r = hf_audio.build_mix_recipe("vo.wav", 12.0, out_path="m.wav",
                                  bed={"path": "bed.mp3", "gain_db": -20}, vo_filters=fx)
    fc = r["filter_complex"]
    # the EQ rides on the audible VO chain (…volume…dB,<fx>[voout])
    assert f"volume=0.0dB,{fx}[voout]" in fc
    # …but the sidechain duck KEY stays the clean, un-effected VO
    assert "[vokey]" in fc and f"[vokey]{fx}" not in fc


# ======================================================================
# 8b. narrative intent -> audio params (the emotional score, made audible)
# ======================================================================
_INTENT = {
    "video_level": {"tone_profile": "dark_warning"},
    "emotional_arc": {"peak": {"dominant_emotion": "awe"}},
    "per_scene_intent": [
        {"scene_index": 0, "pacing_directive": "punchy_staccato",
         "primary_emotion": "curiosity", "texture_directive": "clean_high_contrast"},
        {"scene_index": 1, "pacing_directive": "contemplative",
         "primary_emotion": "awe", "texture_directive": "cinematic_widescreen"},
        {"scene_index": 2, "pacing_directive": "driving",
         "primary_emotion": "awe", "texture_directive": "cinematic_widescreen"}],
}


def test_scene_speeds_map_pacing_to_tts_speed():
    speeds = engine.scene_speeds(SCRIPT, _INTENT)
    assert speeds[1] > 1.0          # punchy_staccato -> faster
    assert speeds[2] < 1.0          # contemplative -> slower
    assert speeds[3] > 1.0          # driving -> faster
    # unscored script -> every scene at 1.0 (unchanged)
    assert set(engine.scene_speeds(SCRIPT, None).values()) == {1.0}


def test_dominant_emotion_and_texture_are_modal():
    assert engine.dominant_emotion(_INTENT) == "awe"          # 2/3 scenes
    assert engine.dominant_texture(_INTENT) == "cinematic_widescreen"
    assert engine.dominant_emotion(None) is None
    assert engine.dominant_texture(None) is None


def test_voice_fx_and_texture_maps_are_closed_and_safe():
    assert "aecho" in engine.voice_fx_for("awe")              # awe gets a hint of room
    assert engine.voice_fx_for("not_an_emotion") == ""        # unknown -> neutral
    assert "instrumental" in engine.texture_music_query("cinematic_widescreen")
    assert engine.texture_music_query("nope") is None
    assert engine.texture_sfx_name("cinematic_widescreen") == "whoosh"
    assert engine.texture_sfx_name("nope") is None


def test_record_narration_varies_speed_per_scene_from_intent():
    captured = {}

    def speedy_tts(text, out, speed=1.0):
        n = int(pathlib.Path(out).stem.split("-")[1])
        captured[n] = speed
        p = pathlib.Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RIFFstub")
        return {"ok": True, "duration": _DURS[n], "output": str(out), "error": None}

    with tempfile.TemporaryDirectory() as d:
        engine.record_narration(SCRIPT, pdir=d, narrative_intent=_INTENT,
                                tts_fn=speedy_tts, concat_fn=fake_concat)
    assert captured[1] > 1.0 and captured[2] < 1.0 and captured[3] > 1.0


def test_record_narration_tolerates_a_legacy_two_arg_tts():
    # An old (text, out) seam must still work (speed applied only to speed-aware seams).
    with tempfile.TemporaryDirectory() as d:
        out = engine.record_narration(SCRIPT, pdir=d, narrative_intent=_INTENT,
                                      tts_fn=fake_tts, concat_fn=fake_concat)
    assert out["total_duration_sec"] == 9.0


def test_mix_records_the_emotional_score_decisions(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(sfx_kit, "ensure_sfx",
                            lambda name, path, **k: (pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True),
                                                     pathlib.Path(path).write_bytes(b"sfx"),
                                                     {"ok": True, "path": str(path), "error": None})[-1])
        transcript = engine.record_narration(SCRIPT, pdir=d, tts_fn=fake_tts,
                                             concat_fn=fake_concat)["transcript"]
        client = FakeClient({})
        manifest = engine.mix_audio(SCRIPT, STYLE, STORYBOARD, transcript, pdir=d,
                                    client=client, narrative_intent=_INTENT, mix_fn=fake_mix)["manifest"]
    score = manifest["mix"]["emotional_score"]
    assert score["dominant_emotion"] == "awe"
    assert score["dominant_texture"] == "cinematic_widescreen"
    assert score["vo_filters"] and "aecho" in score["vo_filters"]
    # the texture drives the bed query + the signature accent
    assert "cinematic" in score["music_query"]
    assert manifest["mix"]["sfx"] == "whoosh"


# ======================================================================
# 9. sfx_kit
# ======================================================================
def test_sfx_kit_recipe_and_defaults():
    r = sfx_kit.build_sfx_recipe("stamp", "out.wav")
    assert "lavfi" in r["args"] and r["name"] == "stamp"
    assert sfx_kit.default_sfx_for({"reference_note": "editorial / paper"}) == "page-turn"
    assert sfx_kit.default_sfx_for({"dos": ["kinetic motion sweeps"]}) == "whoosh"
    assert sfx_kit.default_sfx_for({}) == "stamp"
    assert sfx_kit.provenance("stamp")["license_code"] == "cc0"


# ======================================================================
# 10. audio_sources parsers (canned dicts; no network)
# ======================================================================
def test_openverse_audio_parser():
    data = {"results": [{"title": "T", "creator": "C", "license": "by-sa",
                         "license_version": "4.0", "url": "https://x/a.mp3",
                         "foreign_landing_url": "https://x/page", "duration": 120000,
                         "filetype": "mp3"}]}
    [c] = audio_sources._openverse_parse(data)
    assert c.source == "openverse_audio" and c.license_raw == "cc-by-sa 4.0"
    assert c.duration == 120.0 and engine.is_acceptable(c.license_raw)


def test_freesound_parser_and_keyed_availability():
    data = {"results": [{"name": "click", "username": "u", "license":
                         "http://creativecommons.org/publicdomain/zero/1.0/",
                         "previews": {"preview-hq-mp3": "https://f/cl.mp3"},
                         "duration": 0.4, "url": "https://f/page"}]}
    [c] = audio_sources._freesound_parse(data)
    assert c.source == "freesound" and c.kind == "sfx"
    assert engine.normalize_license(c.license_raw) == "cc0"
    fs = audio_sources.SOURCE_BY_NAME["freesound"]
    assert FakeClient().available(fs) is False     # keyed source absent -> skipped


def test_internet_archive_parser_defers_download_url():
    data = {"response": {"docs": [{"identifier": "id1", "title": "T", "creator": "C",
                                   "licenseurl": "https://creativecommons.org/licenses/by/4.0/"}]}}
    [c] = audio_sources._ia_parse(data)
    assert c.source == "internet_archive_audio" and c.download_url == ""
    assert c.extra["identifier"] == "id1"


# ======================================================================
# 11. Contract validity — emitted artifacts validate; 1.0 stub still validates on 1.1
# ======================================================================
def test_emitted_transcript_and_manifest_validate(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        out = engine.record_narration(SCRIPT, pdir=d, tts_fn=fake_tts, concat_fn=fake_concat)
        transcript = {"schema_version": "1.0", **out["transcript"]}
        ok, errs = contracts.validate("narration_transcript", transcript)
        assert ok, errs
        res = _mix(d, by_source={"openverse_audio": [
            acand("openverse_audio", "Bed", "CC0 1.0", author="C")]},
            storyboard=STORYBOARD, monkeypatch=monkeypatch)
        manifest = {"schema_version": contracts.version_for("audio_manifest"), **res["manifest"]}
        ok, errs = contracts.validate("audio_manifest", manifest)
        assert ok, errs
        assert manifest["schema_version"] == "1.1"


def test_v1_0_stub_manifest_still_validates_on_1_1_schema():
    """The crux of the version-awareness finding: validate() loads the LATEST schema, so
    the 1.1 additions MUST be optional — a 1.0 stub-shaped manifest still validates."""
    stub = {"schema_version": "1.0", "total_duration_sec": 24.0, "tracks": [
        {"role": "narration", "uri": "audio/narration.wav", "gain_db": 0.0, "ducking": False},
        {"role": "music", "uri": "audio/bed.mp3", "gain_db": -18.0, "ducking": True}]}
    ok, errs = contracts.validate("audio_manifest", stub)
    assert ok, errs


def test_words_enrichment_validates_on_transcript_schema():
    tr = {"schema_version": "1.0", "total_duration_sec": 2.0, "segments": [
        {"scene_no": 1, "start_sec": 0.0, "end_sec": 2.0, "text": "hi there",
         "words": [{"start": 0.0, "end": 0.5, "text": "hi"},
                   {"start": 0.5, "end": 2.0, "text": "there"}]}]}
    ok, errs = contracts.validate("narration_transcript", tr)
    assert ok, errs


# ======================================================================
# Runner (works under pytest OR standalone, mirroring the siblings)
# ======================================================================
def _run_all():
    class _MP:
        def __init__(self):
            self._undo = []
        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, old in reversed(self._undo):
                setattr(obj, name, old)
            self._undo = []

    passed = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and isinstance(fn, types.FunctionType)):
            continue
        mp = _MP()
        try:
            if fn.__code__.co_argcount == 1:
                fn(mp)
            else:
                fn()
            print(f"  ✓ {name}")
            passed += 1
        finally:
            mp.undo()
    print(f"\n{passed} tests passed (network/toolchain off).")


if __name__ == "__main__":
    _run_all()


# --- TTS concurrency must be CPU-bounded (regression: 8-wide pool blew per-call timeouts) ---
def test_tts_workers_bounded_by_cores():
    import audio_engine as ae
    # never exceed the scene count, half the cores, or the hard ceiling of 3
    cores = __import__("os").cpu_count() or 2
    for n in (1, 2, 5, 12):
        w = ae._tts_workers(n)
        assert 1 <= w <= 3
        assert w <= n
        assert w <= max(1, cores // 2)
