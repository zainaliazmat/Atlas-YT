"""studio.vo — Kokoro VO + the VO-lock re-timer bridge (Phase 4).

Produces the narration the whole composition is timed against, and converts the
real per-scene VO durations into the ``NS``/``ND`` arrays the Composer's VO-lock
re-timer needs (GOLDEN_REFERENCE.md §2). VO timing is authoritative: the
composition conforms to it, so dead air dies automatically.

REUSE, don't rebuild (REUSE_MAP.md §4): the TTS/concat/transcribe TOOLCHAIN is the
proven ``audio-designer/hf_audio.py`` — ``tts`` (Kokoro 24 kHz mono), ``concat_wavs``
(lossless concat-demuxer), ``transcribe`` (optional whisper word-level),
``probe_duration``. studio.vo WRAPS these through the isolated engine loader
(``studio.engines.audio_hf``) and never forks them. What studio.vo ADDS on top:

  - a voice AUDITION (am_onyx / am_michael / bm_george) that picks an authoritative
    default (am_onyx — a deep American male) — the first candidate that synthesizes;
  - per-scene clips named ``s1..sN.wav`` (matching the golden reference) at a calm
    ~0.95 base speed, with a per-scene SPEED BUMP when a clip overruns its authored
    window so the narration still fits the intended pacing;
  - the VO-DRIVEN GRID: ``NS`` = the prefix-sum of the (3-dp) clip durations — clips
    placed back-to-back, so there is **zero silent gap** between narration — and
    ``ND`` = clip + a ~0.4 s tail, so each scene window overlaps the next start and
    the transition has both scenes live across the seam (reproduces the reference);
  - a stitched ``vo.mp3`` and a word-level ``vo.words.json`` (real whisper when the
    binary is present, else a deterministic word spread across each scene's window);
  - the audio LAYOUT: VO on ALTERNATING track indices (9, 10, 9, …) so adjacent VO
    can overlap during a transition, a mood-matched bed ducked under the VO on its
    own track, and SFX hits on the transition beats.

Sibling engines and ``studio.library`` are imported LAZILY inside functions (never
at module scope) so ``import studio`` stays cheap and side-effect free.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .. import config

# --- policy (studio owns these; hf_audio owns the toolchain) -----------------
DEFAULT_SPEED = 0.95          # a calm, authoritative base read
SCENE_TAIL = 0.4              # window padding past the VO so transitions overlap
SPEED_MIN, SPEED_MAX = 0.80, 1.30
VO_TRACKS = (9, 10)           # adjacent VO alternates so it overlaps across a seam
BED_TRACK = 8                 # the music bed on its own track, below the VO
SFX_TRACK = 7                 # the thin accent track
BED_VOLUME = 0.18             # the bed sits well under the VO (≈ −15 dB), ducked
WHOOSH_DUR = 0.6

# The authority order: the first that synthesizes becomes the authoritative default.
AUDITION_VOICES = ("am_onyx", "am_michael", "bm_george")
AUDITION_SAMPLE = "Before this video ends, you will unlock your phone four more times."

AUDIO_REL = "assets/audio"
VO_REL = "assets/audio/vo"

_DEFAULT = object()  # "use the real toolchain"; None means "disabled" (e.g. no whisper)


# ======================================================================
# 1. The pure VO-lock grid math — NS/ND from the real clip durations
# ======================================================================
def retimer_windows(vo_durs, *, old_grid=None, tail: float = SCENE_TAIL) -> dict:
    """Convert real per-scene VO clip durations into the re-timer arrays.

    ``NS`` (new starts) is the prefix-sum of the 3-dp-rounded clip durations — every
    scene starts exactly when the previous clip ends, so the VO tiles the timeline
    with no silent gap. ``ND`` (new durations) is each clip + ``tail`` so the scene
    window runs a hair past the next clip's start and the transition overlaps the
    seam. ``OS``/``OD`` are the authored nominal grid (``old_grid``; defaults to the
    clip durations themselves, i.e. an identity remap). Returns
    ``{"OS", "OD", "NS", "ND", "total"}``.
    """
    clips = [round(float(d), 3) for d in vo_durs]
    n = len(clips)
    if old_grid is None:
        old_durs = clips[:]
    else:
        old_durs = [float(d) for d in old_grid][:n]
        while len(old_durs) < n:
            old_durs.append(clips[len(old_durs)])

    OS, t = [], 0.0
    for d in old_durs:
        OS.append(round(t, 3))
        t += d
    NS, t = [], 0.0
    for d in clips:
        NS.append(round(t, 3))
        t += d
    ND = [round(d + tail, 3) for d in clips]
    total = round((NS[-1] + ND[-1]), 3) if NS else 0.0
    return {"OS": OS, "OD": [round(d, 3) for d in old_durs],
            "NS": NS, "ND": ND, "total": total}


# ======================================================================
# 2. The voice audition — am_onyx is the authoritative default
# ======================================================================
def audition_voice(adir, *, voices=AUDITION_VOICES, sample_text: str = AUDITION_SAMPLE,
                   speed: float = DEFAULT_SPEED, tts_fn=None) -> str:
    """Synthesize a short audition sample with each candidate in authority order and
    return the FIRST that renders cleanly — the authoritative default. Samples are
    written to ``<adir>/audition/<voice>.wav`` so the pick is inspectable."""
    tts_fn = tts_fn or _default_tts()
    adir = Path(adir)
    for v in voices:
        out = adir / "audition" / f"{v}.wav"
        res = _call_tts(tts_fn, sample_text, out, v, speed)
        if res.get("ok"):
            return v
    return voices[0]


# ======================================================================
# 3. record_vo — per-scene VO + stitched vo.mp3 + word-level transcript + grid
# ======================================================================
def record_vo(script: dict, pdir, *, voice: str | None = None,
              speed: float = DEFAULT_SPEED, old_grid=None,
              tts_fn=None, concat_fn=None, encode_fn=None,
              transcribe_fn=_DEFAULT, audition_fn=None) -> dict:
    """Synthesize ``s1..sN.wav`` (at the audition voice, ~0.95, bumped on overrun),
    stitch ``vo.mp3``, write the word-level ``vo.words.json``, and compute the
    VO-driven grid. Seams (tts/concat/encode/transcribe/audition) are injected so the
    unit suite runs with no toolchain. Returns the VO record dict (see module docstring)."""
    scenes = [s for s in script.get("scenes", [])
              if isinstance(s, dict) and str(s.get("narration", "")).strip()]
    if not scenes:
        raise ValueError("script has no narratable scenes — nothing to voice")

    tts_fn = tts_fn or _default_tts()
    concat_fn = concat_fn or _default_concat()
    encode_fn = encode_fn or _default_encode()
    transcribe_fn = _resolve_transcribe(transcribe_fn)

    pdir = Path(pdir)
    adir = pdir / AUDIO_REL
    vodir = pdir / VO_REL
    vodir.mkdir(parents=True, exist_ok=True)

    if voice is None:
        voice = (audition_fn or audition_voice)(adir, tts_fn=tts_fn)

    if old_grid is None:
        old_grid = [max(2.0, float(s.get("duration_est_sec") or 6)) for s in scenes]

    vo_durs: list[float] = []
    scene_recs: list[dict] = []
    scene_wavs: list[Path] = []
    for i, s in enumerate(scenes):
        n = s.get("scene_no") if isinstance(s.get("scene_no"), int) else i + 1
        text = str(s["narration"]).strip()
        window = float(old_grid[i]) if i < len(old_grid) else 6.0
        out = vodir / f"s{i + 1}.wav"
        used_speed = speed
        res = _call_tts(tts_fn, text, out, voice, used_speed)
        if not res.get("ok"):
            raise RuntimeError(f"tts failed on scene {n}: {res.get('error')}")
        dur = float(res["duration"])
        # Per-scene overrun bump: if the clip runs past its authored window, speed up
        # once (proportionally, clamped) and re-synthesize so it fits the pacing.
        if dur > window and used_speed < SPEED_MAX:
            bumped = min(SPEED_MAX, round(used_speed * (dur / window), 3))
            if bumped > used_speed + 1e-3:
                res2 = _call_tts(tts_fn, text, out, voice, bumped)
                if res2.get("ok"):
                    used_speed, dur = bumped, float(res2["duration"])
        vo_durs.append(dur)
        scene_wavs.append(out)
        scene_recs.append({
            "scene_no": int(n), "src": f"{VO_REL}/s{i + 1}.wav",
            "vo_dur": round(dur, 3), "speed": used_speed,
            "narration": text, "on_screen_text": str(s.get("on_screen_text", "")),
            "track_index": VO_TRACKS[i % 2],
        })

    grid = retimer_windows(vo_durs, old_grid=old_grid)

    # Stitch: lossless concat -> a temp wav (also the transcribe source) -> vo.mp3.
    narration_wav = adir / "vo.wav"
    cat = concat_fn([str(p) for p in scene_wavs], str(narration_wav))
    if not cat.get("ok"):
        raise RuntimeError(f"could not concat the scene VO: {cat.get('error')}")
    vo_mp3 = adir / "vo.mp3"
    enc = encode_fn(str(narration_wav), str(vo_mp3))
    if not enc.get("ok"):
        raise RuntimeError(f"could not encode vo.mp3: {enc.get('error')}")

    words = _word_transcript(scene_recs, grid, narration_wav, transcribe_fn)
    (adir / "vo.words.json").write_text(json.dumps(words, indent=2, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
    # the reference ships only per-scene wavs + vo.mp3; drop the intermediate wav
    try:
        narration_wav.unlink()
    except OSError:
        pass

    return {
        "voice": voice,
        "vo_durs": [round(d, 3) for d in vo_durs],
        "scenes": scene_recs,
        "grid": grid,
        "vo_mp3": f"{AUDIO_REL}/vo.mp3",
        "words_json": f"{AUDIO_REL}/vo.words.json",
        "total_duration_sec": grid["total"],
    }


def _word_transcript(scene_recs, grid, narration_wav, transcribe_fn) -> list[dict]:
    """Word-level transcript on the GLOBAL timeline. Real whisper word timings when
    the binary is present; otherwise a deterministic spread of each scene's words
    across that scene's VO window (so vo.words.json always exists and stays usable)."""
    if transcribe_fn is not None:
        try:
            res = transcribe_fn(str(narration_wav))
            if res.get("ok"):
                ws = _flatten_words(res.get("data") or {})
                if ws:
                    return [{"id": f"w{i}", "text": w["text"],
                             "start": w["start"], "end": w["end"]}
                            for i, w in enumerate(ws)]
        except Exception:  # noqa: BLE001 — enrichment must never break the job
            pass
    NS = grid["NS"]
    out: list[dict] = []
    wid = 0
    for i, sr in enumerate(scene_recs):
        toks = str(sr["narration"]).split()
        if not toks:
            continue
        start, dur = NS[i], sr["vo_dur"]
        step = dur / len(toks)
        for j, tok in enumerate(toks):
            out.append({"id": f"w{wid}", "text": tok,
                        "start": round(start + j * step, 3),
                        "end": round(start + (j + 1) * step, 3)})
            wid += 1
    return out


