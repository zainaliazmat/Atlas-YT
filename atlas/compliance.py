"""The pre-publish COMPLIANCE GATE — the hard line before anything can go public.

It reads a finished project's artifacts and BLOCKS unless every one of these holds:
  * fact-check passed (verdict == "pass");
  * every visual asset is allowlisted (CC0 / PD / CC-BY / CC-BY-SA), attributed
    where the license demands it, has a LOCAL file, and is `cleared`;
  * music/SFX tracks are licensed (not placeholder/unlicensed);
  * NO real-person likeness rides on an un-cleared asset;
  * the advertiser-friendly + originality/transformation checklist passes.

It is deterministic and read-only — it judges artifacts, it never edits them — and
it emits a human-readable report so the CEO sees exactly why a video can or can't go
out. Like the rubric, this gate is a guarantee, not a suggestion: Atlas cannot
approve a block away (the publish flow refuses to upload-to-public a failed gate).
"""
from __future__ import annotations

from pathlib import Path

import chat_state

# The ONLY licenses that may ship. NC (non-commercial) and ND (no-derivatives) are
# excluded on purpose — they're not monetization/transformation-safe.
ALLOWLIST = ("CC0", "PD", "CC-BY", "CC-BY-SA")
_NEEDS_ATTRIBUTION = {"CC-BY", "CC-BY-SA"}

# Phrases in an asset's flag/attribution/note that signal a real-person likeness
# risk which must be explicitly cleared before air.
_LIKENESS_MARKERS = ("identifiable people", "identifiable person", "real person",
                     "real people", "likeness", "recognizable face",
                     "recognizable person", "identifiable individuals")

# A minimal advertiser-unfriendly wordlist (YouTube's monetization no-gos). This is a
# floor, not a substitute for review — but it catches the obvious.
_ADV_UNFRIENDLY = ("fuck", "shit", "bitch", "porn", "nsfw", "gore", "behead",
                   "slur", "kill yourself", "suicide")


def normalize_license(raw: str) -> str | None:
    """Map a free-text license label to its allowlisted canonical form, or None if
    it isn't clearly one of CC0/PD/CC-BY/CC-BY-SA (NC/ND are rejected as None)."""
    s = (raw or "").lower()
    if not s:
        return None
    # reject non-commercial / no-derivatives outright
    if "-nc" in s or " nc " in s or "noncommercial" in s or "non-commercial" in s:
        return None
    if "-nd" in s or "noderiv" in s or "no-deriv" in s:
        return None
    if "cc0" in s or "creative commons zero" in s or "publicdomain/zero" in s:
        return "CC0"
    if "public domain" in s or "publicdomain/mark" in s or s.strip() in ("pd", "pdm"):
        return "PD"
    if "by-sa" in s or "by sa" in s or "attribution-sharealike" in s \
            or "attribution share" in s:
        return "CC-BY-SA"
    if "cc-by" in s or "cc by" in s or s.startswith("by ") or "attribution" in s:
        return "CC-BY"
    return None


def _load(pdir: Path, *rel) -> dict | None:
    return chat_state.load_json(pdir.joinpath(*rel), None)


def _check_fact(pdir: Path) -> dict:
    rep = _load(pdir, "factcheck_report.json")
    if not isinstance(rep, dict):
        return {"name": "fact_check", "passed": False,
                "blockers": ["fact-check: no factcheck_report.json — unverified"]}
    if rep.get("verdict") != "pass":
        return {"name": "fact_check", "passed": False,
                "blockers": [f"fact-check verdict is {rep.get('verdict')!r}, not 'pass'"]}
    return {"name": "fact_check", "passed": True, "blockers": []}


def _check_visual_licenses(pdir: Path) -> dict:
    man = _load(pdir, "asset_manifest.json")
    if not isinstance(man, dict) or not isinstance(man.get("assets"), list):
        return {"name": "visual_licenses", "passed": False,
                "blockers": ["assets: no asset_manifest.json"]}
    blockers = []
    for a in man["assets"]:
        aid = a.get("asset_id", "?")
        # type:diagram assets are composed in-engine (no external file/license).
        if a.get("type") == "diagram":
            continue
        canon = normalize_license(a.get("license", ""))
        if canon is None:
            blockers.append(f"asset {aid}: license {a.get('license')!r} not in allowlist "
                            f"({'/'.join(ALLOWLIST)})")
        if a.get("status") != "cleared":
            blockers.append(f"asset {aid}: status {a.get('status')!r} (not 'cleared')")
        uri = a.get("uri", "")
        if not uri or not (pdir / uri).exists():
            blockers.append(f"asset {aid}: no local file at {uri!r}")
        if canon in _NEEDS_ATTRIBUTION and not (a.get("attribution") or "").strip():
            blockers.append(f"asset {aid}: {canon} requires attribution (missing)")
    return {"name": "visual_licenses", "passed": not blockers, "blockers": blockers}


