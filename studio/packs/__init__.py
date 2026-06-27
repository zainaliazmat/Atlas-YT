"""studio.packs — Design Pack loader + registry (Phase 1).

A **Design Pack** is the opinionated, self-consistent design system a video is
authored against — the thing that makes the golden reference look authored
rather than assembled. One pack lives in ``studio/design-packs/<id>/`` and
bundles:

  - ``DESIGN.md``       — the human source-of-truth for the look,
  - ``tokens.json``     — colors / type roles / motion grammar / textures,
  - ``pack.json``       — the manifest (id, name, aspect_defaults, fonts,
                          required_assets, motion_index, partials),
  - ``partials/``       — authoring fragments the Composer injects:
                          filters.html, base.css, transitions.js, ticker.js,
                          retimer.js,
  - ``motion-library/`` — reusable per-scene motion modules (grows over time).

This REPLACES atlas/'s closed-vocab style/storyboard/motion_mood_board stages:
the Composer reads a curated pack and authors directly against it.

Registry/adapter PATTERN mirrored from ``atlas/registry.py`` (AgentEntry +
``get_entry`` + a REGISTRY list), re-applied to PACKS: a ``PackEntry`` declares
id/name/dir/blurb, the registry is the ``design-packs/packs.json`` file, and
``load_pack`` resolves an entry into a fully-loaded :class:`Pack`.

Imports are stdlib-only so ``import studio.packs`` stays cheap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

# The five authoring partials every pack must ship (logical name -> filename).
REQUIRED_PARTIALS: dict[str, str] = {
    "filters": "partials/filters.html",
    "base_css": "partials/base.css",
    "transitions": "partials/transitions.js",
    "ticker": "partials/ticker.js",
    "retimer": "partials/retimer.js",
}

# Prefix marking a partial that lives in the SHARED, pack-agnostic mechanism root
# (design-packs/_shared/) rather than inside the pack dir. This is how a pack
# REUSES the common transitions/ticker/retimer instead of vendoring its own.
SHARED_PREFIX = "_shared/"


def resolve_partial_path(pack_dir: Path, rel: str) -> Path:
    """Resolve a partial's declared path to an absolute file path.

    A ``_shared/...`` path resolves against the shared-mechanism root
    (``design-packs/_shared/``) so multiple packs can point at the one canonical
    copy of the pack-agnostic JS. Everything else resolves inside the pack dir.
    """
    if rel.startswith(SHARED_PREFIX):
        return config.DESIGN_PACKS_DIR / rel
    return pack_dir / rel

# Top-level keys pack.json / tokens.json must carry (mirrors the JSON schemas in
# design-packs/_schema/). Kept as a lightweight, dependency-free contract check;
# the JSON Schema files are the formal spec.
_PACK_REQUIRED_KEYS = ("id", "name", "aspect_defaults", "fonts", "required_assets", "motion_index")
_TOKENS_REQUIRED_KEYS = ("colors", "type", "motion", "textures")


class PackError(Exception):
    """Raised when a pack is missing, malformed, or fails its contract check."""


@dataclass(frozen=True)
class PackEntry:
    """One row of the Design Pack registry (the analog of atlas's AgentEntry)."""

    id: str
    name: str
    dir: str
    blurb: str = ""

    def path(self) -> Path:
        """Absolute path to this pack's directory."""
        return config.DESIGN_PACKS_DIR / self.dir


@dataclass(frozen=True)
class Pack:
    """A loaded, validated Design Pack ready for the Composer.

    Exposes the parsed manifest + tokens and resolves the partials' paths. This
    is data only — the Composer (Phase 3) reads ``partials`` / ``tokens`` to
    author one index.html against the pack.
    """

    id: str
    name: str
    dir: Path
    manifest: dict
    tokens: dict
    partials: dict[str, Path] = field(default_factory=dict)

    # --- partial access ------------------------------------------------------
    def partial(self, name: str) -> Path:
        """Absolute path to a named partial (e.g. ``"transitions"``)."""
        if name not in self.partials:
            raise PackError(f"pack {self.id!r} has no partial {name!r}")
        return self.partials[name]

    def read_partial(self, name: str) -> str:
        """Read a named partial's text content."""
        return self.partial(name).read_text(encoding="utf-8")

    # --- convenience handles -------------------------------------------------
    @property
    def design_md(self) -> Path:
        return self.dir / "DESIGN.md"

    @property
    def motion_library_dir(self) -> Path:
        return self.dir / "motion-library"

    @property
    def colors(self) -> dict:
        return self.tokens.get("colors", {})

    @property
    def fps(self) -> int:
        return int(self.tokens.get("motion", {}).get("fps", config.DEFAULT_FPS))


# --- registry ---------------------------------------------------------------
def _read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        raise PackError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PackError(f"invalid JSON in {path}: {exc}") from exc


def _registry_path() -> Path:
    return config.PACK_REGISTRY_PATH


def _load_registry() -> dict:
    """Read the packs.json registry (returns ``{}``-shaped doc if absent)."""
    path = _registry_path()
    if not path.exists():
        return {"schema_version": "1.0", "packs": []}
    return _read_json(path)


def list_packs() -> list[PackEntry]:
    """Return all registered packs (reads ``design-packs/packs.json``)."""
    doc = _load_registry()
    out: list[PackEntry] = []
    for row in doc.get("packs", []):
        out.append(
            PackEntry(
                id=row["id"],
                name=row.get("name", row["id"]),
                dir=row.get("dir", row["id"]),
                blurb=row.get("blurb", ""),
            )
        )
    return out


def get_entry(pack_id: str) -> PackEntry | None:
    """Resolve a pack id to its registry entry (case-insensitive), or None."""
    key = pack_id.strip().lower()
    for entry in list_packs():
        if entry.id.lower() == key:
            return entry
    return None


def discover_packs() -> list[str]:
    """Scan ``design-packs/*/pack.json`` and return the ids found on disk.

    The registry (packs.json) is the source of truth for ``list_packs``; this is
    the auto-discovery helper used to (re)build it via :func:`register_pack`.
    """
    base = config.DESIGN_PACKS_DIR
    if not base.exists():
        return []
    ids: list[str] = []
    for child in sorted(p for p in base.iterdir() if p.is_dir()):
        manifest = child / "pack.json"
        if manifest.exists():
            try:
                ids.append(_read_json(manifest)["id"])
            except (PackError, KeyError):
                continue
    return ids


# --- loading + validation ----------------------------------------------------
def _validate_keys(doc: dict, required: tuple[str, ...], what: str, where: Path) -> None:
    missing = [k for k in required if k not in doc]
    if missing:
        raise PackError(f"{what} {where} missing keys: {', '.join(missing)}")


def load_pack(pack_id: str) -> Pack:
    """Resolve ``pack_id`` via the registry and load it into a :class:`Pack`.

    Reads ``pack.json`` + ``tokens.json``, validates the required top-level keys
    against the pack contract, resolves the five required partials (and any
    extra ones declared in ``pack.json``'s ``partials`` map), and confirms they
    exist on disk. Raises :class:`PackError` with a clear message otherwise.
    """
    entry = get_entry(pack_id)
    # Fall back to a direct on-disk lookup so a pack present on disk but not yet
    # in the registry still loads (auto-discovery).
    pack_dir = entry.path() if entry else (config.DESIGN_PACKS_DIR / pack_id)
    if not pack_dir.is_dir():
        known = ", ".join(e.id for e in list_packs()) or "<none>"
        raise PackError(f"unknown pack {pack_id!r}; registered packs: {known}")

    manifest = _read_json(pack_dir / "pack.json")
    tokens = _read_json(pack_dir / "tokens.json")
    _validate_keys(manifest, _PACK_REQUIRED_KEYS, "pack.json", pack_dir / "pack.json")
    _validate_keys(tokens, _TOKENS_REQUIRED_KEYS, "tokens.json", pack_dir / "tokens.json")

    # Resolve partials: the required set, plus any declared in pack.json.
    declared = dict(REQUIRED_PARTIALS)
    declared.update(manifest.get("partials", {}))
    partials: dict[str, Path] = {}
    for name, rel in declared.items():
        path = resolve_partial_path(pack_dir, rel)
        if not path.exists():
            raise PackError(f"pack {manifest['id']!r} missing partial {name!r}: {path}")
        partials[name] = path

    if not (pack_dir / "DESIGN.md").exists():
        raise PackError(f"pack {manifest['id']!r} missing DESIGN.md")

    return Pack(
        id=manifest["id"],
        name=manifest.get("name", manifest["id"]),
        dir=pack_dir,
        manifest=manifest,
        tokens=tokens,
        partials=partials,
    )


def register_pack(
    pack_id: str,
    *,
    name: str | None = None,
    dir: str | None = None,
    blurb: str = "",
) -> PackEntry:
    """Add (or update) a pack in the ``design-packs/packs.json`` registry.

    Idempotent on ``pack_id``. Validates the pack loads before registering, then
    writes the registry atomically. Returns the resulting :class:`PackEntry`.
    """
    pdir = dir or pack_id
    # Prefer the manifest's own name/id when present (and validate it loads).
    manifest_path = config.DESIGN_PACKS_DIR / pdir / "pack.json"
    if manifest_path.exists():
        manifest = _read_json(manifest_path)
        pack_id = manifest.get("id", pack_id)
        name = name or manifest.get("name", pack_id)
        blurb = blurb or manifest.get("blurb", "")
    name = name or pack_id

    doc = _load_registry()
    rows = [r for r in doc.get("packs", []) if r.get("id") != pack_id]
    rows.append({"id": pack_id, "name": name, "dir": pdir, "blurb": blurb})
    rows.sort(key=lambda r: r["id"])
    doc["packs"] = rows
    doc.setdefault("schema_version", "1.0")

    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)

    return PackEntry(id=pack_id, name=name, dir=pdir, blurb=blurb)