def _flatten_words(data: dict) -> list[dict]:
    """Best-effort {text,start,end} extraction from a transcribe JSON payload."""
    out = []
    segs = data.get("segments") or data.get("words") or []
    for s in segs:
        items = s.get("words") if isinstance(s, dict) and s.get("words") else [s]
        for w in items:
            if not isinstance(w, dict):
                continue
            st, en = w.get("start"), w.get("end")
            tok = w.get("word") or w.get("text")
            if isinstance(st, (int, float)) and isinstance(en, (int, float)) and tok:
                out.append({"text": str(tok).strip(),
                            "start": round(float(st), 3), "end": round(float(en), 3)})
    return out


# ======================================================================
# 4. mix — mood bed ducked under VO + SFX on the transition beats
# ======================================================================
_CUE_KEYWORDS = [
    (("slot", "feed", "machine", "scroll", "endless"), "slot-reel"),
    (("glitch", "split", "rgb", "static", "broken"), "glitch"),
    (("designed", "product", "engineer", "stamp", "build"), "stamp"),
    (("unlock", "notification", "badge", "bell", "ping", "alert", "red"), "chime"),
]
_CUE_PROFILE = {  # name -> (duration, volume)
    "slot-reel": (3.7, 0.4), "glitch": (1.8, 0.38),
    "stamp": (0.6, 0.5), "chime": (1.1, 0.5),
}


