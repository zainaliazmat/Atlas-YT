"""The bundled signature-SFX kit — local, keyless, provably CC0.

Cadence's defining move is ONE perfectly-timed accent on the cut into the signature
beat. That move cannot depend on a keyed archive (Freesound) being reachable, so the
core kit is SYNTHESIZED here with FFmpeg from deterministic recipes — no binary blob
in the repo, no network, and unambiguously ours to release under CC0.

Three accents, each a short 24 kHz / mono / s16le wav matching the narration format:
  - "stamp"     — a low thud + click; the default "this is the point" punctuation.
  - "page-turn" — a brief filtered-noise swish; for editorial / chaptered looks.
  - "whoosh"    — a bandpassed noise sweep; for motion-forward transitions.

Mechanism (recipe) is separated from execution so the synthesis command is unit-
testable with no subprocess. The Freesound allowlist source is ENRICHMENT for variety
on top of this — never a dependency for the accent to land.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess

SAMPLE_RATE = 24000   # match the tts/narration format so the mux stays uniform
NOISE_SEED = 42       # fixed seed -> byte-reproducible synthesis

# name -> (lavfi source expr, filter chain). Both deterministic (seeded noise).
_RECIPES: dict[str, tuple[str, str]] = {
    "stamp": (
        f"sine=frequency=140:duration=0.28:sample_rate={SAMPLE_RATE}",
        "afade=t=out:st=0.04:d=0.22,volume=3dB",
    ),
    "page-turn": (
        f"anoisesrc=d=0.4:c=pink:a=0.35:r={SAMPLE_RATE}:s={NOISE_SEED}",
        "highpass=f=2200,lowpass=f=8000,afade=t=in:d=0.04,afade=t=out:st=0.14:d=0.26",
    ),
    "whoosh": (
        f"anoisesrc=d=0.6:c=pink:a=0.4:r={SAMPLE_RATE}:s={NOISE_SEED}",
        "bandpass=f=1500:width_type=h:w=1200,afade=t=in:d=0.2,afade=t=out:st=0.3:d=0.3",
    ),
}

KIT_NAMES = tuple(_RECIPES)
DEFAULT_SFX = "stamp"      # silence beats a mis-placed hit, but a stamp on a cut is safe


def provenance(name: str) -> dict:
    """The clearance record for a synthesized accent — CC0, ours, no attribution owed."""
    return {
        "license": "CC0 1.0 (synthesized)",
        "license_code": "cc0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "attribution": f"\"{name}\" — synthesized CC0 SFX (YT-AGENTS Cadence kit)",
        "provenance": "synthesized in-engine (FFmpeg lavfi); no third-party rights",
        "source": "cadence-sfx-kit",
    }


def default_sfx_for(style_guide: dict | None) -> str:
    """Deterministically pick a kit accent from style cues. Editorial/paper/chapter
    looks → page-turn; motion-forward → whoosh; otherwise the safe stamp."""
    sg = style_guide or {}
    cues = " ".join([
        str(sg.get("reference_note", "")),
        " ".join(str(d) for d in (sg.get("dos") or [])),
        " ".join(t.get("name", "") if isinstance(t, dict) else str(t)
                 for t in (sg.get("textures") or [])),
    ]).lower()
    if any(w in cues for w in ("page", "paper", "editorial", "chapter", "book", "print")):
        return "page-turn"
    if any(w in cues for w in ("kinetic", "motion", "sweep", "fast", "energetic", "whoosh")):
        return "whoosh"
    return DEFAULT_SFX


def build_sfx_recipe(name: str, out_path: str | pathlib.Path) -> dict:
    """PURE: the FFmpeg command to synthesize accent `name` to `out_path`.

    Returns {"args": [...], "name": str, "output": str}. Raises KeyError for an
    unknown accent (a programming error, not a runtime condition)."""
    src, chain = _RECIPES[name]
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    args = [ffmpeg, "-y", "-f", "lavfi", "-i", src,
            "-af", chain, "-ar", str(SAMPLE_RATE), "-ac", "1",
            "-c:a", "pcm_s16le", str(out_path)]
    return {"args": args, "name": name, "output": str(out_path)}


def ensure_sfx(name: str, out_path: str | pathlib.Path, *, timeout: int = 60) -> dict:
    """Synthesize accent `name` to `out_path` (idempotent). Never raises.

    Returns {"ok": bool, "path": str|None, "error": str|None}.
    """
    if name not in _RECIPES:
        return {"ok": False, "path": None, "error": f"unknown SFX accent {name!r}."}
    out_path = pathlib.Path(out_path)
    if out_path.exists() and out_path.stat().st_size > 0:
        return {"ok": True, "path": str(out_path), "error": None}
    if shutil.which("ffmpeg") is None:
        return {"ok": False, "path": None, "error": "ffmpeg not found — required to synthesize SFX."}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    recipe = build_sfx_recipe(name, out_path)
    try:
        proc = subprocess.run(recipe["args"], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "path": None, "error": f"SFX synth timed out after {timeout}s."}
    except OSError as exc:
        return {"ok": False, "path": None, "error": f"could not launch ffmpeg: {exc}"}
    if proc.returncode != 0 or not out_path.exists():
        return {"ok": False, "path": None,
                "error": (proc.stderr or "")[:300] or "SFX synth produced no output."}
    return {"ok": True, "path": str(out_path), "error": None}
