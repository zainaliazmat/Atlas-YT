"""studio.library.seed — seed the shared Asset Library from the golden win.

Imports the reusable, license-cleared assets out of
``reference/dark-truth-social/assets/`` into ``asset-library/`` with correct
license + provenance + semantic tags:

  - the 6 font families (Inter, Roboto Slab, Rubik Spray Paint, Sacramento,
    Space Mono, Spectral),
  - Lucide UI icons (recolorable) + Simple Icons brand logos,
  - the signature SFX (whoosh / slot-reel / glitch / stamp / chime / notification
    + keyboard),
  - the music beds (tagged mood + duration),
  - the halftone portrait.

Idempotent: :func:`studio.library.add` dedupes on sha256, so re-running adds
nothing new. Run with ``python -m studio.library.seed``.
"""

from __future__ import annotations

from pathlib import Path

from .. import config
from . import add

REF = config.REPO_ROOT / "reference" / "dark-truth-social" / "assets"

# --- fonts (6 families) ------------------------------------------------------
# Roboto Slab is Apache-2.0; the rest are SIL OFL 1.1 (verified from the license
# files shipped alongside each family in the reference).
_OFL = "OFL-1.1"
FONTS = [
    {"id": "font-inter", "file": "fonts/Inter/Inter-VariableFont_opsz,wght.ttf", "family": "Inter",
     "license": _OFL, "attribution": "Inter — Rasmus Andersson (SIL OFL 1.1)",
     "tags": ["font", "sans", "body", "variable", "inter"]},
    {"id": "font-roboto-slab", "file": "fonts/Roboto_Slab/static/RobotoSlab-Black.ttf", "family": "Roboto Slab",
     "license": "Apache-2.0", "attribution": "Roboto Slab — Google (Apache License 2.0)", "weight": 900,
     "tags": ["font", "slab", "serif", "display", "numbers", "roboto-slab"]},
    {"id": "font-rubik-spray-paint", "file": "fonts/Rubik_Spray_Paint/RubikSprayPaint-Regular.ttf", "family": "Rubik Spray Paint",
     "license": _OFL, "attribution": "Rubik Spray Paint — NaN (SIL OFL 1.1)", "weight": 400,
     "tags": ["font", "display", "grunge", "hero", "spray", "rubik-spray-paint"]},
    {"id": "font-sacramento", "file": "fonts/Sacramento/Sacramento-Regular.ttf", "family": "Sacramento",
     "license": _OFL, "attribution": "Sacramento — Astigmatic (SIL OFL 1.1)", "weight": 400,
     "tags": ["font", "script", "signature", "handwriting", "sacramento"]},
    {"id": "font-space-mono", "file": "fonts/Space_Mono/SpaceMono-Regular.ttf", "family": "Space Mono",
     "license": _OFL, "attribution": "Space Mono — Colophon (SIL OFL 1.1)", "weight": 400,
     "tags": ["font", "mono", "label", "caption", "metadata", "space-mono"]},
    {"id": "font-spectral", "file": "fonts/Spectral/Spectral-Regular.ttf", "family": "Spectral",
     "license": _OFL, "attribution": "Spectral — Production Type (SIL OFL 1.1)", "weight": 400,
     "tags": ["font", "serif", "body", "editorial", "spectral"]},
]

# --- icons -------------------------------------------------------------------
# Lucide UI icons use stroke="currentColor" -> recolorable. ISC license.
LUCIDE = "lucide.dev"
ICONS_UI = [
    ("bell", ["bell", "notification", "alert"]),
    ("check", ["check", "done", "tick"]),
    ("circle-check", ["circle-check", "verified", "approved", "done"]),
    ("eye-off", ["eye-off", "hidden", "privacy", "grayscale"]),
    ("refresh-cw", ["refresh-cw", "refresh", "reload", "pull-to-refresh"]),
    ("smartphone", ["smartphone", "phone", "device", "mobile"]),
    ("infinity", ["infinity", "loop", "endless", "infinite-scroll"]),
    ("frown", ["frown", "sad", "negative", "unhappy"]),
    ("award", ["award", "badge", "reward", "medal"]),
]
# Simple Icons brand logos: CC0 icon data, but logos are trademarks. Not
# recolorable (brand identity).
SIMPLE = "simpleicons.org"
ICONS_BRAND = [
    ("x", ["x", "twitter", "social"]),
    ("facebook", ["facebook", "meta", "social"]),
    ("instagram", ["instagram", "meta", "social"]),
    ("tiktok", ["tiktok", "social"]),
    ("youtube", ["youtube", "google", "social"]),
]