def mix(script: dict, vo_result: dict, pdir, *, pack=None,
        obtain_fn=None, materialize_fn=None) -> dict:
    """Source a mood-matched bed (ducked under the VO on its own track) and place SFX
    on the transition beats: a whoosh on every seam, plus a content cue (slot-reel /
    glitch / stamp / chime) on the scene whose script calls for it. Returns
    ``{"audio": [rows], "bed": status, "sfx_names": [...]}``; rows are ``<audio>``
    descriptors the Composer embeds verbatim. Reuses ``studio.library.obtain``."""
    obtain_fn = obtain_fn or _default_obtain()
    materialize_fn = materialize_fn or _default_materialize(pdir)
    grid = vo_result["grid"]
    NS, total = grid["NS"], grid["total"]
    scenes = vo_result.get("scenes") or []

    rows: list[dict] = []
    # --- mood-matched bed, ducked under the VO ---
    bed = obtain_fn("music", ["bed"], {"mood": _pack_mood(pack)})
    bed_rel = materialize_fn(bed) if bed else None
    bed_status = "none"
    if bed_rel:
        rows.append({"role": "music", "src": bed_rel, "start": 0, "dur": total,
                     "track": BED_TRACK, "volume": BED_VOLUME})
        bed_status = "cleared"

    # --- one content cue per scene whose script calls for it ---
    # Cue off the authoritative script text (narration + on-screen), keyed by scene_no,
    # falling back to whatever the VO record carries. Build these FIRST so a cue landing
    # on a scene start can claim that beat (no doubled accent on the same track).
    sfx_names: list[str] = []
    cue_rows: list[dict] = []
    script_text = {sc.get("scene_no"): f"{sc.get('narration', '')} {sc.get('on_screen_text', '')}"
                   for sc in script.get("scenes", []) if isinstance(sc, dict)}
    for i, sr in enumerate(scenes):
        no = sr.get("scene_no")
        text = script_text.get(no) or f"{sr.get('narration', '')} {sr.get('on_screen_text', '')}"
        cue = _content_cue(text)
        if not cue:
            continue
        ref = obtain_fn("sfx", [cue])
        rel = materialize_fn(ref) if ref else None
        if rel:
            dur, vol = _CUE_PROFILE.get(cue, (0.6, 0.45))
            cue_rows.append({"role": "sfx", "src": rel, "start": NS[i], "dur": dur,
                             "track": SFX_TRACK, "volume": vol, "scene_no": no})
            sfx_names.append(cue)
    cue_starts = {round(r["start"], 3) for r in cue_rows}

    # --- a whoosh on every transition seam that a content cue hasn't already claimed ---
    whoosh = obtain_fn("sfx", ["whoosh"])
    whoosh_rel = materialize_fn(whoosh) if whoosh else None
    for b in range(1, len(NS)):
        if whoosh_rel and round(NS[b], 3) not in cue_starts:
            rows.append({"role": "sfx", "src": whoosh_rel, "start": NS[b],
                         "dur": WHOOSH_DUR, "track": SFX_TRACK, "volume": 0.5})
            sfx_names.append("whoosh")
    rows += cue_rows

    return {"audio": rows, "bed": bed_status, "sfx_names": sfx_names}


