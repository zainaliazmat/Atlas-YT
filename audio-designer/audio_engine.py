"""Cadence's engine: script.json (+ style_guide / storyboard) -> the audio trio.

Cadence owns three artifacts and produces them in two jobs:

  record_narration  -> narration.wav  +  narration.transcript.json
  mix_audio         -> master.wav (the documentary mix)  +  audio_manifest.json

THE SPLIT (mirrors the siblings):
- The TOOLCHAIN lives behind thin seams: hf_audio.py (tts/transcribe/ffmpeg) and
  audio_sources.py (the network SourceClient). sfx_kit.py synthesizes the CC0 accent.
- The BRAIN here is DETERMINISTIC in everything that touches the MIX — gains, ducking,
  the manifest, the master render, the scene-offset math, the license truth table.
  The ONLY LLM-assisted step is curation (mood -> music search query); it degrades to
  a deterministic fallback when no brain is reachable, so the engine runs fully
  offline. Every decision is unit-testable with injected seams, network/render OFF.

THE DISCIPLINE (documentary mix):
- The VO is authoritative. The transcript describes the pure VO (narration.wav) and is
  the downstream timing authority (the Composition Engineer prefers its segment span).
- A music bed is sourced from an allowlist of CC/PD archives, license-cleared the same
  way the Asset Sourcer clears images, and ducked HARD under the VO. Nothing uncleared
  is ever baked into the master.
- One signature SFX accent lands on the cut into the storyboard's signature-beat scene.

THE MASTER-BRIDGE (decoupling, no Composition Engineer edits): the renderer
(`hf_tools.assemble_final`) muxes `tracks[role=="narration"].uri`. So the narration
track's `uri` points at the MIX (master.wav) and the documentary mix lands in the
final MP4 today; `vo_uri` + `master_uri` back-reference the pure VO and the mix so the
clean follow-up (Mason muxes `master_uri`, narration uri back to pure VO) is a
Mason-only change with no further schema churn.

Decoupling boundary: this engine emits plain dicts and NEVER imports atlas. Atlas
stamps `schema_version` (via contracts.version_for) and validates against the frozen
contract at the boundary. `record_narration(...)` / `mix_audio(...)` are the pure
seams the adapter uses; `run_*(...)` are the CLI/chat conveniences.
"""
from __future__ import annotations

import concurrent.futures as _futures
import os
import pathlib
import re
import time
import wave


