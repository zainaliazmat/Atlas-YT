"""Control-Room settings — niches / defaults / channels (sub-project #4).

A SINGLE dashboard-owned JSON. The dashboard reads it and PASSES the relevant values
INTO the pipeline as args at trigger time (e.g. a niche's default length) — a pure engine
NEVER reads this file globally (preserves the §3/§11 decoupling rule). The only write is a
T1 reversible PUT, validated + sanitized here before it touches disk.

Tolerant by construction (spec E13): a missing or malformed file degrades to defaults and
is left exactly as found (we parse in place, never rewrite a corrupt file behind the user's
back). `validate_settings` never raises — it drops/coerces bad rows and returns a canonical
shape, so a bad PUT can't corrupt the store.

OAuth/token handling is explicitly OUT of scope here — channels are a SHELL (identity +
the connection-state machine + the two YouTube verification flags + niche mapping). Real
tokens arrive with #6 Herald and are stored as encrypted secrets elsewhere, never here.
"""
from __future__ import annotations

import copy
import json
import pathlib

import chat_state

# The per-channel connection-state machine (spec §9): tokens die on revocation, 6-month
# idle, the 100-token/client cap, and — while unverified — a 7-day "Testing" expiry.
CONNECTION_STATES = ("disconnected", "connected", "needs-reconnect", "expired", "revoked")
LENGTH_OPTIONS = ("short", "long")
# Niche intake (#1.5): 'pick' = show Scout's candidates and let the CEO choose; 'auto' =
# take the top-ranked candidate automatically. Configurable, defaults to the safe 'pick'.
INTAKE_MODES = ("pick", "auto")

# YouTube Data API ceiling (spec §9, researched 2026-06-23): videos.insert costs 1600 units
# against a default 10,000/day PER CLOUD PROJECT — a hard, PROJECT-WIDE ~6 uploads/day SHARED
# across ALL channels (adding channels does NOT add quota). Flagged migration caveat carried.
QUOTA = {
    "daily_units": 10000,
    "insert_cost": 1600,
    "max_uploads_per_day": 6,
    "scope": "shared across ALL channels (project-wide ceiling, not per-channel)",
    "note": "Confirm the project's Console quota at build time — Google is mid-migration to a "
            "separate-bucket model that may instead cap videos.insert at ~100/day.",
}

# The two YouTube verification flags a channel must clear before public/scheduled publishing
# (spec §9): the Cloud PROJECT passes sensitive-scope verification, and each CHANNEL is
# phone-verified. The Channels shell shows both and (at #6) gates the publish action on them.
DEFAULT_SETTINGS = {
    "schema_version": "1.0",
    "niches": [],       # [{name, default_length, channel_id, default_angle, voice, style_preset}]
    "defaults": {"target_length": "short", "voice": "", "style_preset": "",
                 "intake_mode": "pick", "render_budget_sec": 600.0},
    "channels": [],     # [{channel_id, title, niche_id, connection_status, project_verified,
                        #   channel_phone_verified, scopes}]
}

DEFAULT_PATH = pathlib.Path(__file__).resolve().parent / "control_room_settings.json"


def _defaults() -> dict:
    return copy.deepcopy(DEFAULT_SETTINGS)


def load_settings(path) -> dict:
    """Read + sanitize the settings, tolerating absence/corruption (E13). Parses in place —
    a corrupt file is NEVER rewritten or renamed by a read (the dashboard is read-mostly)."""
    path = pathlib.Path(path)
    if not path.exists():
        return _defaults()
    try:
        raw = json.loads(path.read_text(errors="replace"))
    except (json.JSONDecodeError, ValueError, OSError):
        return _defaults()
    _ok, _errs, clean = validate_settings(raw if isinstance(raw, dict) else {})
    return clean


