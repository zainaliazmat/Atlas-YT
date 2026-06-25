"""The T3 publish package — the EXACT final package a human reviews before anything could
go live (spec §4 T3, §9 Herald, edge cases E8). Read-only and assembled from artifacts +
settings; it FIRES NOTHING. Real publishing arrives with #6 Herald — this module is the
review SHELL + the enforced checkpoint that proves the package and surfaces every blocker.

The §4 T3 invariant the shape encodes: a human approves the *exact* package
(title/description/tags/thumbnail/visibility/schedule); scheduling only sets the go-live
time AFTER that approval; there is no auto-fire-unreviewed path — and there is no fire path
here at all, so that property holds by construction until Herald lands.
"""
from __future__ import annotations

import pathlib

from dashboard import data, settings_store

# Defaults for the review shell. Visibility defaults to the SAFE option; a public/scheduled
# publish is impossible until Herald + the §9 verification gauntlet (project + channel) pass.
DEFAULT_VISIBILITY = "private"
MAX_TAGS = 15


def _project_niche(proj: dict) -> str:
    return (proj.get("config", {}) or {}).get("niche") or proj.get("niche") or ""


def _route_channel(settings: dict, niche: str) -> dict | None:
    """Route niche → the mapped channel (spec §9). The niche row carries the channel_id;
    we resolve the full channel so the modal can show its verification flags + title."""
    if not niche:
        return None
    channel_id = ""
    for n in settings.get("niches", []) or []:
        if n.get("name") == niche:
            channel_id = n.get("channel_id") or ""
            break
    if not channel_id:
        return None
    for c in settings.get("channels", []) or []:
        if c.get("channel_id") == channel_id:
            return c
    return None


def _tags(script: dict, niche: str) -> list[str]:
    """A conservative tag set derived from the script + niche (no fetch, deterministic)."""
    tags: list[str] = []
    if niche:
        tags.append(niche)
    for kw in (script.get("keywords") or script.get("tags") or []):
        if isinstance(kw, str) and kw.strip():
            tags.append(kw.strip())
    # de-dupe preserving order, bounded
    seen, out = set(), []
    for t in tags:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out[:MAX_TAGS]


def publish_package(projects_dir: pathlib.Path, slug: str,
                    settings_path: pathlib.Path | str) -> dict | None:
    """The exact package for the T3 review modal, or None if the project doesn't exist.

    `ready` = a finished render exists. `would_publish` = the package + channel + the §9
    verification flags would ALLOW a public/scheduled publish — but `fire_enabled` is
    ALWAYS False (real publishing is Herald, #6), and `blockers` explains why. Read-only."""
    pdir = projects_dir / slug
    proj = data.read_json(pdir / "project.json", None)
    if not isinstance(proj, dict):
        return None
    script = data.read_json(pdir / "script.json", {}) or {}
    settings = settings_store.load_settings(settings_path)
    niche = _project_niche(proj)
    channel = _route_channel(settings, niche)
    ready = (pdir / "video.mp4").exists() and proj.get("status") == "done"

    label = proj.get("title") or proj.get("topic") or proj.get("slug") or slug
    title = script.get("working_title") or label
    description = (script.get("description") or script.get("hook") or "").strip()
    tags = _tags(script, niche)

    # Blockers: every reason this package could not (yet) go live. The first, permanent one
    # is that Herald isn't built — so nothing fires regardless of the rest.
    blockers: list[str] = ["Publishing arrives with Herald (#6) — the fire button is a shell."]
    if not ready:
        blockers.append("No finished render yet — only a `done` project with a video can publish.")
    if channel is None:
        blockers.append("No channel mapped to this niche (set it in Settings → Niches).")
    else:
        if not channel.get("project_verified"):
            blockers.append("Cloud project not sensitive-scope verified (else uploads force private).")
        if not channel.get("channel_phone_verified"):
            blockers.append("Channel not phone-verified (else no scheduling / custom thumbnail).")

    would_publish = bool(ready and channel
                         and channel.get("project_verified")
                         and channel.get("channel_phone_verified"))

    quota = settings_store.public_settings(settings_path).get("quota", {})

    return {
        "slug": proj.get("slug") or slug,
        "label": label,
        "ready": ready,
        "package": {
            "title": title,
            "description": description,
            "tags": tags,
            # thumbnail is Glint's job (#8) — surfaced as a placeholder in the shell
            "thumbnail": {"available": False,
                          "note": "A high-CTR thumbnail set arrives with Glint (#8)."},
            "visibility": DEFAULT_VISIBILITY,
            "schedule": None,   # set ONLY after approval (Herald) — never before (§4 T3/E8)
        },
        "channel": ({"title": channel.get("title"),
                     "channel_id": channel.get("channel_id"),
                     "connection_status": channel.get("connection_status"),
                     "project_verified": bool(channel.get("project_verified")),
                     "channel_phone_verified": bool(channel.get("channel_phone_verified"))}
                    if channel else None),
        "quota": quota,
        "would_publish": would_publish,
        "fire_enabled": False,   # ALWAYS False — there is no publish path until Herald (#6)
        "blockers": blockers,
    }
