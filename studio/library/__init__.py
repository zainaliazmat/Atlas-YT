"""studio.library — the shared Asset Library (Phase 2).

A curated, license-cleared, local-first cache of reusable assets — fonts, icons,
textures, lottie, code snippets, SFX, music beds, and images — plus a manifest
(``asset-library/library.json``) recording provenance + license for each. The
Composer and VO/audio stages resolve what they need by ``(kind, tags,
constraints)`` against this cache instead of doing a live, license-risky web hunt
per video.

Storage layout (under ``asset-library/`` at repo root)::

    fonts/  icons/  textures/  lottie/  snippets/  audio/sfx/  audio/music/  img/
    library.json          # the manifest (index of every entry)
    library.schema.json   # the manifest contract

Each manifest entry::

    { id, kind, tags[], file, license, attribution, source, sha256,
      recolorable, provenance, created_at, used_in[],  (+ optional: duration,
      mood[], family, weight) }

Public API:
  - ``resolve(kind, tags, constraints=None)`` — best cached match or None.
  - ``add(file_or_bytes, kind, tags, license, attribution, source, provenance,
    recolorable, ...)`` — hash + copy + append (atomic); dedupes on sha256.
  - ``promote(project_asset_path, ...)`` — pull a per-project asset into the
    shared cache after a final render.
  - ``recolor(entry, hex)`` — recolored copy of a recolorable SVG/snippet; never
    mutates the cached original.
  - ``list_assets(kind=None, tags=None)`` and ``gc()``.

Clearance policy intent mirrors audio-designer/audio_engine.py (license is a
first-class field; nothing uncleared should be resolved into a render). Imports
are stdlib-only so ``import studio.library`` stays cheap.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import config

SCHEMA_VERSION = "1.0"

# kind -> storage subfolder (relative to asset-library/)
KIND_DIRS: dict[str, str] = {
    "font": "fonts",
    "icon": "icons",
    "texture": "textures",
    "lottie": "lottie",
    "snippet": "snippets",
    "sfx": "audio/sfx",
    "music": "audio/music",
    "img": "img",
}

# kinds whose bytes are text we can recolor (string replace).
RECOLORABLE_KINDS = {"icon", "snippet", "texture"}


class LibraryError(Exception):
    """Raised on malformed input or a broken library state."""


# --- path + manifest helpers (read config lazily so tests can monkeypatch) ---
def _lib_dir() -> Path:
    return config.ASSET_LIBRARY_DIR


def _manifest_path() -> Path:
    return _lib_dir() / "library.json"


def _load_manifest() -> dict:
    path = _manifest_path()
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "assets": []}
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise LibraryError(f"corrupt manifest {path}: {exc}") from exc


def _save_manifest(doc: dict) -> None:
    path = _manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)  # atomic on the same filesystem


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9._-]+", "-", text.strip().lower()).strip("-._")
    return s or "asset"


def _norm_tags(tags) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        tags = tags.split(",")
    return [t.strip().lower() for t in tags if str(t).strip()]


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


@dataclass
class _Input:
    data: bytes
    suffix: str
    stem: str


def _read_input(file_or_bytes, filename: str | None) -> _Input:
    """Normalize a path-or-bytes input into (data, suffix, stem)."""
    if isinstance(file_or_bytes, (bytes, bytearray)):
        if not filename:
            raise LibraryError("filename is required when adding raw bytes")
        p = Path(filename)
        return _Input(bytes(file_or_bytes), p.suffix, p.stem)
    src = Path(file_or_bytes)
    if not src.is_file():
        raise LibraryError(f"not a file: {src}")
    name = Path(filename) if filename else src
    return _Input(src.read_bytes(), name.suffix, name.stem)


# --- add / dedupe ------------------------------------------------------------
def add(
    file_or_bytes,
    kind: str,
    tags,
    license: str,
    attribution: str,
    source: str,
    provenance: str,
    recolorable: bool,
    *,
    id: str | None = None,
    filename: str | None = None,
    extra: dict | None = None,
    used_in=None,
    now: datetime | None = None,
) -> dict:
    """Hash, copy into the cache, and append to the manifest (atomic).

    If an entry with the same sha256 already exists, that entry is returned
    unchanged (dedupe) — nothing is copied or re-appended. Returns the entry.
    """
    if kind not in KIND_DIRS:
        raise LibraryError(f"unknown kind {kind!r}; expected one of {sorted(KIND_DIRS)}")
    if provenance not in ("sourced", "generated", "procedural"):
        raise LibraryError(f"invalid provenance {provenance!r}")

    inp = _read_input(file_or_bytes, filename)
    sha = hashlib.sha256(inp.data).hexdigest()

    doc = _load_manifest()
    assets = doc.setdefault("assets", [])

    # dedupe on content hash
    for e in assets:
        if e.get("sha256") == sha:
            # opportunistically record a new usage if provided
            if used_in:
                merged = sorted(set(e.get("used_in", [])) | set(_norm_tags(used_in) if isinstance(used_in, str) else used_in))
                if merged != e.get("used_in"):
                    e["used_in"] = merged
                    _save_manifest(doc)
            return e

    # unique id
    base_id = _slug(id or inp.stem)
    existing_ids = {e["id"] for e in assets}
    new_id = base_id
    n = 2
    while new_id in existing_ids:
        new_id = f"{base_id}-{n}"
        n += 1

    # store under <subdir>/<id><suffix> (id is unique -> collision-free)
    subdir = KIND_DIRS[kind]
    rel = f"{subdir}/{new_id}{inp.suffix}"
    dest = _lib_dir() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(inp.data)

    entry = {
        "id": new_id,
        "kind": kind,
        "tags": _norm_tags(tags),
        "file": rel,
        "license": license,
        "attribution": attribution,
        "source": source,
        "sha256": sha,
        "recolorable": bool(recolorable),
        "provenance": provenance,
        "created_at": _now_iso(now),
        "used_in": list(used_in) if used_in else [],
    }
    # optional typed fields (duration/mood/family/weight for audio/fonts;
    # factory/semantic/default_color for procedural snippets; src_id for
    # generated derivatives). Never clobber the canonical keys above.
    for k, v in (extra or {}).items():
        if v is not None and k not in entry:
            entry[k] = v

    assets.append(entry)
    doc["schema_version"] = doc.get("schema_version", SCHEMA_VERSION)
    _save_manifest(doc)
    return entry


def promote(
    project_asset_path,
    *,
    kind: str,
    tags,
    license: str,
    attribution: str,
    source: str,
    recolorable: bool = False,
    provenance: str = "generated",
    used_in=None,
    remove_source: bool = False,
    now: datetime | None = None,
) -> dict:
    """Pull a per-project asset into the shared library after a final render.

    Thin wrapper over :func:`add` defaulting provenance to ``generated`` (the
    asset was produced inside a project). Dedupes on content. With
    ``remove_source=True`` the original project file is unlinked once cached.
    """
    src = Path(project_asset_path)
    entry = add(
        src, kind, tags, license, attribution, source, provenance, recolorable,
        used_in=used_in, now=now,
    )
    if remove_source and src.is_file():
        # only remove if we actually cached a copy elsewhere
        if (_lib_dir() / entry["file"]).resolve() != src.resolve():
            src.unlink()
    return entry


# --- resolve -----------------------------------------------------------------
def _passes_hard(entry: dict, c: dict | None) -> bool:
    if not c:
        return True
    if "recolorable" in c and bool(entry.get("recolorable")) != bool(c["recolorable"]):
        return False
    dur = entry.get("duration")
    if "min_duration" in c and (dur is None or dur < c["min_duration"]):
        return False
    if "max_duration" in c and (dur is None or dur > c["max_duration"]):
        return False
    if "license_in" in c and entry.get("license") not in c["license_in"]:
        return False
    if "tags_all" in c and not set(_norm_tags(c["tags_all"])).issubset(set(entry.get("tags", []))):
        return False
    return True


_SEMANTIC_CONSTRAINTS = ("mood", "name", "duration")


def _score(entry: dict, tags: list[str], c: dict) -> float:
    score = 0.0
    etags = set(entry.get("tags", []))
    score += len(etags & set(tags))  # tag overlap
    if "mood" in c:
        emood = set(entry.get("mood", [])) | etags
        score += len(set(_norm_tags(c["mood"])) & emood)
    if "name" in c:
        nm = str(c["name"]).strip().lower()
        if nm == entry["id"].lower() or nm in etags:
            score += 3.0
    if "duration" in c and entry.get("duration") is not None:
        target = float(c["duration"])
        score += 1.0 / (1.0 + abs(float(entry["duration"]) - target))
    return score


def resolve(kind: str, tags, constraints: dict | None = None) -> dict | None:
    """Return the best matching cached asset of ``kind``, or None.

    Matching = kind filter + hard-constraint filter, then a score of tag overlap
    plus constraint fit (mood overlap, icon name, music/sfx duration closeness).
    Ties break deterministically by ``id`` (ascending). When the query carries
    semantic intent (tags or a mood/name/duration constraint) but nothing scores,
    returns None rather than an arbitrary asset.
    """
    tags = _norm_tags(tags)
    c = constraints or {}
    cands = [e for e in _load_manifest().get("assets", []) if e.get("kind") == kind and _passes_hard(e, c)]
    if not cands:
        return None
    scored = [(_score(e, tags, c), e["id"], e) for e in cands]
    scored.sort(key=lambda t: (-t[0], t[1]))  # score desc, id asc -> deterministic
    best_score, _, best = scored[0]

    has_semantic = bool(tags) or any(k in c for k in _SEMANTIC_CONSTRAINTS)
    if has_semantic and best_score <= 0:
        return None
    return best


# --- recolor -----------------------------------------------------------------
def recolor(entry: dict, hex_color: str, *, token: str = "currentColor", out_path=None) -> str:
    """Return a recolored copy of a recolorable SVG/snippet asset.

    Replaces ``token`` (default ``currentColor``) with ``hex_color`` in the
    asset's text. NEVER mutates the cached original — it reads the file and
    returns a new string (optionally also writing it to ``out_path``).
    """
    if not entry.get("recolorable"):
        raise LibraryError(f"asset {entry.get('id')!r} is not recolorable")
    if entry.get("kind") not in RECOLORABLE_KINDS:
        raise LibraryError(f"asset {entry.get('id')!r} (kind {entry.get('kind')!r}) is not a text asset")
    if not re.fullmatch(r"#?[0-9a-fA-F]{3,8}", hex_color):
        raise LibraryError(f"invalid hex color {hex_color!r}")
    hex_color = hex_color if hex_color.startswith("#") else f"#{hex_color}"

    src = _lib_dir() / entry["file"]
    text = src.read_text(encoding="utf-8")
    recolored = text.replace(token, hex_color)
    if out_path is not None:
        Path(out_path).write_text(recolored, encoding="utf-8")
    return recolored


# --- list + status + gc ------------------------------------------------------
def list_assets(kind: str | None = None, tags=None) -> list[dict]:
    """Return manifest entries, optionally filtered by kind and/or required tags."""
    want = set(_norm_tags(tags))
    out = []
    for e in _load_manifest().get("assets", []):
        if kind and e.get("kind") != kind:
            continue
        if want and not want.issubset(set(e.get("tags", []))):
            continue
        out.append(e)
    return out


def get(asset_id: str) -> dict | None:
    """Return the manifest entry with this id, or None."""
    for e in _load_manifest().get("assets", []):
        if e.get("id") == asset_id:
            return e
    return None


def has_font(family: str) -> bool:
    """True if a font of this family is cached (used by studio.packs.validate)."""
    fam = (family or "").strip().lower()
    return any(
        e.get("kind") == "font" and (e.get("family", "").lower() == fam or fam in e.get("tags", []))
        for e in _load_manifest().get("assets", [])
    )


def library_status() -> dict:
    """Health summary: counts by kind + clearance flags."""
    assets = _load_manifest().get("assets", [])
    by_kind: dict[str, int] = {}
    missing_files: list[str] = []
    unknown_license: list[str] = []
    for e in assets:
        by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        if not (_lib_dir() / e["file"]).exists():
            missing_files.append(e["id"])
        if str(e.get("license", "")).lower() in ("", "unknown"):
            unknown_license.append(e["id"])
    return {
        "total": len(assets),
        "by_kind": by_kind,
        "missing_files": missing_files,
        "unknown_license": unknown_license,
    }


def gc() -> dict:
    """Garbage-collect the library.

    Drops manifest entries whose backing file is gone, and deletes stored files
    under the kind subfolders that no entry references (orphans). Returns a
    summary of what was removed.
    """
    doc = _load_manifest()
    assets = doc.get("assets", [])
    kept, removed_entries = [], []
    referenced: set[Path] = set()
    for e in assets:
        path = _lib_dir() / e["file"]
        if path.exists():
            kept.append(e)
            referenced.add(path.resolve())
        else:
            removed_entries.append(e["id"])

    removed_files: list[str] = []
    for sub in KIND_DIRS.values():
        d = _lib_dir() / sub
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if not f.is_file() or f.name == ".gitkeep":
                continue
            if f.resolve() not in referenced:
                removed_files.append(str(f.relative_to(_lib_dir())))
                f.unlink()

    if removed_entries:
        doc["assets"] = kept
        _save_manifest(doc)
    return {"removed_entries": removed_entries, "removed_files": removed_files}
