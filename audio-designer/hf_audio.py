"""In-process wrappers around the real audio toolchain (HyperFrames CLI + FFmpeg).

Phase-0 settled calls (verified against hyperframes v0.6.115):
- `npx hyperframes tts [INPUT] -o out.wav -v <voice> -s <speed> --json` — INPUT is
  literal text OR a path to a .txt file; emits ONE wav (PCM s16le, 24000 Hz, mono,
  16-bit) and reports `durationSeconds` in its JSON. We feed text via a temp .txt
  file so long narration with quotes/newlines is never shell-mangled.
- `npx hyperframes transcribe <wav> --json` — word-level timing, but it needs a
  compiled `whisper.cpp` binary which is NOT a pip dep. So transcribe is OPTIONAL
  ENRICHMENT here: absence is reported, never fatal. Cadence's transcript is built
  deterministically from per-scene tts `durationSeconds` (see audio_engine), which
  needs no ASR at all.
- FFmpeg/FFprobe — lossless WAV concat (identical tts params), the documentary mix
  (sidechain-ducked bed + one SFX accent under authoritative VO), and duration probe.

Like atlas/tools.py and the sibling hf_tools.py, every wrapper is ERROR-CONTAINED
(never raises) and TIMEOUT-BOUNDED, returning a structured pass/fail dict. The pure
mix-RECIPE builder (`build_mix_recipe`) is separated from running FFmpeg so the
filtergraph — the crux of the documentary mix — is unit-testable with no subprocess.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile

# Verified tts output format (Phase 0). Per-scene tts has fixed npx + model-load
# overhead (~11s for a one-sentence scene), so timeouts are generous and scale.
# Bumped 240->600: when several scenes synthesize concurrently (see audio_engine
# _tts_workers) each CPU-bound call runs slower, so the per-call budget needs headroom.
TTS_TIMEOUT = 600
TRANSCRIBE_TIMEOUT = 300
FFMPEG_TIMEOUT = 600
PROBE_TIMEOUT = 60

TTS_SAMPLE_RATE = 24000   # Kokoro output: 24 kHz mono s16le
TTS_CHANNELS = 1


def _npx() -> str | None:
    return shutil.which("npx")


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _ffprobe() -> str | None:
    return shutil.which("ffprobe")


def toolchain_available() -> bool:
    """tts + the mix both need npx (HyperFrames) and ffmpeg present."""
    return _npx() is not None and _ffmpeg() is not None


def _parse_json(stdout: str) -> dict | None:
    """Decode the first JSON object in stdout, skipping any leading Chrome/status
    preamble and tolerating trailing telemetry. Returns None if none is present."""
    idx = stdout.find("{")
    if idx == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(stdout[idx:])
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


# ----------------------------------------------------------------------
# Text-to-speech (one scene -> one wav)
# ----------------------------------------------------------------------
def tts(text: str, out_path: str | pathlib.Path, *, voice: str = "af_heart",
        speed: float = 1.0, timeout: int = TTS_TIMEOUT) -> dict:
    """Synthesize one chunk of narration to `out_path`. Never raises.

    Returns {"ok": bool, "duration": float|None, "output": str|None, "error": str|None}.
    `duration` is the model-reported `durationSeconds` (validated as a positive
    number) — the authority for scene-offset math.
    """
    npx = _npx()
    if npx is None:
        return {"ok": False, "duration": None, "output": None,
                "error": "npx not found — install Node.js >= 22 to run hyperframes tts."}
    text = (text or "").strip()
    if not text:
        return {"ok": False, "duration": None, "output": None,
                "error": "empty narration text — nothing to synthesize."}
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Feed text via a temp .txt file so quotes/newlines/length never shell-mangle.
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         dir=str(out_path.parent), encoding="utf-8") as fh:
            fh.write(text)
            tmp = fh.name
        args = [npx, "hyperframes", "tts", tmp, "-o", str(out_path),
                "-v", voice, "-s", str(speed), "--json"]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "duration": None, "output": None,
                    "error": f"`hyperframes tts` timed out after {timeout}s."}
        except OSError as exc:
            return {"ok": False, "duration": None, "output": None,
                    "error": f"could not launch `hyperframes tts`: {exc}"}
    finally:
        if tmp:
            try:
                pathlib.Path(tmp).unlink()
            except OSError:
                pass

    data = _parse_json(proc.stdout) or {}
    if not data.get("ok") or proc.returncode != 0:
        err = data.get("error") or (proc.stderr or "")[:300] or "tts did not succeed"
        return {"ok": False, "duration": None, "output": None, "error": err}
    dur = data.get("durationSeconds")
    if not isinstance(dur, (int, float)) or not dur > 0:
        return {"ok": False, "duration": None, "output": None,
                "error": f"tts reported a non-positive duration ({dur!r})."}
    if not out_path.exists():
        return {"ok": False, "duration": None, "output": None,
                "error": "tts reported success but wrote no output file."}
    return {"ok": True, "duration": float(dur), "output": str(out_path), "error": None}


def transcribe(wav_path: str | pathlib.Path, *, timeout: int = TRANSCRIBE_TIMEOUT) -> dict:
    """OPTIONAL word-level enrichment. Needs a compiled whisper.cpp binary; its
    absence is reported, never fatal. Returns {"ok", "data": <raw json>|None, "error"}.
    """
    npx = _npx()
    if npx is None:
        return {"ok": False, "data": None, "error": "npx not found."}
    wav_path = pathlib.Path(wav_path)
    if not wav_path.exists():
        return {"ok": False, "data": None, "error": f"no audio at {wav_path}."}
    args = [npx, "hyperframes", "transcribe", str(wav_path), "--json"]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "data": None,
                "error": f"`hyperframes transcribe` timed out after {timeout}s."}
    except OSError as exc:
        return {"ok": False, "data": None, "error": f"could not launch transcribe: {exc}"}
    data = _parse_json(proc.stdout) or {}
    if not data.get("ok", proc.returncode == 0) or proc.returncode != 0:
        return {"ok": False, "data": None,
                "error": data.get("error") or (proc.stderr or "")[:300] or
                "transcribe unavailable (whisper.cpp not installed)."}
    return {"ok": True, "data": data, "error": None}


# ----------------------------------------------------------------------
# FFprobe duration + lossless concat
# ----------------------------------------------------------------------
def probe_duration(path: str | pathlib.Path) -> float | None:
    """Return media duration in seconds via ffprobe, or None on any failure."""
    ffprobe = _ffprobe()
    if ffprobe is None:
        return None
    args = [ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nk=1:nw=1", str(path)]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=PROBE_TIMEOUT)
        return float(proc.stdout.strip())
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def concat_wavs(wav_paths: list[str | pathlib.Path], out_path: str | pathlib.Path,
                *, timeout: int = FFMPEG_TIMEOUT) -> dict:
    """Lossless-concat per-scene wavs (identical tts params) -> one wav. Never raises.

    Uses the concat demuxer with `-c copy` — sample-accurate and re-encode-free
    because every scene shares 24 kHz / mono / s16le.
    """
    ffmpeg = _ffmpeg()
    if ffmpeg is None:
        return {"ok": False, "output": None, "error": "ffmpeg not found."}
    wav_paths = [pathlib.Path(p) for p in wav_paths]
    missing = [str(p) for p in wav_paths if not p.exists()]
    if not wav_paths or missing:
        return {"ok": False, "output": None,
                "error": f"missing scene wavs to concat: {missing or 'none provided'}"}
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    listing = out_path.with_name(out_path.stem + "_concat.txt")
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in wav_paths))
    args = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy", str(out_path)]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": None, "error": f"concat timed out after {timeout}s."}
    except OSError as exc:
        return {"ok": False, "output": None, "error": f"could not launch ffmpeg: {exc}"}
    finally:
        try:
            listing.unlink()
        except OSError:
            pass
    if proc.returncode != 0 or not out_path.exists():
        return {"ok": False, "output": None, "error": (proc.stderr or "")[:300] or
                "concat produced no output."}
    return {"ok": True, "output": str(out_path), "error": None}


# ----------------------------------------------------------------------
# The documentary mix — a PURE recipe builder + a runner.
# ----------------------------------------------------------------------
# Defaults are MECHANISM defaults; the engine owns the POLICY values it passes in.
DUCK_THRESHOLD = 0.03   # sidechaincompress: VO above this ducks the bed
DUCK_RATIO = 8          # hard duck — VO is authoritative
DUCK_ATTACK_MS = 5
DUCK_RELEASE_MS = 300
TAIL_FADE_SEC = 0.75    # bed/master tail-fade so the mux ends clean
LIMIT = 0.97            # peak brickwall (alimiter) to avoid summed-clip BEFORE loudnorm
# Final loudness normalization — the peak limiter caps peaks but never RAISES quiet
# content, so masters shipped ~-22 LUFS (≈8 too quiet; the eval calibration flagged this).
# loudnorm brings the integrated loudness up to the YouTube target and re-limits true peak.
TARGET_LUFS = -14.0     # YouTube integrated-loudness standard (LUFS)
TARGET_TP = -1.0        # true-peak ceiling (dBTP)
TARGET_LRA = 11.0       # loudness range (LU) — documentary VO+bed sits comfortably here


def build_mix_recipe(vo_path: str | pathlib.Path, total_dur: float, *,
                     out_path: str | pathlib.Path,
                     bed: dict | None = None, sfx: dict | None = None,
                     vo_gain_db: float = 0.0,
                     duck=(DUCK_THRESHOLD, DUCK_RATIO, DUCK_ATTACK_MS, DUCK_RELEASE_MS),
                     tail_fade: float = TAIL_FADE_SEC, limit: float = LIMIT,
                     target_lufs: float = TARGET_LUFS, target_tp: float = TARGET_TP,
                     target_lra: float = TARGET_LRA) -> dict:
    """Build (but do NOT run) the FFmpeg command for the documentary master mix.

    PURE: returns {"args": [...], "filter_complex": str, "output": str, "inputs": [...]}
    so a unit test can assert the filtergraph for a given cleared-track set without
    touching FFmpeg. The discipline is baked in here:
      - VO is authoritative (its own gain, used un-attenuated as the sidechain key);
      - a bed is hard-ducked UNDER the VO via sidechaincompress, then tail-faded;
      - one SFX accent is delayed to `at_sec` and sits a touch under the VO;
      - the master is trimmed to EXACTLY `total_dur` so it aligns to the concatenated
        video, with a peak limiter against summed clipping THEN a final loudnorm that
        raises the integrated loudness to the YouTube target (≈-14 LUFS) — without it
        masters ship ~-22 LUFS, too quiet (the eval calibration finding).
    `bed`/`sfx` are {"path": str, "gain_db": float[, "at_sec": float]} or None — a
    flagged/uncleared track is simply not passed in, so it can never enter the mix.
    """
    ffmpeg = _ffmpeg() or "ffmpeg"
    dur = round(float(total_dur), 3)
    fade_st = max(0.0, round(dur - tail_fade, 3))
    thr, ratio, attack, release = duck

    # Ordered inputs: VO is always input 0. Bed loops to cover the full duration.
    inputs: list[tuple[list[str], str]] = [([], str(vo_path))]
    bed_idx = sfx_idx = None
    if bed and bed.get("path"):
        bed_idx = len(inputs)
        inputs.append((["-stream_loop", "-1"], str(bed["path"])))
    if sfx and sfx.get("path"):
        sfx_idx = len(inputs)
        inputs.append(([], str(sfx["path"])))

    # VO is split into an audible track + an (un-attenuated) duck key ONLY when a bed
    # needs the key — otherwise the [vokey] pad would dangle and FFmpeg would error.
    if bed_idx is not None:
        chains = [f"[0:a]asplit=2[vokey][vo0]",
                  f"[vo0]volume={vo_gain_db}dB[voout]"]
    else:
        chains = [f"[0:a]volume={vo_gain_db}dB[voout]"]
    mix_labels = ["[voout]"]

    if bed_idx is not None:
        bed_gain = bed.get("gain_db", -20.0)
        chains.append(
            f"[{bed_idx}:a]volume={bed_gain}dB[bedlvl]")
        chains.append(
            f"[bedlvl][vokey]sidechaincompress="
            f"threshold={thr}:ratio={ratio}:attack={attack}:release={release}[bedduck]")
        chains.append(
            f"[bedduck]atrim=0:{dur},afade=t=out:st={fade_st}:d={tail_fade}[bedout]")
        mix_labels.append("[bedout]")

    if sfx_idx is not None:
        sfx_gain = sfx.get("gain_db", -6.0)
        at_ms = int(round(float(sfx.get("at_sec", 0.0)) * 1000))
        chains.append(
            f"[{sfx_idx}:a]adelay={at_ms}|{at_ms},volume={sfx_gain}dB[sfxout]")
        mix_labels.append("[sfxout]")

    # Final stage: trim to length, peak-limit, then loudnorm to the integrated-loudness
    # target. loudnorm is LAST so it sets the master's delivered loudness + true peak.
    loudnorm = f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={target_lra}"
    # amix needs >= 2 inputs; with only the VO (no bed, no accent) skip it entirely —
    # an amix=inputs=1 is rejected by FFmpeg.
    if len(mix_labels) == 1:
        chains.append(f"{mix_labels[0]}atrim=0:{dur},alimiter=limit={limit},{loudnorm}[master]")
    else:
        chains.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:normalize=0:"
            f"dropout_transition=0[mixed]")
        chains.append(f"[mixed]atrim=0:{dur},alimiter=limit={limit},{loudnorm}[master]")
    filter_complex = ";".join(chains)

    args = [ffmpeg, "-y"]
    for prefix, path in inputs:
        args += prefix + ["-i", path]
    args += ["-filter_complex", filter_complex, "-map", "[master]",
             "-ar", str(TTS_SAMPLE_RATE), "-ac", str(TTS_CHANNELS), str(out_path)]
    return {"args": args, "filter_complex": filter_complex,
            "output": str(out_path), "inputs": [p for _, p in inputs]}


def run_mix(recipe: dict, *, timeout: int = FFMPEG_TIMEOUT) -> dict:
    """Execute a `build_mix_recipe` result into its output wav. Never raises."""
    if _ffmpeg() is None:
        return {"ok": False, "output": None, "error": "ffmpeg not found — required for the mix."}
    out_path = pathlib.Path(recipe["output"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(recipe["args"], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": None, "error": f"mix timed out after {timeout}s."}
    except OSError as exc:
        return {"ok": False, "output": None, "error": f"could not launch ffmpeg: {exc}"}
    if proc.returncode != 0 or not out_path.exists():
        return {"ok": False, "output": None,
                "error": (proc.stderr or "")[:300] or "mix produced no output."}
    return {"ok": True, "output": str(out_path), "error": None}