def _tts_workers(n_scenes: int) -> int:
    """How many scenes to synthesize at once. Kokoro TTS is CPU-bound (and onnxruntime
    multithreads internally), so oversubscribing the box makes EVERY per-call `hyperframes
    tts` slower and can blow its per-call timeout. Bound concurrency to half the cores
    (hard ceiling 3) so we get a real speedup without starving each synth. Determinism is
    independent of this number — offsets/concat are computed in original scene order."""
    cores = os.cpu_count() or 2
    return max(1, min(n_scenes, cores // 2, 3))

import chat_state
import llm                # imported eagerly (the loader-safe sibling pattern) so the
                          # isolation loader binds Cadence's OWN llm, not another agent's
import audio_sources
import hf_audio
import sfx_kit
from audio_sources import (ALLOWLIST_NAMES, SOURCE_BY_NAME, SOURCE_ORDER, SOURCES,
                           AudioCandidate)

HERE = pathlib.Path(__file__).parent
SOUL = (HERE / "soul" / "SOUL.md").read_text()
SKILL = (HERE / "SKILL.md").read_text() if (HERE / "SKILL.md").exists() else ""
MEMORY = HERE / "memory.json"
MIXES_DIR = HERE / "mixes"

# Cadence ADDED license/attribution/status + master_uri/vo_uri/scene anchors to the
# audio_manifest under a bumped schema_version. Atlas is the authority (stamps via
# contracts.version_for at the boundary); this local copy keeps a standalone run
# contract-shaped. The transcript stays on the base version.
MANIFEST_SCHEMA_VERSION = "1.1"
TRANSCRIPT_SCHEMA_VERSION = "1.0"

AUDIO_SUBDIR = "audio"
VOICE_DEFAULT = "af_heart"

# --- The mix POLICY (the engine owns these values; hf_audio owns the filtergraph) ---
VO_GAIN_DB = 0.0       # the VO is the reference — authoritative, un-attenuated
BED_GAIN_DB = -20.0    # the bed sits well under the VO before sidechain ducking
SFX_GAIN_DB = -8.0     # the one accent: present on the cut, never competing with VO


# ======================================================================
# 1. License normalization + truth table (audio variant of the Sourcer's policy)
# ======================================================================
_ACCEPT_PD = {"cc0", "pdm", "pd"}
_ACCEPT_BY = {"by", "by-sa"}


def _has(token: str, s: str) -> bool:
    return re.search(rf"(^|[ \-/]){token}([ \-/]|$)", s) is not None


def normalize_license(raw: str) -> str:
    """Map a verbatim license string/URL to a canonical code (lowercased).

    Worldwide PD accepts (CC0, Public Domain Mark, plain "public domain"). CC-BY /
    CC-BY-SA accept (attribution required). NC/ND, "no known restrictions", NoC-US,
    Sampling+, unknown/missing -> reject. Provenance uncertainty is disqualifying.
    """
    s = (raw or "").strip().lower()
    if not s:
        return "unknown"
    if "noc-us" in s or ("no copyright" in s and "united states" in s):
        return "noc-us"
    if ("no known copyright" in s or "no known restriction" in s
            or "flickr commons" in s):
        return "no-known"
    if "sampling" in s:           # CC "Sampling+" is NOT a clean reuse license
        return "sampling"
    if "publicdomain/zero" in s or s in ("cc0", "cc-0") or "cc0" in s:
        return "cc0"
    if "publicdomain/mark" in s or "public domain mark" in s or s == "pdm":
        return "pdm"
    if ("creativecommons.org/licenses" in s or s.startswith("cc-") or s.startswith("cc ")
            or _has("by", s)):
        has_by = _has("by", s) or "licenses/by" in s
        has_nc = _has("nc", s) or "-nc" in s
        has_nd = _has("nd", s) or "-nd" in s
        has_sa = _has("sa", s) or "-sa" in s
        if has_nc or has_nd:
            parts = ["by"] + (["nc"] if has_nc else []) + (["nd"] if has_nd else []) \
                + (["sa"] if has_sa and not has_nd else [])
            return "-".join(parts)
        if has_sa:
            return "by-sa"
        if has_by:
            return "by"
    if "public domain" in s or s == "pd":
        return "pd"
    if "all rights reserved" in s or "©" in s:
        return "arr"
    return "unknown"


class Disposition:
    __slots__ = ("verdict", "requires_attribution", "share_alike", "label", "note")

    def __init__(self, verdict, requires_attribution, share_alike, label, note=""):
        self.verdict = verdict
        self.requires_attribution = requires_attribution
        self.share_alike = share_alike
        self.label = label
        self.note = note


_DISPOSITIONS: dict[str, Disposition] = {
    "cc0": Disposition("accept", False, False, "CC0 1.0"),
    "pdm": Disposition("accept", False, False, "Public Domain Mark 1.0"),
    "pd":  Disposition("accept", False, False, "Public Domain"),
    "by":  Disposition("accept", True, False, "CC BY"),
    "by-sa": Disposition("accept", True, True, "CC BY-SA",
                         "share-alike: a monetized video using this bed must carry a "
                         "compatible license"),
}
_REJECT_REASONS = {
    "noc-us": "US-only rights statement (No Copyright – United States) — not cleared worldwide",
    "no-known": "\"no known copyright restrictions\" is not a license — provenance uncertain",
    "sampling": "CC Sampling+ is not a clean reuse license for a full bed",
    "arr": "all rights reserved",
    "unknown": "no traceable rights statement",
}


def classify(code: str) -> Disposition:
    """The accept/reject decision for a canonical license code (single policy seam)."""
    disp = _DISPOSITIONS.get(code)
    if disp is not None:
        return disp
    reason = _REJECT_REASONS.get(code)
    if reason is None:
        reason = ("non-commercial / no-derivatives license — unusable for a monetized, "
                  "composited video") if ("nc" in code or "nd" in code) \
            else "no traceable rights statement"
    return Disposition("reject", False, False, code.upper() or "unknown", reason)


def is_acceptable(license_raw: str) -> bool:
    return classify(normalize_license(license_raw)).verdict == "accept"


def build_attribution(cand: AudioCandidate, disp: Disposition) -> tuple[str, bool]:
    """Build a renderable TASL string + whether it is COMPLETE for this license.

    PD/CC0 need no attribution to be legal (we capture provenance anyway). CC-BY /
    CC-BY-SA legally require it — a missing author/source means the bed cannot CLEAR.
    """
    title = cand.title.strip() or "Untitled"
    author = cand.author.strip()
    src_label = (SOURCE_BY_NAME[cand.source].label
                 if cand.source in SOURCE_BY_NAME else cand.source)
    src_url = cand.source_url.strip()
    license_url = (cand.extra or {}).get("license_url", "").strip()
    parts = [f'"{title}"']
    if author:
        parts.append(f"by {author}")
    parts.append(f"via {src_label}" + (f" ({src_url})" if src_url else ""))
    parts.append(disp.label + (f" — {license_url}" if license_url else ""))
    tasl = " ".join(parts)
    complete = bool(author and src_url) if disp.requires_attribution else True
    return tasl, complete


# ======================================================================
# 2. Input validation — reject a bad script BEFORE spending on tts/downloads
# ======================================================================
def validate_script(script) -> tuple[bool, str]:
    """A script is usable only if it carries scenes that each have narration to speak."""
    if not isinstance(script, dict):
        return False, "That's not a script — I need the script JSON object."
    scenes = script.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return False, ("This script has no scenes — there's nothing to narrate. Send it "
                       "back to the Scriptwriter.")
    speakable = [s for s in scenes
                 if isinstance(s, dict) and str(s.get("narration", "")).strip()]
    if not speakable:
        return False, ("No scene carries any narration text — nothing to voice. The "
                       "script needs `narration` on its scenes.")
    return True, ""


def _scene_no(scene: dict, idx: int):
    n = scene.get("scene_no")
    return n if isinstance(n, int) else idx + 1


# ======================================================================
# 3. record_narration — per-scene tts -> concat -> narration.wav + transcript
# ======================================================================
def record_narration(script: dict, *, pdir: str | pathlib.Path,
                     voice: str = VOICE_DEFAULT, tts_fn=None, transcribe_fn=None,
                     concat_fn=None) -> dict:
    """Synthesize the narration and build the transcript. The seams (tts/concat/
    transcribe) are injected so the unit suite runs with fakes, no toolchain.

    Returns {"narration_wav": rel, "transcript": {...}, "total_duration_sec": float}.
    Raises ValueError on a bad script; RuntimeError if tts/concat fails (no partial
    artifact ships).
    """
    ok, reason = validate_script(script)
    if not ok:
        raise ValueError(reason)

    tts_fn = tts_fn or (lambda text, out: hf_audio.tts(text, out, voice=voice))
    concat_fn = concat_fn or hf_audio.concat_wavs

    pdir = pathlib.Path(pdir)
    adir = pdir / AUDIO_SUBDIR
    adir.mkdir(parents=True, exist_ok=True)

    scenes = [s for s in script["scenes"] if isinstance(s, dict)]

    # The speakable scenes, in ORIGINAL ORDER. Each tts call is a pure function of its
    # (text, voice) and writes to its OWN scene-NN.wav, so we synthesize them CONCURRENTLY
    # and key results by ORIGINAL INDEX. Offsets/concat are then a prefix-sum over the
    # order-preserved list — byte-identical to the old sequential loop, never completion
    # order. If tts_fn is sync the pool runs it on threads; an async tts_fn run via a
    # thread is fine (each call is independent and self-contained).
    speakable: list[tuple[int, str, pathlib.Path]] = []
    for idx, scene in enumerate(scenes):
        text = str(scene.get("narration", "")).strip()
        if not text:
            continue  # a silent scene contributes no narration span
        n = _scene_no(scene, idx)
        speakable.append((n, text, adir / f"scene-{n:02d}.wav"))

    if not speakable:
        raise RuntimeError("no narration was synthesized — every scene was empty.")

    # Synthesize concurrently; collect into a list aligned to `speakable` order. We wait
    # for ALL futures (the pool's __exit__ joins them) before inspecting any result, so a
    # later-scene failure can't slip past — and we raise BEFORE any concat (no partial
    # artifact). The first failure (in original order) wins, matching the sequential raise.
    results: list[dict] = [None] * len(speakable)  # type: ignore[list-item]
    with _futures.ThreadPoolExecutor(max_workers=_tts_workers(len(speakable))) as pool:
        fut_to_pos = {pool.submit(tts_fn, text, str(wav)): pos
                      for pos, (n, text, wav) in enumerate(speakable)}
        for fut in _futures.as_completed(fut_to_pos):
            results[fut_to_pos[fut]] = fut.result()  # propagates any tts_fn exception

    segments: list[dict] = []
    scene_wavs: list[pathlib.Path] = []
    t = 0.0
    for (n, text, wav), res in zip(speakable, results):  # ORIGINAL scene order
        if not res.get("ok"):
            raise RuntimeError(f"tts failed on scene {n}: {res.get('error')}")
        dur = float(res["duration"])
        segments.append({
            "scene_no": int(n),
            "start_sec": round(t, 3),
            "end_sec": round(t + dur, 3),
            "text": text,
        })
        t += dur
        scene_wavs.append(wav)

    total = round(t, 3)
    narration_rel = f"{AUDIO_SUBDIR}/narration.wav"
    cat = concat_fn(scene_wavs, str(pdir / narration_rel))
    if not cat.get("ok"):
        raise RuntimeError(f"could not concat the scene narration: {cat.get('error')}")

    # OPTIONAL word-level enrichment — never fatal (whisper.cpp may be absent).
    if transcribe_fn is not None:
        _enrich_words(segments, pdir / narration_rel, transcribe_fn)

    transcript = {
        "schema_version": TRANSCRIPT_SCHEMA_VERSION,
        "total_duration_sec": total,
        "segments": segments,
    }
    return {"narration_wav": narration_rel, "transcript": transcript,
            "total_duration_sec": total}


def _enrich_words(segments: list[dict], wav_path, transcribe_fn) -> None:
    """Fold word-level timings into each scene segment IF transcribe is available.

    Words land on the scene whose [start,end] window contains the word's midpoint.
    A failure or absent binary is swallowed — the deterministic segments still stand.
    """
    try:
        res = transcribe_fn(str(wav_path))
    except Exception:  # noqa: BLE001 — enrichment must never break the job
        return
    if not res.get("ok"):
        return
    words = _flatten_words(res.get("data") or {})
    if not words:
        return
    for seg in segments:
        seg_words = [w for w in words
                     if seg["start_sec"] <= (w["start"] + w["end"]) / 2 < seg["end_sec"]]
        if seg_words:
            seg["words"] = seg_words


def _flatten_words(data: dict) -> list[dict]:
    """Best-effort extraction of {start,end,text} word entries from transcribe JSON."""
    out = []
    segs = data.get("segments") or data.get("words") or []
    for s in segs:
        for w in (s.get("words") if isinstance(s, dict) and s.get("words") else [s]):
            if not isinstance(w, dict):
                continue
            st, en = w.get("start"), w.get("end")
            tok = w.get("word") or w.get("text")
            if isinstance(st, (int, float)) and isinstance(en, (int, float)) and tok:
                out.append({"start": round(float(st), 3), "end": round(float(en), 3),
                            "text": str(tok).strip()})
    return out


# ======================================================================
# 4. Mood -> music search query (the ONE LLM-assisted step; deterministic fallback)
# ======================================================================
_MOOD_STOP = {"the", "a", "an", "style", "video", "explainer", "look", "feel", "vibe"}
_MOOD_MAP = [
    (("somber", "serious", "dark", "grave", "investigat"), "somber ambient underscore"),
    (("upbeat", "energetic", "bright", "fun", "playful"), "upbeat light instrumental"),
    (("calm", "minimal", "clean", "documentary", "vox", "explainer"),
     "minimal documentary underscore"),
    (("tense", "thriller", "suspense"), "tense cinematic underscore"),
]


def derive_mood_query(style_guide: dict | None) -> str:
    """DETERMINISTIC fallback: a music search query from style cues. Always instrumental,
    always 'no vocals' (a bed must never fight the VO with its own words)."""
    sg = style_guide or {}
    cues = " ".join([
        str(sg.get("reference_note", "")),
        " ".join(str(d) for d in (sg.get("dos") or [])),
    ]).lower()
    base = "minimal documentary underscore"
    for needles, phrase in _MOOD_MAP:
        if any(nd in cues for nd in needles):
            base = phrase
            break
    return f"{base} instrumental no vocals".strip()


_MOOD_SYSTEM = (
    "You name a short royalty-free MUSIC BED search query for a documentary-style "
    "explainer video. Output ONLY the query (a few words), instrumental, no vocals — "
    "no preamble.")


def mood_query(style_guide: dict | None, script: dict | None = None,
               chat_fn=None) -> str:
    """LLM-assisted mood->query with a deterministic fallback. `chat_fn(system,user)->str`
    is injectable (tests pass a fake or None). On ANY failure -> derive_mood_query."""
    fallback = derive_mood_query(style_guide)
    if chat_fn is None:
        chat_fn = llm.chat   # module-global (loader-bound) seam; fallback on any failure
    sg = style_guide or {}
    title = (script or {}).get("working_title", "") if isinstance(script, dict) else ""
    user = (f"Style reference: {sg.get('reference_note', '(none)')}\n"
            f"Do's: {', '.join(str(d) for d in (sg.get('dos') or [])) or '(none)'}\n"
            f"Working title: {title or '(none)'}\n"
            "Give the music bed search query.")
    try:
        q = chat_fn(_MOOD_SYSTEM, user).strip().strip('"').splitlines()[0].strip()
    except Exception:  # noqa: BLE001 — offline / rate-limited -> deterministic
        return fallback
    q = re.sub(r"\s+", " ", q)
    return q[:80] if q else fallback


# ======================================================================
# 5. Bed sourcing — search allowlist -> rank (license-first) -> clear + download-local
# ======================================================================
_LICENSE_RANK = {"cc0": 0, "pdm": 0, "pd": 1, "by": 2, "by-sa": 3}


def rank_candidates(candidates: list[AudioCandidate]) -> list[AudioCandidate]:
    """Acceptable candidates, best first, by a fully-ordered (reproducible) key:
    better license -> longer (covers the video) -> allowlist order -> stable ties."""
    usable = []
    for c in candidates:
        if c.source not in ALLOWLIST_NAMES:
            continue
        code = normalize_license(c.license_raw)
        if classify(code).verdict != "accept":
            continue
        usable.append((code, c))

    def key(item):
        code, c = item
        return (_LICENSE_RANK.get(code, 50), -c.duration,
                SOURCE_ORDER.get(c.source, 99), c.source, c.source_url, c.title)

    return [c for _, c in sorted(usable, key=key)]


def _gather(client, available, query: str) -> list[AudioCandidate]:
    out: list[AudioCandidate] = []
    for source in available:
        try:
            out.extend(client.search(source, query, {}) or [])
        except Exception:  # noqa: BLE001 — a dead/timing-out source is skipped, not fatal
            continue
    return out


def _safe_available(client, source) -> bool:
    try:
        return bool(client.available(source))
    except Exception:  # noqa: BLE001
        return False


def source_bed(style_guide: dict | None, *, client, pdir: pathlib.Path,
               query: str | None = None) -> dict:
    """Find + clear ONE music bed. Returns a record:
       {"status": "cleared", "track": {...}, "path": rel}            (baked into master)
       {"status": "placeholder", "track": {...}, "path": None, "flag": str}  (excluded)
    A cleared bed is downloaded LOCAL (HyperFrames forbids render-time fetches).
    """
    pdir = pathlib.Path(pdir)
    (pdir / AUDIO_SUBDIR).mkdir(parents=True, exist_ok=True)
    q = query or derive_mood_query(style_guide)

    available = [s for s in SOURCES if _safe_available(client, s)
                 and s.media in ("music", "mixed")]
    ranked = rank_candidates(_gather(client, available, q))

    for cand in ranked:
        try:
            data = client.download(cand.download_url)
        except Exception:  # noqa: BLE001 — dead media URL -> next pick
            continue
        if not data:
            continue
        code = normalize_license(cand.license_raw)
        disp = classify(code)
        tasl, complete = build_attribution(cand, disp)
        if disp.requires_attribution and not complete:
            continue  # accepted license but un-attributable -> can't clear, try next
        rel = f"{AUDIO_SUBDIR}/bed.{(cand.ext or 'mp3').lstrip('.')}"
        path = pdir / rel
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        track = _music_track(rel, cand, disp, tasl, code)
        return {"status": "cleared", "track": track, "path": rel}

    # Nothing cleared -> a flagged placeholder (excluded from the master).
    flag = ("no provably-reusable bed on the allowlist for this mood"
            if not ranked else "candidates found but none could be cleared")
    rel = f"{AUDIO_SUBDIR}/bed_placeholder.wav"
    _write_silence(pdir / rel, 1.0)
    return {"status": "placeholder", "path": None, "flag": flag,
            "track": {
                "role": "music", "uri": rel, "gain_db": BED_GAIN_DB,
                "ducking": "narration", "status": "placeholder",
                "license": "unlicensed (placeholder)", "attribution": "",
                "flag": flag, "suggested_query": q,
            }}


def _music_track(rel: str, cand: AudioCandidate, disp: Disposition, tasl: str,
                 code: str) -> dict:
    track = {
        "role": "music", "uri": rel, "gain_db": BED_GAIN_DB, "ducking": "narration",
        "license": disp.label, "license_code": code,
        "license_url": (cand.extra or {}).get("license_url", ""),
        "attribution": tasl, "provenance": cand.source_url,
        "source": cand.source, "status": "cleared",
    }
    if disp.share_alike:
        track["share_alike"] = True
        track["status"] = "sourced"     # legal but needs a human share-alike sign-off
        track["flag"] = disp.note
    return track


# ======================================================================
# 6. Signature SFX placement — on the cut into the storyboard's signature beat
# ======================================================================
def signature_scene(storyboard: dict | None) -> int | None:
    """The FIRST scene flagged `signature_beat: true`, or None. Multiple -> first."""
    for scene in (storyboard or {}).get("scenes", []) or []:
        if isinstance(scene, dict) and scene.get("signature_beat"):
            n = scene.get("scene_no")
            if isinstance(n, int):
                return n
    return None


def signature_at_sec(transcript: dict, scene_no: int) -> float | None:
    """`at_sec` = the global start of that scene's first transcript segment (the cut)."""
    segs = [s for s in (transcript or {}).get("segments", [])
            if s.get("scene_no") == scene_no]
    if not segs:
        return None
    return round(min(float(s.get("start_sec", 0.0)) for s in segs), 3)


def place_signature_sfx(storyboard: dict | None, transcript: dict, style_guide: dict | None,
                        *, pdir: pathlib.Path) -> dict | None:
    """Synthesize + anchor the ONE accent. None when there's no signature beat to hit
    (silence beats a mis-placed hit). Returns an sfx track dict (cleared CC0)."""
    scene_no = signature_scene(storyboard)
    if scene_no is None:
        return None
    at = signature_at_sec(transcript, scene_no)
    if at is None:
        return None
    name = sfx_kit.default_sfx_for(style_guide)
    rel = f"{AUDIO_SUBDIR}/sfx/{name}.wav"
    res = sfx_kit.ensure_sfx(name, pathlib.Path(pdir) / rel)
    if not res.get("ok"):
        return None
    prov = sfx_kit.provenance(name)
    return {
        "role": "sfx", "uri": rel, "gain_db": SFX_GAIN_DB, "ducking": False,
        "scene_no": int(scene_no), "at_sec": at, "name": name, "status": "cleared",
        **{k: prov[k] for k in ("license", "license_code", "license_url",
                                "attribution", "provenance", "source")},
    }


# ======================================================================
# 7. mix_audio — pre-mix the master + emit the manifest
# ======================================================================
def mix_audio(script: dict, style_guide: dict | None, storyboard: dict | None,
              transcript: dict, *, pdir: str | pathlib.Path, client=None,
              mood_query_fn=None, mix_fn=None) -> dict:
    """Source the bed, place the accent, pre-mix master.wav, emit the audio_manifest.

    Seams (client / mix_fn / mood_query_fn) are injected for the unit suite. Returns
    {"manifest": {...}, "master_wav": rel|None}. Guarantees: every music/sfx track
    carries license+attribution; nothing uncleared is baked into the master; the three
    total_duration_sec values agree.
    """
    pdir = pathlib.Path(pdir)
    (pdir / AUDIO_SUBDIR).mkdir(parents=True, exist_ok=True)
    total = round(float(transcript.get("total_duration_sec", 0.0)), 3)
    narration_rel = f"{AUDIO_SUBDIR}/narration.wav"
    master_rel = f"{AUDIO_SUBDIR}/master.wav"

    if client is None:
        client = audio_sources.SourceClient()
    q = (mood_query_fn or mood_query)(style_guide, script) if mood_query_fn \
        else mood_query(style_guide, script)

    bed = source_bed(style_guide, client=client, pdir=pdir, query=q)
    sfx = place_signature_sfx(storyboard, transcript, style_guide, pdir=pdir)

    # Build the master: VO is always in; only a CLEARED bed / the CC0 accent are baked.
    bed_in = ({"path": str(pdir / bed["path"]), "gain_db": BED_GAIN_DB}
              if bed["status"] == "cleared" else None)
    sfx_in = ({"path": str(pdir / sfx["uri"]), "gain_db": SFX_GAIN_DB,
               "at_sec": sfx["at_sec"]} if sfx else None)
    recipe = hf_audio.build_mix_recipe(
        str(pdir / narration_rel), total, out_path=str(pdir / master_rel),
        bed=bed_in, sfx=sfx_in, vo_gain_db=VO_GAIN_DB)
    run = (mix_fn or hf_audio.run_mix)(recipe)
    master_ok = bool(run.get("ok"))

    # The narration track is the MASTER (the bridge), with a vo_uri back-reference.
    tracks = [{
        "role": "narration",
        "uri": master_rel if master_ok else narration_rel,
        "vo_uri": narration_rel,
        "gain_db": VO_GAIN_DB, "ducking": False,
        "license": "n/a (engine-synthesized narration, Kokoro TTS)",
        "attribution": "Narration synthesized by Cadence (HyperFrames Kokoro TTS)",
        "status": "cleared",
    }]
    tracks.append(bed["track"])         # cleared OR flagged placeholder (never baked if placeholder)
    if sfx:
        tracks.append(sfx["track"] if "track" in sfx else sfx)

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "total_duration_sec": total,
        "master_uri": master_rel if master_ok else None,
        "vo_uri": narration_rel,
        "tracks": tracks,
        "wired_into": _wired_into(script),
        "mix": {
            "rule": "VO authoritative; bed ducked under VO; one signature accent on the cut",
            "vo_gain_db": VO_GAIN_DB, "bed_gain_db": BED_GAIN_DB, "sfx_gain_db": SFX_GAIN_DB,
            "bed": bed["status"], "sfx": (sfx["name"] if sfx else None),
            "master_rendered": master_ok,
        },
    }
    _enforce_clearance(manifest)
    return {"manifest": manifest, "master_wav": master_rel if master_ok else None}


def _wired_into(script: dict) -> list[str]:
    return [f"scenes/scene-{_scene_no(s, i):02d}/index.html"
            for i, s in enumerate(script.get("scenes", []) if isinstance(script, dict) else [])
            if isinstance(s, dict)]


def _enforce_clearance(manifest: dict) -> None:
    """Hard invariant (code, not just schema): NO music/sfx track ships without a
    license AND attribution. A cleared/sourced track missing either is a bug; a
    placeholder is allowed (it is flagged and excluded from the master)."""
    for t in manifest.get("tracks", []):
        if t.get("role") not in ("music", "sfx"):
            continue
        if t.get("status") == "placeholder":
            if not t.get("license"):
                t["license"] = "unlicensed (placeholder)"
            continue
        if not str(t.get("license", "")).strip() or not str(t.get("attribution", "")).strip():
            raise RuntimeError(
                f"refusing to ship a {t.get('role')} track without license+attribution: "
                f"{t.get('uri')}")


# ======================================================================
# Manifest stats + loading / saving / runs (standalone + chat conveniences)
# ======================================================================
def manifest_stats(manifest: dict) -> dict:
    roles = {"narration": 0, "music": 0, "sfx": 0}
    cleared = baked = 0
    for t in manifest.get("tracks", []):
        roles[t.get("role", "?")] = roles.get(t.get("role", "?"), 0) + 1
        if t.get("status") in ("cleared", "sourced"):
            cleared += 1
        if t.get("status") != "placeholder":
            baked += 1
    return {"tracks": len(manifest.get("tracks", [])), **roles,
            "cleared": cleared, "master": bool(manifest.get("master_uri"))}


def _write_silence(path: str | pathlib.Path, seconds: float) -> None:
    """Write a tiny silent 24 kHz mono s16le wav (no ffmpeg needed) so a placeholder
    bed `uri` resolves to a real local file for downstream."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(hf_audio.TTS_SAMPLE_RATE * max(0.1, seconds))
    tmp = path.with_name(path.name + ".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(hf_audio.TTS_SAMPLE_RATE)
        w.writeframes(b"\x00\x00" * frames)
    tmp.replace(path)


def load_script(path: str | pathlib.Path) -> dict:
    p = pathlib.Path(path).expanduser()
    if p.is_dir():
        p = p / "script.json"
    return chat_state.load_json(p, {})


def _load_beside(path: str | pathlib.Path, name: str) -> dict:
    p = pathlib.Path(path).expanduser()
    p = (p if p.is_dir() else p.parent) / name
    return chat_state.load_json(p, {})


def load_memory():
    return chat_state.load_json(MEMORY, {"runs": []})


def save_memory(mem):
    chat_state.atomic_write_json(MEMORY, mem)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "mix").lower()).strip("-")
    return (s or "mix")[:50]


def _resolve_pdir(path: str | pathlib.Path) -> pathlib.Path:
    p = pathlib.Path(path).expanduser()
    if p.is_dir():
        return p
    MIXES_DIR.mkdir(exist_ok=True)
    pdir = MIXES_DIR / f"{_slug(p.stem)}-{time.strftime('%Y%m%d-%H%M%S')}"
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir


def run_narrate(path: str | pathlib.Path, *, voice: str = VOICE_DEFAULT,
                quiet: bool = False) -> tuple[dict, pathlib.Path]:
    """Standalone: load script, synthesize narration + transcript, save both."""
    script = load_script(path)
    ok, reason = validate_script(script)
    if not ok:
        raise ValueError(reason)
    pdir = _resolve_pdir(path)
    if not quiet:
        print(f"\n🎙️  Recording narration for {len(script.get('scenes', []))} scenes…")
    out = record_narration(script, pdir=pdir, voice=voice)
    chat_state.atomic_write_json(
        pdir / AUDIO_SUBDIR / "narration.transcript.json", out["transcript"])
    if not quiet:
        print(f"  · {len(out['transcript']['segments'])} segments, "
              f"{out['total_duration_sec']}s → {out['narration_wav']}")
    return out, pdir


def run_mix(path: str | pathlib.Path, *, quiet: bool = False
            ) -> tuple[dict, pathlib.Path]:
    """Standalone: load script/style/storyboard + transcript, source+mix, save manifest.

    Records narration first if no transcript exists beside the script."""
    pdir = _resolve_pdir(path)
    script = load_script(pdir if pdir == pathlib.Path(path).expanduser() else path)
    ok, reason = validate_script(script)
    if not ok:
        raise ValueError(reason)
    style = _load_beside(pdir, "style_guide.json") or None
    storyboard = _load_beside(pdir, "storyboard.json") or None
    transcript = chat_state.load_json(
        pdir / AUDIO_SUBDIR / "narration.transcript.json", {})
    if not transcript.get("segments"):
        out, pdir = run_narrate(pdir, quiet=quiet)
        transcript = out["transcript"]
    if not quiet:
        print("\n🎚️  Mixing the audio (sourcing bed, placing the accent)…")
    res = mix_audio(script, style, storyboard, transcript, pdir=pdir)
    json_path = pdir / AUDIO_SUBDIR / "audio_manifest.json"
    chat_state.atomic_write_json(json_path, res["manifest"])
    st = manifest_stats(res["manifest"])
    if not quiet:
        print(f"  · {st['tracks']} tracks ({st['music']} music, {st['sfx']} sfx), "
              f"master {'rendered' if st['master'] else 'NOT rendered'}")
    _log_run(script, st)
    return res["manifest"], json_path


def _log_run(script: dict, stats: dict) -> None:
    mem = load_memory()
    mem["runs"].append({"scenes": len(script.get("scenes", [])), **stats,
                        "generated": time.strftime("%Y-%m-%d %H:%M:%S")})
    save_memory(mem)