def _coerce_niche(n) -> dict | None:
    if not isinstance(n, dict):
        return None
    # reuse Scout's importable niche guard so the editor rejects the same junk the engine would
    from validate import validate_niche
    name = str(n.get("name", "")).strip()
    ok, _reason = validate_niche(name)
    if not ok:
        return None
    length = n.get("default_length")
    return {
        "name": name[:80],
        "default_length": length if length in LENGTH_OPTIONS else "short",
        "channel_id": str(n.get("channel_id", "") or "")[:64],
        "default_angle": str(n.get("default_angle", "") or "")[:160],
        "voice": str(n.get("voice", "") or "")[:64],
        "style_preset": str(n.get("style_preset", "") or "")[:64],
    }


def _coerce_channel(c) -> dict | None:
    if not isinstance(c, dict):
        return None
    state = c.get("connection_status")
    scopes = c.get("scopes")
    return {
        "channel_id": str(c.get("channel_id", "") or "")[:64],
        "title": str(c.get("title", "") or "")[:120],
        "niche_id": str(c.get("niche_id", "") or "")[:64],
        "connection_status": state if state in CONNECTION_STATES else "disconnected",
        "project_verified": bool(c.get("project_verified")),
        "channel_phone_verified": bool(c.get("channel_phone_verified")),
        "scopes": [str(s)[:80] for s in scopes][:8] if isinstance(scopes, list) else [],
    }


def validate_settings(obj) -> tuple[bool, list[str], dict]:
    """Coerce arbitrary input into the canonical settings shape. NEVER raises: bad rows are
    dropped, bad scalars coerced to a safe default. Returns (ok, errors, sanitized)."""
    out = _defaults()
    errors: list[str] = []
    if not isinstance(obj, dict):
        return False, ["settings must be an object"], out

    niches = obj.get("niches")
    if isinstance(niches, list):
        kept = [c for c in (_coerce_niche(n) for n in niches) if c]
        if len(kept) != len(niches):
            errors.append("dropped %d invalid niche row(s)" % (len(niches) - len(kept)))
        out["niches"] = kept

    channels = obj.get("channels")
    if isinstance(channels, list):
        kept_c = [c for c in (_coerce_channel(c) for c in channels) if c]
        if len(kept_c) != len(channels):
            errors.append("dropped %d invalid channel row(s)" % (len(channels) - len(kept_c)))
        out["channels"] = kept_c

    defaults = obj.get("defaults")
    if isinstance(defaults, dict):
        tl = defaults.get("target_length")
        im = defaults.get("intake_mode")
        raw_budget = (defaults or {}).get("render_budget_sec", 600.0)
        try:
            budget = float(raw_budget)
            if budget < 0:
                budget = 600.0
        except (TypeError, ValueError):
            budget = 600.0
        out["defaults"] = {
            "target_length": tl if tl in LENGTH_OPTIONS else "short",
            "voice": str(defaults.get("voice", "") or "")[:64],
            "style_preset": str(defaults.get("style_preset", "") or "")[:64],
            "intake_mode": im if im in INTAKE_MODES else "pick",
            "render_budget_sec": budget,
        }
    return (not errors), errors, out


def save_settings(path, obj) -> dict:
    """Validate + sanitize, then atomically persist. Returns the canonical saved doc."""
    _ok, _errs, clean = validate_settings(obj)
    chat_state.atomic_write_json(pathlib.Path(path), clean)
    return clean


def public_settings(path) -> dict:
    """The settings the UI consumes: the stored doc + the read-only quota ceiling and the
    enums the editor needs. No secrets are stored here, so nothing is redacted away."""
    s = load_settings(path)
    s = dict(s)
    s["quota"] = copy.deepcopy(QUOTA)
    s["connection_states"] = list(CONNECTION_STATES)
    s["length_options"] = list(LENGTH_OPTIONS)
    s["intake_modes"] = list(INTAKE_MODES)
    return s


def length_for_niche(settings: dict, niche: str | None) -> str:
    """Resolve a niche's default target length (else the global default). Called dashboard-
    side at trigger time so the value flows INTO the pipeline as an arg, not via a global."""
    default = (settings.get("defaults", {}) or {}).get("target_length", "short")
    if not niche:
        return default
    for n in settings.get("niches", []) or []:
        if n.get("name") == niche:
            return n.get("default_length") or default
    return default
