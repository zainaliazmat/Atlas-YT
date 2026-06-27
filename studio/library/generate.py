"""studio.library.generate — the self-growing seam of the Asset Library.

Single entry point: ``obtain(kind, tags, constraints)``. It resolves
cheapest-deterministic-first and ALWAYS writes back, so an asset is produced at
most once and then served from cache forever:

    1. library.resolve(...) HIT  -> return it (recolor if a color is requested).
                                    No generation.
    2. MISS -> generate, stopping at the first route that satisfies:
       a. PROCEDURAL (preferred) -> an inline SVG+GSAP snippet (deterministic,
          like the reference's hand-built bell/checkmarks). provenance="procedural".
       b. LOTTIE (only when illustration-grade) -> via the lottie-master skill
          (scripts/lottie_gen.py + lottie_optimize.py), recolored to the pack
          token. provenance="generated".
       c. SOURCE (icons/img/audio) -> Simple Icons / Lucide / Pexels / Pixabay /
          Freesound with a license accept-list. provenance="sourced".
    3. Every branch calls library.add(...) -> cached forever, never regenerated.
    4. Returns a uniform :class:`AssetRef` the Composer drops into HTML.

Plus :func:`halftone` — the in-engine 1-bit portrait processor (deterministic,
cached once per source).

Offline-safe: the procedural route and halftone need no network; the lottie and
source routes degrade to ``None`` when their tools/network are unavailable.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import _snippets, add, get, recolor, resolve
from . import LibraryError
from .. import config

# Licenses we will cache. Anything else from a SOURCE is rejected.
ACCEPT_LICENSES = {
    "OFL-1.1", "Apache-2.0", "ISC", "MIT", "CC0-1.0",
    "CC-BY-4.0", "CC-BY-3.0", "CC-BY-SA-4.0",
    "Pixabay Content License", "Pexels License",
}

# Procedural snippets are our own code -> CC0 (no attribution required).
_PROCEDURAL_LICENSE = "CC0-1.0"


class GenerationError(LibraryError):
    """Raised when a requested asset cannot be generated or sourced."""


@dataclass
class AssetRef:
    """Uniform handle the Composer drops into HTML.

    ``embed`` is one of: ``inline_snippet`` | ``svg`` | ``img`` | ``dotlottie``
    | ``font_face`` | ``audio``. ``payload`` is the inline text (snippet/SVG) or
    the library-relative file path (img/lottie/font/audio). ``factory`` is the
    snippet's JS factory name (snippets only).
    """

    entry: dict
    embed: str
    payload: str
    factory: str | None = None
    color: str | None = None
    meta: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.entry["id"]

    @property
    def kind(self) -> str:
        return self.entry["kind"]

    @property
    def provenance(self) -> str:
        return self.entry["provenance"]

    @property
    def uri(self) -> str:
        """Library-relative path (Composer rewrites to the composition's asset dir)."""
        return self.entry["file"]

    def html(self) -> str:
        """Render a drop-in HTML fragment for this asset."""
        if self.embed == "inline_snippet":
            return f"<script>\n{self.payload}\n</script>"
        if self.embed == "svg":
            return self.payload
        if self.embed == "img":
            return f'<img src="{self.uri}" alt="">'
        if self.embed == "dotlottie":
            return f'<dotlottie-wc src="{self.uri}" autoplay loop></dotlottie-wc>'
        if self.embed == "font_face":
            fam = self.entry.get("family", self.id)
            return f"@font-face {{ font-family: '{fam}'; src: url('{self.uri}'); }}"
        if self.embed == "audio":
            return f'<audio src="{self.uri}"></audio>'
        return self.payload


# --- routing -----------------------------------------------------------------
def _norm(tags) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        tags = tags.split(",")
    return [t.strip().lower() for t in tags if str(t).strip()]


def _query_names(tags, constraints) -> list[str]:
    names = list(_norm(tags))
    nm = (constraints or {}).get("name")
    if nm:
        names.insert(0, str(nm).strip().lower())
    return names


def _as_ref(entry: dict, constraints: dict | None) -> AssetRef:
    """Wrap a manifest entry as an AssetRef, recoloring text assets if asked."""
    c = constraints or {}
    color = c.get("color")
    kind = entry["kind"]
    lib_path = config.ASSET_LIBRARY_DIR / entry["file"]

    if kind == "snippet":
        text = lib_path.read_text(encoding="utf-8")
        return AssetRef(entry, "inline_snippet", text, factory=entry.get("factory"),
                        color=color or entry.get("default_color"))
    if kind == "icon":
        text = lib_path.read_text(encoding="utf-8")
        if color and entry.get("recolorable"):
            text = recolor(entry, color)
        return AssetRef(entry, "svg", text, color=color)
    if kind == "lottie":
        return AssetRef(entry, "dotlottie", entry["file"], color=color)
    if kind == "font":
        return AssetRef(entry, "font_face", entry["file"])
    if kind == "img":
        return AssetRef(entry, "img", entry["file"])
    if kind in ("sfx", "music"):
        return AssetRef(entry, "audio", entry["file"])
    return AssetRef(entry, "svg", entry.get("file", ""))


# --- the entry point ---------------------------------------------------------
def obtain(kind: str, tags, constraints: dict | None = None) -> AssetRef | None:
    """Resolve-or-generate an asset and always cache it. See module docstring."""
    constraints = dict(constraints or {})
    tags = _norm(tags)
    names = _query_names(tags, constraints)
    sem = _snippets.resolve_semantic(names)

    # Decide the route + the effective stored kind to resolve against.
    if sem is not None and not constraints.get("illustration"):
        route, eff_kind = "procedural", "snippet"
    elif kind == "lottie" or constraints.get("illustration"):
        route, eff_kind = "lottie", "lottie"
    else:
        route, eff_kind = "source", kind

    # 1. cache hit?
    resolve_tags = tags if route != "procedural" else ([sem] + tags)
    hit = resolve(eff_kind, resolve_tags, constraints)
    if hit is not None:
        return _as_ref(hit, constraints)

    # 2. generate (stop at the first route that satisfies)
    entry = None
    if route == "procedural":
        entry = _gen_procedural(sem, tags, constraints)
    elif route == "lottie":
        entry = _gen_lottie(names, tags, constraints)
        if entry is None and kind in ("icon", "img"):
            entry = _gen_source(kind, tags, constraints)  # graceful fallthrough
    else:
        entry = _gen_source(kind, tags, constraints)

    if entry is None:
        return None
    return _as_ref(entry, constraints)


# --- (a) procedural ----------------------------------------------------------
def _gen_procedural(sem: str, tags, constraints: dict) -> dict:
    """Generate (or dedupe) a procedural SVG+GSAP snippet for ``sem``."""
    factory, source = _snippets.GENERATORS[sem]
    entry = add(
        source.encode("utf-8"),
        "snippet",
        sorted(set([sem, "procedural", "ui"]) | set(tags)),
        _PROCEDURAL_LICENSE,
        "studio (procedural)",
        "studio.library.generate",
        "procedural",
        True,  # recolorable: color is a runtime opt
        id=f"snippet-{sem}",
        filename=f"{sem}.js",
        extra={"factory": factory, "semantic": sem, "default_color": "currentColor"},
    )
    return entry


# --- (b) lottie (via the lottie-master skill) --------------------------------
def _lottie_skill_dir() -> Path | None:
    """Locate the lottie-master skill (env override or known repo locations)."""
    env = os.environ.get("STUDIO_LOTTIE_SKILL")
    cands = []
    if env:
        cands.append(Path(env))
    cands += [
        config.REPO_ROOT / ".claude" / "skills" / "lottie-master",
        Path.home() / "Documents" / "lottieExperiments" / "skills" / "lottie-master",
        Path.home() / ".claude" / "skills" / "lottie-master",
    ]
    for c in cands:
        if (c / "scripts" / "lottie_gen.py").is_file():
            return c
    return None


def _gen_lottie(names, tags, constraints: dict) -> dict | None:
    """Generate an illustration-grade Lottie via the lottie-master skill.

    Runs scripts/lottie_gen.py (preset) -> scripts/lottie_optimize.py, recolors
    to the requested pack token, caches it. Returns None (degrade) when the skill
    or its scripts are unavailable — the procedural route is preferred anyway.
    """
    skill = _lottie_skill_dir()
    if skill is None:
        return None
    preset = constraints.get("preset") or (names[0] if names else "icon")
    token = constraints.get("color")
    try:
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "raw.json"
            opt = Path(td) / "opt.json"
            subprocess.run(
                ["python", str(skill / "scripts" / "lottie_gen.py"), "--preset", str(preset), "--out", str(raw)],
                check=True, capture_output=True, timeout=180,
            )
            optimizer = skill / "scripts" / "lottie_optimize.py"
            src = raw
            if optimizer.is_file():
                subprocess.run(
                    ["python", str(optimizer), "--in", str(raw), "--out", str(opt)],
                    check=True, capture_output=True, timeout=120,
                )
                src = opt
            data = src.read_text(encoding="utf-8")
            if token:
                data = _recolor_lottie(data, token)
            return add(
                data.encode("utf-8"), "lottie",
                sorted(set(["lottie"]) | set(_norm(tags))),
                "MIT", "lottie-master (generated)", "studio.library.generate:lottie-master",
                "generated", bool(token),
                filename=f"{preset}.json", id=f"lottie-{preset}",
                extra={"semantic": preset},
            )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None


def _recolor_lottie(data: str, hex_color: str) -> str:
    """Best-effort: replace a token placeholder in the Lottie JSON with hex."""
    hexc = hex_color if hex_color.startswith("#") else f"#{hex_color}"
    return data.replace("{{COLOR}}", hexc).replace("currentColor", hexc)


# --- (c) source (icons / img / audio) ---------------------------------------
# Raw, key-free sources for vector icons. Pexels/Pixabay/Freesound need API keys
# and are wired as documented seams (return None offline / without a key).
_LUCIDE_RAW = "https://raw.githubusercontent.com/lucide-icons/lucide/main/icons/{name}.svg"
_SIMPLE_RAW = "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/{name}.svg"


def _gen_source(kind: str, tags, constraints: dict, *, fetch_fn=None) -> dict | None:
    """Source an asset from an external provider with a license accept-list.

    ``fetch_fn(url) -> bytes | None`` is injectable for tests; the default uses
    urllib. Returns None when offline / unavailable / license-rejected.
    """
    names = _query_names(tags, constraints)
    if kind == "icon" and names:
        name = names[0]
        brand = "brand" in tags or "logo" in tags
        url = (_SIMPLE_RAW if brand else _LUCIDE_RAW).format(name=name)
        license = "CC0-1.0" if brand else "ISC"
        attribution = "Simple Icons (CC0 1.0)" if brand else "Lucide (ISC)"
        if license not in ACCEPT_LICENSES:
            return None
        data = (fetch_fn or _fetch)(url)
        if not data:
            return None
        return add(
            data, "icon", sorted(set(names) | set(_norm(tags))),
            license, attribution, url, "sourced",
            recolorable=not brand, filename=f"{name}.svg",
            id=(f"brand-{name}" if brand else f"icon-{name}"),
        )

    # img / sfx / music: require provider API keys; wired but degrade offline.
    # (Pexels: PEXELS_API_KEY, Pixabay: PIXABAY_API_KEY, Freesound: FREESOUND_API_KEY.)
    return None


def _fetch(url: str) -> bytes | None:
    """Default network fetch (degrades to None offline)."""
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
            if resp.status != 200:
                return None
            return resp.read()
    except Exception:
        return None


# --- (4) in-engine halftone / portrait processor -----------------------------
def halftone(source, *, ink: str = "#1f1f1e", source_id: str | None = None, contrast: float = 1.5) -> AssetRef | None:
    """Produce the deterministic 1-bit halftone derivative of a portrait, cached once.

    ``source`` may be a manifest entry (dict), a library asset id, or a file path.
    Mirrors the pack's #halftone treatment (luminance -> contrast -> 1-bit dither,
    inked pixels opaque, paper transparent) using ffmpeg's deterministic ``monob``
    dither + ``colorkey``. The result is cached with ``provenance="generated"`` and
    a ``src:<id>`` tag, so the same portrait is processed exactly once.
    """
    # Resolve the source path + a stable id.
    if isinstance(source, dict):
        entry = source
        src_path = config.ASSET_LIBRARY_DIR / entry["file"]
        src_id = source_id or entry["id"]
        src_license = entry.get("license", "Unknown")
        src_attr = entry.get("attribution", "")
    else:
        maybe = get(str(source))
        if maybe is not None:
            return halftone(maybe, ink=ink, source_id=source_id, contrast=contrast)
        src_path = Path(source)
        src_id = source_id or src_path.stem
        src_license, src_attr = "Unknown", ""

    if not src_path.is_file():
        raise GenerationError(f"halftone source not found: {src_path}")

    out_id = f"halftone-{src_id}"
    cached = get(out_id)
    if cached is not None:  # processed once -> serve cache
        return _as_ref(cached, None)

    ffmpeg = _which_ffmpeg()
    if ffmpeg is None:
        raise GenerationError("ffmpeg not available for halftone processing")

    with tempfile.TemporaryDirectory() as td:
        out_png = Path(td) / f"{out_id}.png"
        vf = (
            f"format=gray,eq=contrast={contrast}:brightness=0.05,"
            f"format=monob,format=rgba,colorkey=0xFFFFFF:0.40:0.0"
        )
        proc = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(src_path),
             "-vf", vf, "-frames:v", "1", str(out_png)],
            capture_output=True, timeout=120,
        )
        if proc.returncode != 0 or not out_png.is_file():
            raise GenerationError(f"halftone ffmpeg failed: {proc.stderr.decode('utf-8', 'ignore')[:200]}")
        entry = add(
            out_png, "img",
            ["halftone", "portrait", "1-bit", "treated", f"src:{src_id}"],
            src_license, src_attr, f"halftone:{src_id}", "generated", False,
            id=out_id, extra={"src_id": src_id, "ink": ink},
        )
    return _as_ref(entry, None)


def _which_ffmpeg() -> str | None:
    import shutil

    return shutil.which("ffmpeg")
