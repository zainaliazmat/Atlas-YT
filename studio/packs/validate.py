"""studio.packs.validate — well-formedness checks for a Design Pack.

``validate_pack(pack_id)`` answers "is this pack structurally sound enough for
the Composer to author against?" It is deliberately static (no rendering): it
checks files, token conformance, that the shared mechanism partials export what
the Composer calls, and that every referenced font is accounted for.

Checks performed:
  1. required files present  — DESIGN.md, pack.json, tokens.json, and the five
     partials (resolved, so ``_shared/`` reuse is followed).
  2. tokens.json conforms    — validated against design-packs/_schema/tokens.schema.json
     (via jsonschema when available; structural fallback otherwise).
  3. transitions.js exports   the four transition verbs (txPush/txTile/txWhip/txCut).
  4. retimer.js exports       makeRetimer.
  5. fonts resolvable         — every font family used in tokens.type exists in the
     asset library OR is listed in pack.required_assets (via pack.fonts -> file).

Returns a :class:`ValidationResult`; never raises for a merely-invalid pack
(only for a genuinely missing pack id). Wired to the CLI as
``python -m studio.run packs --validate <id>``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from . import Pack, PackError, load_pack

# The transition verbs the Composer relies on (greenSwipe is an internal helper).
REQUIRED_TRANSITIONS = ("txPush", "txTile", "txWhip", "txCut")
TOKENS_SCHEMA_PATH = config.DESIGN_PACKS_DIR / "_schema" / "tokens.schema.json"


@dataclass
class ValidationResult:
    """Outcome of validating one pack."""

    pack_id: str
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)

    def _fail(self, check: str, msg: str) -> None:
        self.ok = False
        self.checks[check] = False
        self.errors.append(msg)

    def _pass(self, check: str) -> None:
        self.checks.setdefault(check, True)

    def _warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _strip_js_comments(src: str) -> str:
    """Remove /* block */ and // line comments so we scan executable code only."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"//.*", "", src)
    return src


# --- individual checks -------------------------------------------------------
def _check_files(pack: Pack, res: ValidationResult) -> None:
    check = "files_present"
    ok = True
    if not pack.design_md.exists():
        res._fail(check, f"missing DESIGN.md: {pack.design_md}")
        ok = False
    for name, path in pack.partials.items():
        if not path.exists():
            res._fail(check, f"missing partial {name!r}: {path}")
            ok = False
    # pack.json / tokens.json existence is implied by a successful load_pack(),
    # but assert anyway for a standalone signal.
    for fname in ("pack.json", "tokens.json"):
        if not (pack.dir / fname).exists():
            res._fail(check, f"missing {fname}")
            ok = False
    if ok:
        res._pass(check)