def _content_cue(text: str) -> str | None:
    t = (text or "").lower()
    for needles, name in _CUE_KEYWORDS:
        if any(k in t for k in needles):
            return name
    return None


# ======================================================================
# 5. produce_vo — the orchestrator: artifacts + vo.grid.json (no silent gaps)
# ======================================================================
def produce_vo(script: dict, pdir, *, pack=None, voice: str | None = None,
               tts_fn=None, concat_fn=None, encode_fn=None, transcribe_fn=_DEFAULT,
               obtain_fn=None, materialize_fn=None) -> dict:
    """Run record_vo + mix, assemble the combined audio manifest (VO on alternating
    tracks + bed + SFX), and persist ``vo.grid.json`` — the single artifact the
    Composer reads to conform the composition to the real VO."""
    rec = record_vo(script, pdir, voice=voice, tts_fn=tts_fn, concat_fn=concat_fn,
                    encode_fn=encode_fn, transcribe_fn=transcribe_fn)
    m = mix(script, rec, pdir, pack=pack, obtain_fn=obtain_fn,
            materialize_fn=materialize_fn)

    NS = rec["grid"]["NS"]
    vo_rows = [{"role": "vo", "src": sr["src"], "start": NS[i], "dur": sr["vo_dur"],
                "track": sr["track_index"], "volume": 1.0, "scene_no": sr["scene_no"]}
               for i, sr in enumerate(rec["scenes"])]

    manifest = {
        "schema_version": "studio-vo-1",
        "voice": rec["voice"],
        "grid": rec["grid"],
        "scenes": rec["scenes"],
        "vo_mp3": rec["vo_mp3"],
        "words_json": rec["words_json"],
        "total_duration_sec": rec["total_duration_sec"],
        "audio": vo_rows + m["audio"],
        "bed": m["bed"],
        "sfx": m["sfx_names"],
    }
    _write_json(Path(pdir) / "vo.grid.json", manifest)
    return manifest


