"""The allowlist — the one auditable place that says WHERE Magpie may source.

Two things live here, and only here:

1. SOURCES — the allowlist as an explicit data structure (a list of `Source`
   descriptors), each declaring its endpoint, whether it is keyless or needs a free
   key (read from env, silent-skip when absent), how to BUILD a search request, and —
   most important — how to PARSE that source's response into `Candidate`s carrying a
   raw license string + the provenance fields. The method stays allowlist-FIRST: the
   engine only ever iterates this list, so an off-allowlist source is structurally
   impossible to record.

2. SourceClient — the THIN network seam. It is the ONLY place that touches the
   network (search + download). Every `build`/`parse` is a PURE function of a dict, so
   the per-source license/attribution extraction is unit-testable with canned
   responses and NO network. The engine's decision logic (normalize → validate →
   rank → clear) is pure and takes an injected client, so the unit suite mocks the
   client and never makes a request.

License RAW strings only: a source reports the license string/URL it found; it does
NOT decide acceptability. Normalization + the accept/reject truth table live in the
engine (source_engine.py) — the single source of policy truth.

Conservative parsers: when a source's license/rights metadata is missing or
ambiguous, the parser emits a raw string that the engine's truth table REJECTS
(unknown / no-known-restrictions). Provenance uncertainty is disqualifying — never
guess a candidate up to "public domain."
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable

# Default, polite HTTP manners for real runs (match Sage's HTTP path).
USER_AGENT = ("YT-AGENTS-Magpie/1.0 (asset sourcing; contact: set in .env) "
              "python-requests")
DEFAULT_TIMEOUT = 15


# ----------------------------------------------------------------------
# The two record types
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Candidate:
    """One sourcing candidate from an allowlist source (pre-clearance).

    `source` is the canonical allowlist name (so the engine can confirm membership).
    `license_raw` is the verbatim license string/URL the source reported — the engine
    normalizes + validates it. The rest are the provenance fields a TASL needs.
    """
    source: str
    title: str
    author: str
    source_url: str          # human provenance/landing page  (S in TASL)
    license_raw: str         # verbatim license string / URL  (engine decides)
    download_url: str        # direct media URL to fetch bytes
    ext: str = "jpg"
    width: int = 0
    height: int = 0
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Source:
    """One allowlisted archive."""
    name: str                # canonical handle (e.g. "openverse")
    label: str               # human name for attribution's Source field
    base_url: str
    keyless: bool
    env_key: str | None      # env var holding the free key (None when keyless)
    media: str               # "image" | "video" | "mixed"
    build: Callable          # (query_text, filters, key) -> (url, params, headers)
    parse: Callable          # (resp_json) -> list[Candidate]


def _txt(v) -> str:
    return "" if v is None else str(v).strip()


def _strip_html(v) -> str:
    """Wikimedia returns Artist/description as HTML; flatten to readable text."""
    return re.sub(r"<[^>]+>", "", _txt(v)).strip()


# ======================================================================
# Per-source BUILD + PARSE (pure; unit-tested with canned dicts)
# ======================================================================
# --- Openverse (keyless) — the cleanest license metadata in the allowlist --------
def _openverse_build(q, filters, key):
    params = {"q": q, "page_size": 8}
    # Openverse supports a server-side license filter; bias to our accept-list.
    params["license"] = "cc0,pdm,by,by-sa"
    return f"{SRC_OPENVERSE_BASE}", params, {}


def _openverse_parse(data):
    out = []
    for r in (data or {}).get("results", []) or []:
        lic = _txt(r.get("license"))
        ver = _txt(r.get("license_version"))
        out.append(Candidate(
            source="openverse",
            title=_txt(r.get("title")),
            author=_txt(r.get("creator")),
            source_url=_txt(r.get("foreign_landing_url") or r.get("url")),
            license_raw=(f"cc-{lic} {ver}".strip() if lic else ""),
            download_url=_txt(r.get("url")),
            ext=_ext_from(_txt(r.get("url")), r.get("filetype")),
            width=int(r.get("width") or 0), height=int(r.get("height") or 0),
            extra={"license_url": _txt(r.get("license_url"))},
        ))
    return out


# --- Wikimedia Commons (keyless) — license buried in extmetadata (awkward) --------
def _wikimedia_build(q, filters, key):
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:bitmap {q}", "gsrnamespace": 6, "gsrlimit": 8,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|size", "iiurlwidth": 1600,
    }
    return SRC_WIKIMEDIA_BASE, params, {}


def _wikimedia_parse(data):
    out = []
    pages = ((data or {}).get("query", {}) or {}).get("pages", {}) or {}
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        lic = _txt((meta.get("LicenseShortName") or {}).get("value"))
        out.append(Candidate(
            source="wikimedia",
            title=_txt(page.get("title")).replace("File:", ""),
            author=_strip_html((meta.get("Artist") or {}).get("value")),
            source_url=_txt(info.get("descriptionurl")),
            license_raw=lic,   # e.g. "CC BY-SA 4.0", "Public domain", "CC0"
            download_url=_txt(info.get("thumburl") or info.get("url")),
            ext=_ext_from(_txt(info.get("url"))),
            width=int(info.get("width") or 0), height=int(info.get("height") or 0),
            extra={"license_url": _txt((meta.get("LicenseUrl") or {}).get("value"))},
        ))
    return out


# --- Library of Congress (keyless) — rights are often "no known restrictions" -----
def _loc_build(q, filters, key):
    return SRC_LOC_BASE, {"q": q, "fo": "json", "c": 8}, {}


def _loc_parse(data):
    out = []
    for r in (data or {}).get("results", []) or []:
        rights = _txt(r.get("rights") or r.get("rights_advisory"))
        if isinstance(r.get("rights_advisory"), list):
            rights = "; ".join(_txt(x) for x in r["rights_advisory"])
        images = r.get("image_url") or []
        dl = _txt(images[-1]) if isinstance(images, list) and images else _txt(r.get("url"))
        out.append(Candidate(
            source="loc",
            title=_txt(r.get("title")),
            author=_txt((r.get("contributor") or [""])[0] if isinstance(
                r.get("contributor"), list) else r.get("contributor")),
            source_url=_txt(r.get("id") or r.get("url")),
            license_raw=rights,   # usually a rights statement; engine rejects the vague ones
            download_url=dl, ext=_ext_from(dl),
        ))
    return out


# --- Internet Archive (keyless) — licenseurl/rights, frequently missing -----------
def _ia_build(q, filters, key):
    params = {"q": f"{q} AND mediatype:image", "fl[]": "identifier,title,creator,licenseurl,rights",
              "rows": 8, "output": "json"}
    return SRC_IA_BASE, params, {}


def _ia_parse(data):
    out = []
    docs = (((data or {}).get("response") or {}).get("docs")) or []
    for d in docs:
        ident = _txt(d.get("identifier"))
        lic = _txt(d.get("licenseurl") or d.get("rights"))
        out.append(Candidate(
            source="internet_archive",
            title=_txt(d.get("title")),
            author=_txt(d.get("creator")),
            source_url=f"https://archive.org/details/{ident}" if ident else "",
            license_raw=lic,   # missing -> "" -> engine rejects (unknown)
            download_url=(f"https://archive.org/services/img/{ident}" if ident else ""),
            ext="jpg",
        ))
    return out


# --- Smithsonian Open Access (free key) — CC0 flagged in indexedStructured --------
def _si_build(q, filters, key):
    return SRC_SI_BASE, {"q": q, "rows": 8, "api_key": key}, {}


def _si_parse(data):
    out = []
    rows = (((data or {}).get("response") or {}).get("rows")) or []
    for row in rows:
        content = row.get("content") or {}
        desc = (content.get("descriptiveNonRepeating") or {})
        indexed = (content.get("indexedStructured") or {})
        usage = _txt((desc.get("metadata_usage") or {}).get("access"))
        media = (desc.get("online_media") or {}).get("media") or []
        dl = _txt(media[0].get("content")) if media else ""
        out.append(Candidate(
            source="smithsonian",
            title=_txt(row.get("title")),
            author=_txt("; ".join(indexed.get("name", []) or [])),
            source_url=_txt((desc.get("record_link"))),
            license_raw=usage,   # "CC0" when open access; else "" -> reject
            download_url=dl, ext=_ext_from(dl),
        ))
    return out


# --- NASA images (keyless) — PD-by-default WITH exceptions (logos/third-party) -----
def _nasa_build(q, filters, key):
    return SRC_NASA_BASE, {"q": q, "media_type": "image"}, {}


def _nasa_parse(data):
    out = []
    items = (((data or {}).get("collection") or {}).get("items")) or []
    for it in items[:8]:
        d = (it.get("data") or [{}])[0]
        links = it.get("links") or []
        dl = _txt(links[0].get("href")) if links else ""
        out.append(Candidate(
            source="nasa",
            title=_txt(d.get("title")),
            author=_txt(d.get("center") or "NASA"),
            source_url=_txt(d.get("nasa_id") and f"https://images.nasa.gov/details-{d.get('nasa_id')}"),
            license_raw="NASA",   # engine treats as PD-but-unverifiable -> sourced + carve-out flag
            download_url=dl, ext=_ext_from(dl),
        ))
    return out


# --- The Met (keyless) — isPublicDomain boolean -> CC0 (clean) ---------------------
# The Met search returns object IDs; the client resolves each via /objects/{id}.
def _met_build(q, filters, key):
    return SRC_MET_SEARCH, {"q": q, "hasImages": "true"}, {}


def _met_parse_object(obj):
    """Parse a single Met /objects/{id} response into a Candidate (or None)."""
    if not isinstance(obj, dict):
        return None
    is_pd = bool(obj.get("isPublicDomain"))
    dl = _txt(obj.get("primaryImage"))
    if not dl:
        return None
    return Candidate(
        source="met",
        title=_txt(obj.get("title")),
        author=_txt(obj.get("artistDisplayName")),
        source_url=_txt(obj.get("objectURL")),
        license_raw="CC0" if is_pd else "All rights reserved",
        download_url=dl, ext=_ext_from(dl),
    )


# --- Pexels (free key) — proprietary-but-permissive; never laundered as CC0 -------
def _pexels_build(q, filters, key):
    return SRC_PEXELS_BASE, {"query": q, "per_page": 8}, {"Authorization": key or ""}


def _pexels_parse(data):
    out = []
    for p in (data or {}).get("photos", []) or []:
        src = p.get("src") or {}
        out.append(Candidate(
            source="pexels",
            title=_txt(p.get("alt")) or "Pexels photo",
            author=_txt(p.get("photographer")),
            source_url=_txt(p.get("url")),
            license_raw="Pexels License",
            download_url=_txt(src.get("large2x") or src.get("original")),
            ext="jpg",
            width=int(p.get("width") or 0), height=int(p.get("height") or 0),
        ))
    return out


# --- Pixabay (free key) — proprietary-but-permissive ------------------------------
def _pixabay_build(q, filters, key):
    return SRC_PIXABAY_BASE, {"key": key or "", "q": q, "per_page": 8,
                              "safesearch": "true"}, {}


def _pixabay_parse(data):
    out = []
    for h in (data or {}).get("hits", []) or []:
        out.append(Candidate(
            source="pixabay",
            title=_txt(h.get("tags")) or "Pixabay image",
            author=_txt(h.get("user")),
            source_url=_txt(h.get("pageURL")),
            license_raw="Pixabay License",
            download_url=_txt(h.get("largeImageURL") or h.get("webformatURL")),
            ext="jpg",
            width=int(h.get("imageWidth") or 0), height=int(h.get("imageHeight") or 0),
        ))
    return out


def _ext_from(url: str, filetype=None) -> str:
    if filetype:
        return str(filetype).lower().lstrip(".")
    m = re.search(r"\.([a-zA-Z0-9]{3,4})(?:\?|$)", url or "")
    return m.group(1).lower() if m else "jpg"


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
SRC_OPENVERSE_BASE = "https://api.openverse.org/v1/images/"
SRC_WIKIMEDIA_BASE = "https://commons.wikimedia.org/w/api.php"
SRC_LOC_BASE = "https://www.loc.gov/photos/"
SRC_IA_BASE = "https://archive.org/advancedsearch.php"
SRC_SI_BASE = "https://api.si.edu/openaccess/api/v1.0/search"
SRC_NASA_BASE = "https://images-api.nasa.gov/search"
SRC_MET_SEARCH = "https://collectionapi.metmuseum.org/public/collection/v1/search"
SRC_MET_OBJECT = "https://collectionapi.metmuseum.org/public/collection/v1/objects/"
SRC_PEXELS_BASE = "https://api.pexels.com/v1/search"
SRC_PIXABAY_BASE = "https://pixabay.com/api/"


# ----------------------------------------------------------------------
# THE ALLOWLIST — ordered by clearance preference (keyless PD/CC archives first,
# proprietary-but-permissive stock last). The engine iterates exactly this list.
# ----------------------------------------------------------------------
SOURCES: list[Source] = [
    Source("openverse", "Openverse", SRC_OPENVERSE_BASE, True, None, "image",
           _openverse_build, _openverse_parse),
    Source("wikimedia", "Wikimedia Commons", SRC_WIKIMEDIA_BASE, True, None, "image",
           _wikimedia_build, _wikimedia_parse),
    Source("met", "The Metropolitan Museum of Art", SRC_MET_SEARCH, True, None, "image",
           _met_build, _met_parse_object),  # parse handled specially in the client
    Source("loc", "Library of Congress", SRC_LOC_BASE, True, None, "image",
           _loc_build, _loc_parse),
    Source("internet_archive", "Internet Archive", SRC_IA_BASE, True, None, "image",
           _ia_build, _ia_parse),
    Source("smithsonian", "Smithsonian Open Access", SRC_SI_BASE, False,
           "SMITHSONIAN_API_KEY", "image", _si_build, _si_parse),
    Source("nasa", "NASA", SRC_NASA_BASE, True, None, "image",
           _nasa_build, _nasa_parse),
    Source("pexels", "Pexels", SRC_PEXELS_BASE, False, "PEXELS_API_KEY", "image",
           _pexels_build, _pexels_parse),
    Source("pixabay", "Pixabay", SRC_PIXABAY_BASE, False, "PIXABAY_API_KEY", "image",
           _pixabay_build, _pixabay_parse),
]

# The canonical names the engine confirms membership against (defense in depth).
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

    def search(self, source: Source, query_text: str, filters: dict) -> list[Candidate]:
        """Run one source's search and return parsed candidates. May raise."""
        url, params, headers = source.build(query_text, filters, self._key(source))
        sess = self._sess()
        merged = dict(sess.headers)
        merged.update(headers)
        resp = sess.get(url, params=params, headers=merged, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if source.name == "met":
            return self._met_resolve(data, sess)
        return source.parse(data)

    def _met_resolve(self, search_data: dict, sess) -> list[Candidate]:
        """The Met returns IDs; resolve the first few to objects with license flags."""
        ids = (search_data or {}).get("objectIDs") or []
        out = []
        for oid in ids[:8]:
            try:
                r = sess.get(f"{SRC_MET_OBJECT}{oid}", timeout=self.timeout)
                r.raise_for_status()
                cand = _met_parse_object(r.json())
            except Exception:  # noqa: BLE001 — one bad object never kills the source
                cand = None
            if cand is not None:
                out.append(cand)
        return out

    def download(self, url: str) -> bytes:
        """Fetch raw bytes for a chosen candidate. May raise (caller -> placeholder)."""
        resp = self._sess().get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.content
