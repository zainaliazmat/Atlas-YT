"""The audio allowlist — the one auditable place that says WHERE Cadence may source.

Mirrors the Asset Sourcer's sources.py exactly, for AUDIO (music beds + SFX):

1. SOURCES — the allowlist as explicit data (a list of `Source` descriptors), each
   declaring its endpoint, whether it is keyless or needs a free key (read from env,
   silent-skip when absent), how to BUILD a search request, and how to PARSE that
   source's response into `AudioCandidate`s carrying a raw license string + provenance.
   The method stays allowlist-FIRST: the engine only ever iterates this list, so an
   off-allowlist source is structurally impossible to record.

2. SourceClient — the THIN network seam, the ONLY place that touches the network
   (search + download). Every build/parse is a PURE function of a dict, so per-source
   license/attribution extraction is unit-testable with canned responses and NO
   network. The accept/reject TRUTH TABLE lives in the engine (audio_engine.py), the
   single source of policy truth — a source only reports the raw license it found.

Conservative parsers: when a source's license metadata is missing or ambiguous, the
parser emits a raw string the engine's truth table REJECTS. Provenance uncertainty is
disqualifying — never guess a track up to "public domain."

NOTE: the bundled CC0 SFX kit (sfx_kit.py) is NOT a network source — it's local,
keyless, and always available, so Cadence's signature accent never depends on a keyed
source being reachable. These archives are enrichment for variety, not the dependency.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable

USER_AGENT = ("YT-AGENTS-Cadence/1.0 (audio sourcing; contact: set in .env) "
              "python-requests")
DEFAULT_TIMEOUT = 20


# ----------------------------------------------------------------------
# The two record types
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class AudioCandidate:
    """One audio sourcing candidate from an allowlist source (pre-clearance).

    `source` is the canonical allowlist name (the engine confirms membership).
    `license_raw` is the verbatim license string/URL the source reported — the engine
    normalizes + validates it. `kind` is "music" | "sfx" | "mixed" (a search hint, not
    a clearance fact). `duration` (sec) lets the engine prefer a bed long enough to run.
    """
    source: str
    title: str
    author: str
    source_url: str          # human provenance/landing page  (S in TASL)
    license_raw: str         # verbatim license string / URL  (engine decides)
    download_url: str        # direct media URL to fetch bytes
    ext: str = "mp3"
    kind: str = "music"
    duration: float = 0.0
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Source:
    """One allowlisted audio archive."""
    name: str                # canonical handle (e.g. "openverse_audio")
    label: str               # human name for attribution's Source field
    base_url: str
    keyless: bool
    env_key: str | None      # env var holding the free key (None when keyless)
    media: str               # "music" | "sfx" | "mixed"
    build: Callable          # (query_text, filters, key) -> (url, params, headers)
    parse: Callable          # (resp_json) -> list[AudioCandidate]


def _txt(v) -> str:
    return "" if v is None else str(v).strip()


def _strip_html(v) -> str:
    return re.sub(r"<[^>]+>", "", _txt(v)).strip()


def _ext_from(url: str, filetype=None) -> str:
    if filetype:
        return str(filetype).lower().lstrip(".")
    m = re.search(r"\.(mp3|wav|ogg|oga|flac|m4a|aac)(?:\?|$)", url or "", re.IGNORECASE)
    return m.group(1).lower() if m else "mp3"


# ======================================================================
# Per-source BUILD + PARSE (pure; unit-tested with canned dicts)
# ======================================================================
# --- Openverse audio (keyless) — the cleanest CC license metadata -----------------
def _openverse_build(q, filters, key):
    params = {"q": q, "page_size": 8, "license": "cc0,pdm,by,by-sa"}
    return SRC_OPENVERSE_AUDIO, params, {}


def _openverse_parse(data):
    out = []
    for r in (data or {}).get("results", []) or []:
        lic = _txt(r.get("license"))
        ver = _txt(r.get("license_version"))
        dur_ms = r.get("duration") or 0
        out.append(AudioCandidate(
            source="openverse_audio",
            title=_txt(r.get("title")),
            author=_txt(r.get("creator")),
            source_url=_txt(r.get("foreign_landing_url") or r.get("url")),
            license_raw=(f"cc-{lic} {ver}".strip() if lic else ""),
            download_url=_txt(r.get("url")),
            ext=_ext_from(_txt(r.get("url")), r.get("filetype")),
            kind="music",
            duration=round(float(dur_ms) / 1000.0, 2) if dur_ms else 0.0,
            extra={"license_url": _txt(r.get("license_url"))},
        ))
    return out


# --- Wikimedia Commons audio (keyless) — license in extmetadata -------------------
def _wikimedia_build(q, filters, key):
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:audio {q}", "gsrnamespace": 6, "gsrlimit": 8,
        "prop": "imageinfo", "iiprop": "url|extmetadata|size",
    }
    return SRC_WIKIMEDIA, params, {}


def _wikimedia_parse(data):
    out = []
    pages = ((data or {}).get("query", {}) or {}).get("pages", {}) or {}
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        lic = _txt((meta.get("LicenseShortName") or {}).get("value"))
        out.append(AudioCandidate(
            source="wikimedia_audio",
            title=_txt(page.get("title")).replace("File:", ""),
            author=_strip_html((meta.get("Artist") or {}).get("value")),
            source_url=_txt(info.get("descriptionurl")),
            license_raw=lic,   # e.g. "CC BY-SA 4.0", "Public domain", "CC0"
            download_url=_txt(info.get("url")),
            ext=_ext_from(_txt(info.get("url"))),
            kind="mixed",
            extra={"license_url": _txt((meta.get("LicenseUrl") or {}).get("value"))},
        ))
    return out


# --- Internet Archive audio (keyless) — search returns IDs; client resolves a file -
def _ia_build(q, filters, key):
    params = {"q": f"{q} AND mediatype:audio",
              "fl[]": "identifier,title,creator,licenseurl,rights",
              "rows": 8, "output": "json"}
    return SRC_IA, params, {}


def _ia_parse(data):
    """Parse the advancedsearch docs. download_url is resolved later by the client
    (the item's file list lives behind a second metadata request, like the Met)."""
    out = []
    docs = (((data or {}).get("response") or {}).get("docs")) or []
    for d in docs:
        ident = _txt(d.get("identifier"))
        lic = _txt(d.get("licenseurl") or d.get("rights"))
        out.append(AudioCandidate(
            source="internet_archive_audio",
            title=_txt(d.get("title")),
            author=_txt(d.get("creator")),
            source_url=f"https://archive.org/details/{ident}" if ident else "",
            license_raw=lic,   # missing -> "" -> engine rejects (unknown)
            download_url="",   # resolved by the client from the item metadata
            ext="mp3", kind="mixed",
            extra={"identifier": ident},
        ))
    return out


# --- Freesound (free key) — SFX + short loops; CC0 / CC-BY, previews are downloadable
def _freesound_build(q, filters, key):
    params = {"query": q, "page_size": 8,
              "fields": "id,name,username,license,previews,duration,url"}
    return SRC_FREESOUND, params, {"Authorization": f"Token {key or ''}"}


def _freesound_parse(data):
    out = []
    for r in (data or {}).get("results", []) or []:
        previews = r.get("previews") or {}
        dl = _txt(previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3"))
        out.append(AudioCandidate(
            source="freesound",
            title=_txt(r.get("name")),
            author=_txt(r.get("username")),
            source_url=_txt(r.get("url")),
            license_raw=_txt(r.get("license")),   # a CC license URL
            download_url=dl, ext="mp3", kind="sfx",
            duration=round(float(r.get("duration") or 0.0), 2),
        ))
    return out


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
SRC_OPENVERSE_AUDIO = "https://api.openverse.org/v1/audio/"
SRC_WIKIMEDIA = "https://commons.wikimedia.org/w/api.php"
SRC_IA = "https://archive.org/advancedsearch.php"
SRC_IA_METADATA = "https://archive.org/metadata/"
SRC_FREESOUND = "https://freesound.org/apiv2/search/text/"


# ----------------------------------------------------------------------
# THE ALLOWLIST — ordered by clearance preference (keyless CC/PD archives first,
# keyed SFX last). The engine iterates exactly this list.
# ----------------------------------------------------------------------
SOURCES: list[Source] = [
    Source("openverse_audio", "Openverse", SRC_OPENVERSE_AUDIO, True, None, "music",
           _openverse_build, _openverse_parse),
    Source("wikimedia_audio", "Wikimedia Commons", SRC_WIKIMEDIA, True, None, "mixed",
           _wikimedia_build, _wikimedia_parse),
    Source("internet_archive_audio", "Internet Archive", SRC_IA, True, None, "mixed",
           _ia_build, _ia_parse),
    Source("freesound", "Freesound", SRC_FREESOUND, False, "FREESOUND_API_KEY", "sfx",
           _freesound_build, _freesound_parse),
]

ALLOWLIST_NAMES = frozenset(s.name for s in SOURCES)
SOURCE_BY_NAME = {s.name: s for s in SOURCES}
SOURCE_ORDER = {s.name: i for i, s in enumerate(SOURCES)}


# ======================================================================
# The thin network client — the ONLY code that touches the network.
# ======================================================================
class SourceClient:
    """Search + download over the allowlist. Network lives here; the brain is pure.

    `available(source)` decides whether a source can be used at all (keyless, or its
    free key is present in env). A missing optional key is NOT an error — the engine
    skips that source silently. `search` raises on a dead/timing-out source; the
    engine catches it and moves on (graceful degradation).
    """

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT, session=None,
                 user_agent: str = USER_AGENT):
        self.timeout = timeout
        self.user_agent = user_agent
        self._session = session  # injectable; real one built lazily so import stays light

    def _sess(self):
        if self._session is None:
            import requests  # lazy: pure import of this module needs no network lib
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": self.user_agent})
        return self._session

    def available(self, source: Source) -> bool:
        if source.keyless:
            return True
        return bool(os.environ.get(source.env_key or ""))

    def _key(self, source: Source) -> str:
        return os.environ.get(source.env_key or "", "") if source.env_key else ""

    def search(self, source: Source, query_text: str, filters: dict) -> list[AudioCandidate]:
        """Run one source's search and return parsed candidates. May raise."""
        url, params, headers = source.build(query_text, filters, self._key(source))
        sess = self._sess()
        merged = dict(sess.headers)
        merged.update(headers)
        resp = sess.get(url, params=params, headers=merged, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        cands = source.parse(data)
        if source.name == "internet_archive_audio":
            cands = [self._ia_resolve(c, sess) for c in cands]
            cands = [c for c in cands if c is not None]
        return cands

    def _ia_resolve(self, cand: AudioCandidate, sess) -> AudioCandidate | None:
        """Resolve an Internet Archive item to a concrete audio file URL. One bad item
        never kills the source (returns None to drop it)."""
        ident = (cand.extra or {}).get("identifier")
        if not ident:
            return None
        try:
            r = sess.get(f"{SRC_IA_METADATA}{ident}", timeout=self.timeout)
            r.raise_for_status()
            meta = r.json()
        except Exception:  # noqa: BLE001 — drop this one, keep the run alive
            return None
        files = (meta or {}).get("files") or []
        audio = next((f for f in files
                      if str(f.get("name", "")).lower().endswith((".mp3", ".ogg", ".flac"))), None)
        if not audio:
            return None
        name = audio["name"]
        from dataclasses import replace
        return replace(cand, download_url=f"https://archive.org/download/{ident}/{name}",
                       ext=_ext_from(name),
                       duration=round(float(audio.get("length") or 0.0), 2)
                       if str(audio.get("length", "")).replace(".", "").isdigit() else 0.0)

    def download(self, url: str) -> bytes:
        """Fetch raw bytes for a chosen candidate. May raise (caller -> placeholder)."""
        resp = self._sess().get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.content