# ======================================================================
# helpers: default (real) toolchain seams + small fs utilities
# ======================================================================
def _call_tts(tts_fn, text, out, voice, speed) -> dict:
    try:
        return tts_fn(text, out, voice=voice, speed=speed)
    except TypeError:
        return tts_fn(text, out)


def _default_tts():
    from .. import engines
    hf = engines.audio_hf()

    def _t(text, out, *, voice="am_onyx", speed=1.0):
        return hf.tts(text, out, voice=voice, speed=speed)
    return _t


def _default_concat():
    from .. import engines
    hf = engines.audio_hf()

    def _c(wavs, out):
        return hf.concat_wavs(wavs, out)
    return _c


def _default_encode():
    def _e(in_path, out_path):
        ff = shutil.which("ffmpeg")
        if ff is None:
            return {"ok": False, "output": None, "error": "ffmpeg not found"}
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        args = [ff, "-y", "-i", str(in_path), "-codec:a", "libmp3lame",
                "-q:a", "2", str(out_path)]
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=600)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {"ok": False, "output": None, "error": str(exc)}
        if p.returncode != 0 or not Path(out_path).exists():
            return {"ok": False, "output": None, "error": (p.stderr or "")[:300]}
        return {"ok": True, "output": str(out_path), "error": None}
    return _e


def _resolve_transcribe(t):
    if t is _DEFAULT:
        from .. import engines
        hf = engines.audio_hf()
        return lambda wav: hf.transcribe(wav)
    return t


def _default_obtain():
    def _o(kind, tags, constraints=None):
        from ..library import generate
        try:
            return generate.obtain(kind, tags, constraints)
        except Exception:  # noqa: BLE001 — a missing asset is non-fatal
            return None
    return _o


def _default_materialize(pdir):
    def _m(ref):
        return _materialize_asset(pdir, ref)
    return _m


def _materialize_asset(pdir, ref) -> str | None:
    """Copy a library audio asset into the project ``assets/audio/`` and return its
    composition-relative path (HyperFrames forbids render-time fetches)."""
    if ref is None:
        return None
    entry = getattr(ref, "entry", None) or {}
    rel = entry.get("file")
    if not rel:
        return None
    src = config.ASSET_LIBRARY_DIR / rel
    if not src.is_file():
        return None
    dest_dir = Path(pdir) / AUDIO_REL
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(src).name
    if not dest.exists():
        shutil.copyfile(src, dest)
    return f"{AUDIO_REL}/{dest.name}"


def _pack_mood(pack):
    try:
        sig = pack.manifest.get("audio", {}) if pack is not None else {}
        mood = sig.get("bed_mood") or sig.get("mood")
        if mood:
            return mood if isinstance(mood, list) else [mood]
    except Exception:  # noqa: BLE001
        pass
    return ["dark", "hopeful"]


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