# --- sfx ---------------------------------------------------------------------
_PIXABAY = "Pixabay Content License"
SFX = [
    {"id": "sfx-whoosh", "file": "audio/dragon-studio-simple-whoosh-382724.mp3", "duration": 0.575,
     "license": _PIXABAY, "attribution": "dragon-studio (Pixabay)", "tags": ["whoosh", "swipe", "transition"]},
    {"id": "sfx-slot-reel", "file": "audio/freesound_community-slot-machine-payout-81725.mp3", "duration": 3.72,
     "license": _PIXABAY, "attribution": "freesound_community (Pixabay)", "tags": ["slot-reel", "slot-machine", "reward", "payout"]},
    {"id": "sfx-glitch", "file": "audio/kave_msri-glitch-sfx-312910.mp3", "duration": 1.752,
     "license": _PIXABAY, "attribution": "kave_msri (Pixabay)", "tags": ["glitch", "distortion", "rgb-split"]},
    {"id": "sfx-stamp", "file": "audio/sfx-stamp.mp3", "duration": 0.601,
     "license": "Unknown", "attribution": "", "tags": ["stamp", "impact", "verdict"]},
    {"id": "sfx-chime", "file": "audio/universfield-soft-opening-piano-logo-153268.mp3", "duration": 6.243,
     "license": _PIXABAY, "attribution": "universfield (Pixabay)", "tags": ["chime", "logo-sting", "piano", "outro"]},
    {"id": "sfx-notification", "file": "audio/universfield-new-notification-08-352461.mp3", "duration": 1.128,
     "license": _PIXABAY, "attribution": "universfield (Pixabay)", "tags": ["notification", "bell", "alert", "ding"]},
    {"id": "sfx-keyboard", "file": "audio/yzaak-keyboard-sound-satisfying-304411.mp3", "duration": 19.592,
     "license": _PIXABAY, "attribution": "yzaak (Pixabay)", "tags": ["keyboard", "typing", "satisfying"]},
]

# --- music beds (mood + duration) -------------------------------------------
MUSIC = [
    {"id": "music-dark-hopeful", "file": "audio/music.mp3", "duration": 85.029, "provenance": "generated",
     "license": _PIXABAY, "attribution": "desifreemusic + nastelbom (Pixabay)",
     "mood": ["dark", "hopeful", "transition", "editorial"], "tags": ["bed", "music", "dark", "hopeful"]},
    {"id": "music-dark-horizon", "file": "audio/desifreemusic-dark-horizon-suspense-build-up-music-411017.mp3", "duration": 134.952,
     "license": _PIXABAY, "attribution": "desifreemusic (Pixabay)",
     "mood": ["dark", "suspense", "tension", "build-up"], "tags": ["bed", "music", "dark", "suspense"]},
    {"id": "music-hopeful", "file": "audio/nastelbom-hopeful-436853.mp3", "duration": 142.028,
     "license": _PIXABAY, "attribution": "nastelbom (Pixabay)",
     "mood": ["hopeful", "uplifting", "warm"], "tags": ["bed", "music", "hopeful"]},
]

# --- images ------------------------------------------------------------------
IMG = [
    {"id": "img-portrait", "file": "img/portrait.jpg",
     "license": "Pexels License", "attribution": "Pexels", "tags": ["portrait", "person", "face", "silhouette"]},
]


def _add(src_rel: str, *, kind, tags, license, attribution, source, provenance="sourced", recolorable=False, id=None, extra=None):
    src = REF / src_rel
    if not src.is_file():
        return None, f"missing source: {src}"
    entry = add(
        src, kind, tags, license, attribution, source, provenance, recolorable,
        id=id, extra=extra,
    )
    return entry, None


def seed(verbose: bool = True) -> dict:
    """Seed the library from the reference assets. Returns a summary."""
    added, skipped_missing = [], []

    def emit(entry, err, what):
        if err:
            skipped_missing.append(what)
            if verbose:
                print(f"  skip {what}: {err}")
        else:
            added.append(entry["id"])
            if verbose:
                print(f"  ok   {entry['id']:<24} {entry['file']}")

    if verbose:
        print("fonts:")
    for f in FONTS:
        extra = {"family": f["family"]}
        if "weight" in f:
            extra["weight"] = f["weight"]
        e, err = _add(f["file"], kind="font", tags=f["tags"], license=f["license"],
                      attribution=f["attribution"], source="Google Fonts", id=f["id"], extra=extra)
        emit(e, err, f["id"])

    if verbose:
        print("icons (UI / Lucide):")
    for name, tags in ICONS_UI:
        e, err = _add(f"icons/{name}.svg", kind="icon", tags=tags + ["ui", "lucide"], license="ISC",
                      attribution="Lucide (ISC)", source=LUCIDE, recolorable=True, id=f"icon-{name}")
        emit(e, err, f"icon-{name}")

    if verbose:
        print("icons (brand / Simple Icons):")
    for name, tags in ICONS_BRAND:
        e, err = _add(f"icons/{name}.svg", kind="icon", tags=tags + ["brand", "logo"], license="CC0-1.0",
                      attribution="Simple Icons (CC0 1.0); logo is a trademark of its owner",
                      source=SIMPLE, recolorable=False, id=f"brand-{name}")
        emit(e, err, f"brand-{name}")

    if verbose:
        print("sfx:")
    for s in SFX:
        e, err = _add(s["file"], kind="sfx", tags=s["tags"], license=s["license"],
                      attribution=s["attribution"], source="reference/dark-truth-social",
                      id=s["id"], extra={"duration": s["duration"]})
        emit(e, err, s["id"])

    if verbose:
        print("music:")
    for m in MUSIC:
        e, err = _add(m["file"], kind="music", tags=m["tags"], license=m["license"],
                      attribution=m["attribution"], source="reference/dark-truth-social",
                      provenance=m.get("provenance", "sourced"), id=m["id"],
                      extra={"duration": m["duration"], "mood": m["mood"]})
        emit(e, err, m["id"])

    if verbose:
        print("images:")
    for im in IMG:
        e, err = _add(im["file"], kind="img", tags=im["tags"], license=im["license"],
                      attribution=im["attribution"], source="pexels.com", id=im["id"])
        emit(e, err, im["id"])

    summary = {"added": added, "skipped_missing": skipped_missing}
    if verbose:
        print(f"\nseeded {len(added)} asset(s); {len(skipped_missing)} missing source(s).")
    return summary


if __name__ == "__main__":  # pragma: no cover
    seed()