def _check_audio_licenses(pdir: Path) -> dict:
    man = _load(pdir, "audio", "audio_manifest.json") or _load(pdir, "audio_manifest.json")
    if not isinstance(man, dict):
        return {"name": "audio_licenses", "passed": True,
                "blockers": [], "note": "no audio manifest (no music/sfx to clear)"}
    blockers = []
    for t in man.get("tracks", []):
        role = t.get("role")
        if role not in ("music", "sfx"):
            continue                       # narration is engine TTS — nothing to license
        lic = (t.get("license") or "").lower()
        if t.get("status") == "placeholder" or not lic or "unlicensed" in lic \
                or "placeholder" in lic:
            blockers.append(f"{role} track: not licensed (status {t.get('status')!r}, "
                            f"license {t.get('license')!r})")
    return {"name": "audio_licenses", "passed": not blockers, "blockers": blockers}


def _check_likeness(pdir: Path, overrides: dict) -> dict:
    if overrides.get("likeness_cleared"):
        return {"name": "no_real_person_likeness", "passed": True, "blockers": []}
    man = _load(pdir, "asset_manifest.json")
    blockers = []
    if isinstance(man, dict):
        for a in man.get("assets", []):
            hay = " ".join(str(a.get(k, "")) for k in ("flag", "attribution", "note")).lower()
            if any(m in hay for m in _LIKENESS_MARKERS):
                blockers.append(f"asset {a.get('asset_id','?')}: possible real-person "
                                "likeness not cleared")
    return {"name": "no_real_person_likeness", "passed": not blockers, "blockers": blockers}


def _script_text(pdir: Path) -> str:
    s = _load(pdir, "script.json")
    if not isinstance(s, dict):
        return ""
    parts = [str(s.get("hook", "")), str(s.get("cta", ""))]
    for sc in s.get("scenes", []):
        parts += [str(sc.get("narration", "")), str(sc.get("on_screen_text", ""))]
    return " ".join(parts)


def _check_advertiser_friendly(pdir: Path) -> dict:
    text = _script_text(pdir).lower()
    hits = [w for w in _ADV_UNFRIENDLY if w in text]
    blockers = ([f"advertiser-friendly: script contains flagged terms {hits}"] if hits else [])
    return {"name": "advertiser_friendly", "passed": not blockers, "blockers": blockers}


def _check_originality(pdir: Path) -> dict:
    s = _load(pdir, "script.json")
    has_narration = isinstance(s, dict) and any(
        (sc.get("narration") or "").strip() for sc in s.get("scenes", []))
    if not has_narration:
        return {"name": "originality_transformation", "passed": False,
                "blockers": ["originality: no original narration/script — not "
                             "transformative"]}
    return {"name": "originality_transformation", "passed": True, "blockers": []}


def check(project_dir: str | Path, *, overrides: dict | None = None) -> dict:
    """Run the full gate over a project dir. Returns a report dict with per-check
    results, a flat blocker list, and an overall `passed`."""
    pdir = Path(project_dir)
    overrides = overrides or {}
    checks = [
        _check_fact(pdir),
        _check_visual_licenses(pdir),
        _check_audio_licenses(pdir),
        _check_likeness(pdir, overrides),
        _check_advertiser_friendly(pdir),
        _check_originality(pdir),
    ]
    blockers = [b for c in checks for b in c["blockers"]]
    return {"slug": pdir.name, "passed": not blockers, "checks": checks,
            "blockers": blockers}


def format_report(rep: dict) -> str:
    """A human-readable compliance report the CEO reads before approving a publish."""
    head = "✅ COMPLIANCE: PASS" if rep["passed"] else "⛔ COMPLIANCE: BLOCKED"
    lines = [f"{head} — project '{rep.get('slug','?')}'", "=" * 56]
    for c in rep["checks"]:
        mark = "✓" if c["passed"] else "✗"
        note = f"  ({c['note']})" if c.get("note") else ""
        lines.append(f"  {mark} {c['name']}{note}")
        for b in c["blockers"]:
            lines.append(f"      ⛔ {b}")
    if rep["passed"]:
        lines.append("\nCleared to upload (unlisted). Public requires the board's yes.")
    else:
        lines.append(f"\n{len(rep['blockers'])} blocker(s) — fix and re-run the gate. "
                     "This video does NOT ship.")
    return "\n".join(lines)