def _check_tokens(pack: Pack, res: ValidationResult) -> None:
    check = "tokens_conform"
    # Prefer JSON Schema validation when both the schema and jsonschema exist.
    schema = None
    if TOKENS_SCHEMA_PATH.exists():
        try:
            schema = json.loads(TOKENS_SCHEMA_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            res._warn(f"tokens schema unreadable, using structural check: {exc}")
    if schema is not None:
        try:
            import jsonschema  # optional dependency

            try:
                jsonschema.validate(pack.tokens, schema)
                res._pass(check)
                return
            except jsonschema.ValidationError as exc:
                res._fail(check, f"tokens.json does not conform: {exc.message} (at {list(exc.path)})")
                return
        except ImportError:
            res._warn("jsonschema not installed; using structural token check")

    # Structural fallback (dependency-free).
    tok = pack.tokens
    ok = True
    colors = tok.get("colors")
    if not isinstance(colors, dict) or not colors:
        res._fail(check, "tokens.colors must be a non-empty object")
        ok = False
    typ = tok.get("type")
    if not isinstance(typ, dict) or not typ:
        res._fail(check, "tokens.type must be a non-empty object")
        ok = False
    else:
        for role, spec in typ.items():
            if not isinstance(spec, dict) or "font" not in spec or "weight" not in spec:
                res._fail(check, f"tokens.type.{role} must have font + weight")
                ok = False
    motion = tok.get("motion")
    if not isinstance(motion, dict):
        res._fail(check, "tokens.motion must be an object")
        ok = False
    else:
        if not isinstance(motion.get("fps"), int):
            res._fail(check, "tokens.motion.fps must be an integer")
            ok = False
        if not isinstance(motion.get("eases"), list) or not motion.get("eases"):
            res._fail(check, "tokens.motion.eases must be a non-empty array")
            ok = False
        if "budget" not in motion:
            res._fail(check, "tokens.motion.budget is required")
            ok = False
    if not isinstance(tok.get("textures"), list):
        res._fail(check, "tokens.textures must be an array")
        ok = False
    if ok:
        res._pass(check)


def _check_transitions(pack: Pack, res: ValidationResult) -> None:
    check = "transitions_export"
    try:
        code = _strip_js_comments(pack.read_partial("transitions"))
    except PackError as exc:
        res._fail(check, str(exc))
        return
    ok = True
    for verb in REQUIRED_TRANSITIONS:
        if verb not in code:
            res._fail(check, f"transitions.js does not export {verb}")
            ok = False
    if ok:
        res._pass(check)


def _check_retimer(pack: Pack, res: ValidationResult) -> None:
    check = "retimer_export"
    try:
        code = _strip_js_comments(pack.read_partial("retimer"))
    except PackError as exc:
        res._fail(check, str(exc))
        return
    if "makeRetimer" in code and re.search(r"function\s+makeRetimer\s*\(", code):
        res._pass(check)
    else:
        res._fail(check, "retimer.js does not export makeRetimer")


def _library_has_font(family: str) -> bool:
    """Best-effort asset-library lookup. The library is Phase 2 (stub), so this
    is False today; wired so validation upgrades automatically once it lands."""
    try:
        from .. import library  # noqa: F401

        resolver = getattr(library, "has_font", None)
        if callable(resolver):
            return bool(resolver(family))
    except Exception:
        return False
    return False


def _check_fonts(pack: Pack, res: ValidationResult) -> None:
    check = "fonts_resolvable"
    manifest = pack.manifest
    # family -> declared font entry (carries the file path)
    fonts_by_family = {f.get("family"): f for f in manifest.get("fonts", [])}
    # file refs declared as font assets
    font_refs = {
        a.get("ref")
        for a in manifest.get("required_assets", [])
        if a.get("kind") == "font"
    }

    used: set[str] = set()
    for spec in pack.tokens.get("type", {}).values():
        fam = spec.get("font") if isinstance(spec, dict) else None
        if fam:
            used.add(fam)

    ok = True
    for fam in sorted(used):
        if _library_has_font(fam):
            continue
        decl = fonts_by_family.get(fam)
        if not decl:
            res._fail(
                check,
                f"font {fam!r} used in tokens.type is neither declared in pack.fonts "
                f"nor resolvable in the asset library",
            )
            ok = False
            continue
        file_ref = decl.get("file")
        if file_ref and file_ref in font_refs:
            continue
        res._fail(
            check,
            f"font {fam!r} ({file_ref}) is not listed in required_assets nor in the asset library",
        )
        ok = False
    if ok:
        res._pass(check)


# --- public entry ------------------------------------------------------------
def validate_pack(pack_id: str) -> ValidationResult:
    """Validate a pack is well-formed. Returns a :class:`ValidationResult`.

    Raises :class:`PackError` only if the pack id cannot be loaded at all
    (missing directory / unparseable manifest). A pack that loads but is
    malformed comes back with ``ok=False`` and populated ``errors``.
    """
    pack = load_pack(pack_id)  # may raise PackError for a truly-missing pack
    res = ValidationResult(pack_id=pack.id)
    _check_files(pack, res)
    _check_tokens(pack, res)
    _check_transitions(pack, res)
    _check_retimer(pack, res)
    _check_fonts(pack, res)
    return res
