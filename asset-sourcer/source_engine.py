"""Magpie's engine: storyboard.json (+ style_guide.json) -> a cleared asset_manifest.

Magpie sources the real images/clips/icons each storyboard shot needs, proves each is
reusable from an allowlist of public-domain / Creative-Commons archives, downloads it
LOCAL, and records source + license + attribution + status. When a shot can't be
cleared, it ships a flagged LOCAL placeholder rather than an unlicensed asset.

THE SPLIT (mirrors the siblings): the NETWORK lives behind a thin seam (sources.py's
SourceClient — search + download). The BRAIN here is PURE and deterministic — no LLM,
no taste call; sourcing is retrieval + a license truth table, not vibe. Every decision
is unit-testable with a mocked client and canned responses, network OFF:

  1. normalize_license  — raw license string/URL -> a canonical code
  2. classify           — the accept / reject / force-sourced truth table (the crux)
  3. classify_shot      — shot.kind (+ content) -> asset type + source/generate/skip
  4. derive_query       — shot.content (biased by style era/palette) -> a search query
  5. rank_candidates    — deterministic, fully-ordered; reproducible manifests
  6. build_attribution  — TASL (Title/Author/Source/License) + completeness
  7. source_assets      — the loop: search allowlist -> clear -> download-local OR
                          placeholder-and-flag; assembles the contract-shaped dict

Decoupling boundary: this engine emits plain dicts and NEVER imports atlas. Atlas
stamps `schema_version` (asset_manifest stays "1.0") and validates against the frozen
contract at the boundary. `source_assets(...)` is the pure seam the adapter uses;
`run_source(...)` is the CLI/chat convenience that loads, saves, and logs.

THE INVARIANTS (enforced in code, relied on downstream):
- asset_id == the storyboard shot's `asset_ref` (the spine of the job).
- Nothing reaches `cleared` without an accept-list license AND complete attribution
  AND a LOCAL `uri`. "Probably fine" never passes; "no known copyright restrictions"
  is a reject, not a maybe.
- Every recorded asset comes from an allowlisted source (membership re-checked here).
- Every `placeholder`/`sourced` `uri` resolves to a real LOCAL file (no remote URLs,
  no dangling paths) — HyperFrames-safe, no render-time fetches.
"""
from __future__ import annotations

import base64
import hashlib
import pathlib
import re
import time
from dataclasses import dataclass, field

import chat_state  # atomic_write_json / load_json — corruption-safe file helpers
import sources
from sources import ALLOWLIST_NAMES, SOURCE_BY_NAME, SOURCE_ORDER, SOURCES, Candidate

HERE = pathlib.Path(__file__).parent
SOUL = (HERE / "soul" / "SOUL.md").read_text()
SKILL = (HERE / "SKILL.md").read_text()
MEMORY = HERE / "memory.json"
MANIFESTS_DIR = HERE / "manifests"

# asset_manifest is NOT one of the additively-extended contracts, so it stays on the
# base CONTRACT_VERSION. Atlas is the authority (stamps via contracts.version_for at the
# boundary); this local copy keeps a standalone `run.py` save independently contract-shaped.
SCHEMA_VERSION = "1.0"

# Where downloads land inside the per-project dir; the manifest `uri` is relative to it.
ASSETS_SUBDIR = "assets"
PLACEHOLDER_REL = f"{ASSETS_SUBDIR}/_placeholder.png"
PLACEHOLDER_LICENSE = "unlicensed (placeholder)"

# One bundled, deterministic placeholder asset (1x1 transparent PNG). Every
# placeholder/un-downloadable shot points its `uri` at a copy of this LOCAL file, so
# the Composition Engineer never hits a dangling path. No image dependency needed.
_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


# ======================================================================
# 1. License normalization — raw string/URL -> canonical code
# ======================================================================
# Canonical codes the truth table keys on.
_ACCEPT_PD = {"cc0", "pdm", "pd"}            # no attribution legally required
_ACCEPT_BY = {"by", "by-sa"}                 # attribution required
_FORCE_SOURCED = {"pexels", "pixabay", "nasa"}  # accepted but never auto-cleared
# Everything else (nc/nd variants, noc-us, no-known, arr, unknown) -> reject.


def _has(token: str, s: str) -> bool:
    """A CC flag token (by/nc/nd/sa) present as a delimited unit, not inside a word."""
    return re.search(rf"(^|[ \-/]){token}([ \-/]|$)", s) is not None


def normalize_license(raw: str) -> str:
    """Map a verbatim license string/URL to a canonical code (lowercased).

    Worldwide PD declarations accept (CC0, Public Domain Mark, plain "public domain").
    Jurisdiction-limited / uncertainty statements REJECT — crucially:
      - "Public Domain Mark" (PDM)            -> ACCEPT (worldwide)
      - "No Copyright - United States" (NoC-US) -> REJECT (US-only; uncertain elsewhere)
      - "No known copyright restrictions" / Flickr-Commons -> REJECT (not a license)
    """
    s = (raw or "").strip().lower()
    if not s:
        return "unknown"

    # Proprietary-but-permissive stock + NASA (handled as force-sourced, not PD).
    if "pexels" in s:
        return "pexels"
    if "pixabay" in s:
        return "pixabay"
    if s == "nasa" or "images.nasa" in s:
        return "nasa"

    # Rights statements that are NOT clear licenses -> reject (order matters: the
    # NoC-US / no-known checks come before any generic "public domain" substring).
    # Both the human label ("No Copyright - United States") and the rightsstatements.org
    # URL form (".../NoC-US/1.0/") must be caught.
    if "noc-us" in s or ("no copyright" in s and "united states" in s):
        return "noc-us"
    if ("no known copyright" in s or "no known restrictions" in s
            or "no known restriction" in s or "flickr commons" in s
            or "/nkc/" in s or "/nkc" in s):
        return "no-known"

    # Public domain (worldwide).
    if "publicdomain/zero" in s or s in ("cc0", "cc-0") or "cc0" in s:
        return "cc0"
    if "publicdomain/mark" in s or "public domain mark" in s or s in ("pdm",):
        return "pdm"

    # Creative Commons license families — parse the flags.
    if "creativecommons.org/licenses" in s or s.startswith("cc-") or s.startswith("cc ") \
            or _has("by", s):
        has_by = _has("by", s) or "licenses/by" in s
        has_nc = _has("nc", s) or "-nc" in s
        has_nd = _has("nd", s) or "-nd" in s
        has_sa = _has("sa", s) or "-sa" in s
        if has_nc or has_nd:
            # Build the (rejected) NC/ND code for an honest flag.
            parts = ["by"] + (["nc"] if has_nc else []) + (["nd"] if has_nd else []) \
                + (["sa"] if has_sa and not has_nd else [])
            return "-".join(parts)
        if has_sa:
            return "by-sa"
        if has_by:
            return "by"

    # Generic public-domain declaration (no mark) — accept as PD.
    if "public domain" in s or s == "pd":
        return "pd"

    # Explicit all-rights-reserved / copyright.
    if "all rights reserved" in s or "©" in s or "copyright ©" in s:
        return "arr"
    return "unknown"


# ======================================================================
# 2. The license truth table (the crux of the engine)
# ======================================================================
@dataclass(frozen=True)
class Disposition:
    verdict: str             # "accept" | "reject"
    requires_attribution: bool
    force_sourced: bool      # accepted, but can never auto-clear (carve-outs)
    share_alike: bool
    label: str               # human license label for the manifest `license` field
    note: str = ""           # carve-out / reason, surfaced as a flag


_DISPOSITIONS: dict[str, Disposition] = {
    "cc0": Disposition("accept", False, False, False, "CC0 1.0"),
    "pdm": Disposition("accept", False, False, False, "Public Domain Mark 1.0"),
    "pd":  Disposition("accept", False, False, False, "Public Domain"),
    "by":  Disposition("accept", True, False, False, "CC BY"),
    "by-sa": Disposition("accept", True, False, True, "CC BY-SA",
                         "share-alike: derivative video must carry a compatible license"),
    "pexels": Disposition("accept", False, True, False, "Pexels License",
                          "Pexels License: commercial OK, but identifiable people / "
                          "trademarks / property are NOT cleared — verify before air"),
    "pixabay": Disposition("accept", False, True, False, "Pixabay License",
                           "Pixabay License: commercial OK, but identifiable people / "
                           "trademarks / property are NOT cleared — verify before air"),
    "nasa": Disposition("accept", False, True, False, "NASA (public domain*)",
                        "NASA media is public domain EXCEPT logos, insignia, and "
                        "third-party material — verify this item before air"),
}

# Rejections, with a reason string for the flag (so the placeholder is honest).
_REJECT_REASONS = {
    "noc-us": "rights statement is US-only (No Copyright – United States) — not "
              "cleared worldwide",
    "no-known": "\"no known copyright restrictions\" is not a license — provenance "
                "uncertain, disqualified",
    "arr": "all rights reserved",
    "unknown": "no traceable rights statement",
}


def classify(code: str) -> Disposition:
    """The accept/reject decision for a canonical license code. The single policy seam."""
    disp = _DISPOSITIONS.get(code)
    if disp is not None:
        return disp
    # NC / ND variants and every unrecognized/uncertain code -> reject.
    reason = _REJECT_REASONS.get(code)
    if reason is None:
        if "nc" in code or "nd" in code:
            reason = ("non-commercial / no-derivatives license — unusable for a "
                      "monetized, composited video")
        else:
            reason = "no traceable rights statement"
    return Disposition("reject", False, False, False, code.upper() or "unknown", reason)


def is_acceptable(license_raw: str) -> bool:
    """Convenience: does this raw license clear the accept-list at all?"""
    return classify(normalize_license(license_raw)).verdict == "accept"


# ======================================================================
# 3. Shot classification — what kind of asset, and source / generate / skip
# ======================================================================
# Pure typography rendered by the Composition Engineer from script text -> not an asset.
_TYPOGRAPHY_KINDS = {"title", "text", "quote", "headline", "caption", "label",
                     "lower-third", "subtitle", "kicker"}
_VIDEO_KINDS = {"footage", "video", "clip", "broll-video", "motion", "b-roll-video"}
_ICON_KINDS = {"icon", "logo", "symbol", "glyph", "pictogram", "mark"}
_DATAVIZ_KINDS = {"chart", "graph", "plot", "data", "dataviz", "data-viz", "diagram",
                  "figure", "infographic"}
_IMAGE_KINDS = {"image", "photo", "still", "b-roll", "broll", "portrait", "photograph",
                "picture", "archival", "illustration", "painting", "engraving"}
# Kinds whose disposition depends on the CONTENT (named-archival -> image; data -> viz).
_SPLIT_KINDS = {"map"} | _DATAVIZ_KINDS

# Cues that a "map"/"diagram" names a real period artifact worth the scavenger hunt.
_ARCHIVAL_CUE = re.compile(
    r"(\b1[5-9]\d{2}\b|\b20[0-1]\d\b|\b\d{4}s\b|\bcirca\b|\bc\.\s?\d{2,4}\b|"
    r"rand\s*mcnally|sanborn|lithograph|engraving|woodcut|vintage|historical|"
    r"archival|antique|old\s+map|ancient|medieval|manuscript|broadside|"
    r"map of\b|portrait of\b|photograph of\b)",
    re.IGNORECASE)
# Cues that a "map"/"diagram" is a data visualization to be GENERATED, not sourced.
_DATA_CUE = re.compile(
    r"(by county|by state|by region|by district|per capita|choropleth|heatmap|"
    r"\brate\b|\bcases\b|\bgrowth\b|\btrend\b|over time|timeline|bar chart|"
    r"line chart|pie chart|scatter|animated route|route overlay|flow of)",
    re.IGNORECASE)


@dataclass(frozen=True)
class ShotPlan:
    asset_type: str          # "image" | "video" | "icon" | "data-viz"
    action: str              # "source" | "generate" | "skip"
    reason: str = ""


def classify_shot(shot: dict) -> ShotPlan:
    """Decide the manifest `type` and whether Magpie sources, generates, or skips.

    - typography kinds (title/quote/text) -> SKIP (the Composition Engineer sets type).
    - charts/data-viz, and data-driven maps/diagrams -> GENERATE (placeholder + flag).
    - a NAMED period/archival map or diagram -> SOURCE as an image (the scavenger hunt).
    - image/video/icon kinds -> SOURCE.
    """
    kind = str(shot.get("kind", "")).strip().lower()
    content = str(shot.get("content", ""))

    if kind in _TYPOGRAPHY_KINDS:
        return ShotPlan("image", "skip", "typography — rendered from script text")

    if kind in _SPLIT_KINDS:
        # The map/diagram split routes on CONTENT, scavenger bias wins ties.
        if _ARCHIVAL_CUE.search(content):
            return ShotPlan("image", "source", "named period/archival artifact")
        if _DATA_CUE.search(content) or kind in _DATAVIZ_KINDS:
            return ShotPlan("data-viz", "generate", "composition-generated (data-viz)")
        # A bare "map" with no cue either way: source as an image (period imagery is
        # Magpie's lane; a truly data-driven map names its data).
        return ShotPlan("image", "source", "map with no data cue — source as image")

    if kind in _VIDEO_KINDS:
        return ShotPlan("video", "source")
    if kind in _ICON_KINDS:
        return ShotPlan("icon", "source")
    if kind in _IMAGE_KINDS:
        return ShotPlan("image", "source")
    # Unknown kind: default to sourcing an image (be useful), like Iris's shot default.
    return ShotPlan("image", "source", f"unrecognized kind {kind!r} — defaulting to image")


# ======================================================================
# 4. Query derivation — shot.content biased by style era/palette cues
# ======================================================================
_YEAR = re.compile(r"\b(1[5-9]\d{2}|20[0-2]\d)\b")
_DECADE = re.compile(r"\b((?:1[5-9]|20)\d0)s\b")
_STOP = {"a", "an", "the", "of", "with", "and", "in", "on", "to", "for", "single",
         "shot", "image", "photo", "showing", "shows", "view"}


@dataclass(frozen=True)
class Query:
    text: str
    era: str = ""
    monochrome: bool = False
    filters: dict = field(default_factory=dict)


def _is_grayscale(hexval: str) -> bool:
    s = (hexval or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) < 6 or not re.fullmatch(r"[0-9A-Fa-f]{6,8}", s):
        return False
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return max(r, g, b) - min(r, g, b) <= 16   # near-neutral channels


def is_monochrome(style_guide: dict) -> bool:
    """A near-monochrome look: no chromatic accents and grayscale base colors."""
    sg = style_guide or {}
    palette = sg.get("palette") or {}
    accents = [a for a in (palette.get("accents") or [])]
    # The signature_highlight is reserved (#FFD000) and does NOT count as a palette accent.
    chromatic_accents = [a for a in accents if not _is_grayscale(a)]
    if chromatic_accents:
        return False
    base = [palette.get("primary"), palette.get("bg"), palette.get("text")]
    base = [b for b in base if b]
    return bool(base) and all(_is_grayscale(b) for b in base)


def derive_query(content: str, style_guide: dict | None = None) -> Query:
    """Build a search query from a shot's content description, biased by the style.

    - Extracts an era (a year or a decade) to bias toward period-accurate results.
    - Flags monochrome so sources/ranking can prefer black-and-white candidates.
    - Trims filler so the query is the nouns that matter, deterministically.
    """
    raw = (content or "").strip()
    era = ""
    m = _YEAR.search(raw) or _DECADE.search(raw)
    if m:
        era = m.group(0)

    # Keep meaningful tokens in order; drop punctuation + filler. Deterministic.
    words = re.findall(r"[A-Za-z0-9']+", raw.lower())
    kept = [w for w in words if w not in _STOP]
    text = " ".join(kept) if kept else raw.lower()

    mono = is_monochrome(style_guide or {})
    if mono and "black" not in text and "white" not in text:
        text = (text + " black and white").strip()
    if era and era not in text:
        text = (text + " " + era).strip()

    return Query(text=text.strip(), era=era, monochrome=mono,
                 filters={"license": "cc0,pdm,by,by-sa"})


# ======================================================================
# 5. Ranking — deterministic, fully-ordered (reproducible manifests)
# ======================================================================
# License preference: worldwide-PD first, then BY, BY-SA, then proprietary stock.
_LICENSE_RANK = {"cc0": 0, "pdm": 0, "pd": 1, "by": 2, "by-sa": 3,
                 "nasa": 6, "pexels": 7, "pixabay": 7}


def _license_rank(code: str) -> int:
    return _LICENSE_RANK.get(code, 50)


def relevance(query: Query, cand: Candidate) -> int:
    """A simple, deterministic token-overlap score between the query and the candidate."""
    q_tokens = set(re.findall(r"[a-z0-9']+", query.text.lower()))
    hay = f"{cand.title} {cand.extra.get('description', '')}".lower()
    h_tokens = set(re.findall(r"[a-z0-9']+", hay))
    return len(q_tokens & h_tokens)


def rank_candidates(query: Query, candidates: list[Candidate]) -> list[Candidate]:
    """Return the ACCEPTABLE candidates, best first, by a fully-ordered key.

    Off-allowlist candidates are dropped (defense in depth). Reject-licensed
    candidates are dropped here so the clearing walk only sees usable assets. The
    sort key is total and unseeded, so re-running on the same inputs is reproducible.
    """
    usable = []
    for c in candidates:
        if c.source not in ALLOWLIST_NAMES:
            continue
        code = normalize_license(c.license_raw)
        if classify(code).verdict != "accept":
            continue
        usable.append((code, c))

    def key(item):
        code, c = item
        return (
            _license_rank(code),               # better license first
            -relevance(query, c),              # more relevant first
            SOURCE_ORDER.get(c.source, 99),    # allowlist preference order
            -(c.width * c.height),             # higher resolution first
            c.source, c.source_url, c.title,   # stable final tiebreakers
        )

    return [c for _, c in sorted(usable, key=key)]


# ======================================================================
# 6. Attribution — TASL (Title / Author / Source / License) + completeness
# ======================================================================
def build_attribution(cand: Candidate, disp: Disposition) -> tuple[str, bool, dict]:
    """Build a renderable TASL string + whether it is COMPLETE for this license.

    CC0/PD need no attribution to be legal, but we still capture provenance (Magpie's
    mandate; the channel may credit anyway). CC-BY / CC-BY-SA legally require it — a
    missing required field (no findable author) means the asset cannot CLEAR.
    """
    title = cand.title.strip() or "Untitled"
    author = cand.author.strip()
    src_label = SOURCE_BY_NAME[cand.source].label if cand.source in SOURCE_BY_NAME else cand.source
    src_url = cand.source_url.strip()
    license_url = (cand.extra or {}).get("license_url", "").strip()

    parts = [f'"{title}"']
    if author:
        parts.append(f"by {author}")
    parts.append(f"via {src_label}" + (f" ({src_url})" if src_url else ""))
    parts.append(disp.label + (f" — {license_url}" if license_url else ""))
    tasl = " ".join(parts)

    fields = {"title": title, "author": author, "source": src_label,
              "source_url": src_url, "license": disp.label, "license_url": license_url}

    if disp.requires_attribution:
        complete = bool(author and src_url)   # an attributable BY/BY-SA needs both
    else:
        complete = True                       # PD/CC0: legally nothing required
    return tasl, complete, fields


# ======================================================================
# 7. The loop — source each shot; clear-and-download OR placeholder-and-flag
# ======================================================================
def _iter_shots(storyboard: dict):
    """Yield (scene_no, shot) for every shot in the storyboard, in order."""
    for scene in (storyboard or {}).get("scenes", []) or []:
        n = scene.get("scene_no")
        for shot in (scene.get("shots") or []):
            if isinstance(shot, dict):
                yield n, shot


def validate_storyboard(storyboard) -> tuple[bool, str]:
    """A storyboard is usable only if it carries scenes with shots to source for."""
    if not isinstance(storyboard, dict):
        return False, "That's not a storyboard — I need the storyboard JSON object."
    scenes = storyboard.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return False, ("This storyboard has no scenes — there's nothing to source. "
                       "Send it back to the Art Director.")
    return True, ""


def _write_placeholder(pdir: pathlib.Path) -> None:
    """Write the one shared, local placeholder file (idempotent)."""
    path = pdir / PLACEHOLDER_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(_PLACEHOLDER_PNG)
        tmp.replace(path)


def _placeholder_asset(asset_id: str, scene_no, asset_type: str, *, source: str,
                       license_label: str, flag: str, query: str = "") -> dict:
    """A flagged, contract-shaped placeholder pointing at the LOCAL placeholder file."""
    asset = {
        "asset_id": asset_id,
        "scene_no": int(scene_no) if isinstance(scene_no, int) else (scene_no or 0),
        "type": asset_type,
        "source": source,
        "uri": PLACEHOLDER_REL,
        "license": license_label,
        "attribution": "",
        "status": "placeholder",
        "flag": flag,
    }
    if query:
        asset["suggested_query"] = query
    return asset


def source_assets(storyboard: dict, style_guide: dict | None = None, *,
                  client, pdir: str | pathlib.Path, dedupe: bool = True) -> dict:
    """Source every storyboard shot -> a contract-shaped asset_manifest dict (env-free).

    Pure decision logic over an INJECTED `client` (the only network seam). For each
    shot:
      classify -> skip (typography) | generate (data-viz placeholder) | source
      source: derive query -> search the available allowlist -> rank acceptable
              candidates -> walk them, download the first that downloads -> clear
              (license + attribution) -> record local; else placeholder-and-flag.

    Returns {"assets": [...]} (Atlas stamps schema_version + validates). Writes
    downloads + the shared placeholder under `pdir/assets/`.
    """
    ok, reason = validate_storyboard(storyboard)
    if not ok:
        raise ValueError(reason)

    pdir = pathlib.Path(pdir)
    (pdir / ASSETS_SUBDIR).mkdir(parents=True, exist_ok=True)
    _write_placeholder(pdir)

    available = [s for s in SOURCES if _safe_available(client, s)]
    hash_to_uri: dict[str, str] = {}        # within-run content-hash dedupe
    assets: list[dict] = []

    for scene_no, shot in _iter_shots(storyboard):
        asset_id = _asset_id_for(shot, scene_no, len(assets))
        plan = classify_shot(shot)

        if plan.action == "skip":
            continue
        if plan.action == "generate":
            assets.append(_placeholder_asset(
                asset_id, scene_no, plan.asset_type, source="composition-engineer",
                license_label="n/a (generated)", flag=plan.reason))
            continue

        # --- action == "source" -------------------------------------------------
        query = derive_query(shot.get("content", ""), style_guide)
        candidates = _gather(client, available, query)
        ranked = rank_candidates(query, candidates)

        recorded = _try_clear(ranked, asset_id, scene_no, plan, client,
                              pdir, hash_to_uri, dedupe)
        if recorded is None:
            recorded = _placeholder_asset(
                asset_id, scene_no, plan.asset_type, source="(none cleared)",
                license_label=PLACEHOLDER_LICENSE,
                flag=("no provably-reusable candidate found on the allowlist"
                      if ranked == [] else "candidates found but none could be cleared"),
                query=query.text)
        assets.append(recorded)

    return {"assets": assets}


def _safe_available(client, source) -> bool:
    try:
        return bool(client.available(source))
    except Exception:  # noqa: BLE001 — a flaky availability check never blocks the run
        return False


def _gather(client, available, query: Query) -> list[Candidate]:
    """Search every available source; a dead/timing-out source is skipped, not fatal."""
    out: list[Candidate] = []
    for source in available:
        try:
            out.extend(client.search(source, query.text, query.filters) or [])
        except Exception:  # noqa: BLE001 — graceful degradation per the spec
            continue
    return out


def _try_clear(ranked, asset_id, scene_no, plan, client, pdir, hash_to_uri,
               dedupe) -> dict | None:
    """Walk ranked candidates; download + record the first that downloads. Else None.

    A downloaded candidate is always accept-licensed (rank_candidates dropped the
    rest). Status is decided here: PD/CC0 with a local file -> cleared; BY/BY-SA with
    complete attribution -> cleared; force-sourced (Pexels/Pixabay/NASA) or
    BY/BY-SA with incomplete attribution -> sourced + flag. Download failure -> next.
    """
    for cand in ranked:
        try:
            data = client.download(cand.download_url)
        except Exception:  # noqa: BLE001 — a dead media URL just drops to the next pick
            continue
        if not data:
            continue

        uri = _store(data, asset_id, cand.ext, pdir, hash_to_uri, dedupe)
        code = normalize_license(cand.license_raw)
        disp = classify(code)
        tasl, complete, _fields = build_attribution(cand, disp)

        if disp.force_sourced:
            status, flag = "sourced", disp.note
        elif disp.requires_attribution and not complete:
            status, flag = "sourced", ("accepted license but attribution is "
                                       "incomplete (no findable author) — not cleared")
        else:
            status, flag = "cleared", ""

        asset = {
            "asset_id": asset_id,
            "scene_no": int(scene_no) if isinstance(scene_no, int) else (scene_no or 0),
            "type": plan.asset_type,
            "source": cand.source,
            "uri": uri,
            "license": disp.label,
            "license_code": code,
            "license_url": (cand.extra or {}).get("license_url", ""),
            "attribution": tasl,
            "provenance": cand.source_url,
            "status": status,
        }
        if disp.share_alike:
            asset["share_alike"] = True
        if flag:
            asset["flag"] = flag
        return asset
    return None


def _store(data: bytes, asset_id: str, ext: str, pdir: pathlib.Path,
           hash_to_uri: dict, dedupe: bool) -> str:
    """Write bytes to a LOCAL file under assets/ and return the relative uri.

    Within-run content-hash dedupe SHARES the file (not the provenance) — two shots
    reusing the same image point at one file, each still its own asset entry.
    """
    if dedupe:
        digest = hashlib.sha256(data).hexdigest()
        hit = hash_to_uri.get(digest)
        if hit:
            return hit
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", asset_id) or "asset"
    rel = f"{ASSETS_SUBDIR}/{safe}.{(ext or 'jpg').lstrip('.')}"
    path = pdir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    if dedupe:
        hash_to_uri[hashlib.sha256(data).hexdigest()] = rel
    return rel


def _asset_id_for(shot: dict, scene_no, idx: int) -> str:
    """asset_id == the shot's asset_ref (the spine). Fall back deterministically."""
    ref = shot.get("asset_ref")
    if isinstance(ref, str) and ref.strip():
        return ref.strip()
    return f"s{scene_no}-{idx + 1}"


# ======================================================================
# Loading / saving / runs (standalone + chat convenience) — mirrors the siblings
# ======================================================================
def load_storyboard(path: str | pathlib.Path) -> dict:
    """Resolve `path` (a storyboard.json or a project dir holding one) to a dict."""
    p = pathlib.Path(path).expanduser()
    if p.is_dir():
        p = p / "storyboard.json"
    return chat_state.load_json(p, {})


def load_style_guide(path: str | pathlib.Path) -> dict:
    """Resolve `path` (a style_guide.json or a project dir holding one) to a dict."""
    p = pathlib.Path(path).expanduser()
    if p.is_dir():
        p = p / "style_guide.json"
    return chat_state.load_json(p, {})


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "manifest").lower()).strip("-")
    return (s or "manifest")[:50]


def load_memory():
    return chat_state.load_json(MEMORY, {"runs": []})


def save_memory(mem):
    chat_state.atomic_write_json(MEMORY, mem)


def manifest_stats(manifest: dict) -> dict:
    """Count assets by status for a one-line digest."""
    assets = manifest.get("assets", [])
    by = {"cleared": 0, "sourced": 0, "placeholder": 0}
    for a in assets:
        by[a.get("status", "placeholder")] = by.get(a.get("status", "placeholder"), 0) + 1
    return {"total": len(assets), **by}


def run_source(path: str | pathlib.Path, *, client=None, pdir=None, quiet: bool = False
               ) -> tuple[dict, pathlib.Path]:
    """Full standalone run: load storyboard (+ style), source, save, log.

    Downloads land in `pdir` (defaults to the project dir if `path` is a dir, else a
    timestamped folder under manifests/). Returns (stamped_manifest, json_path).
    """
    def log(m):
        if not quiet:
            print(m)

    p = pathlib.Path(path).expanduser()
    storyboard = load_storyboard(p)
    style_guide = load_style_guide(p) or None
    ok, reason = validate_storyboard(storyboard)
    if not ok:
        raise ValueError(reason)

    if pdir is None:
        if p.is_dir():
            pdir = p
        else:
            MANIFESTS_DIR.mkdir(exist_ok=True)
            pdir = MANIFESTS_DIR / f"{_slug(p.stem)}-{time.strftime('%Y%m%d-%H%M%S')}"
            pdir.mkdir(parents=True, exist_ok=True)
    pdir = pathlib.Path(pdir)

    if client is None:
        client = sources.SourceClient()

    log(f"\n🗂️  Sourcing assets for {storyboard.get('total_scenes', len(storyboard.get('scenes', [])))} scenes…")
    manifest = source_assets(storyboard, style_guide, client=client, pdir=pdir)
    stamped = {"schema_version": SCHEMA_VERSION, **manifest}

    json_path = pdir / "asset_manifest.json"
    chat_state.atomic_write_json(json_path, stamped)

    st = manifest_stats(stamped)
    log(f"  · {st['total']} assets — {st['cleared']} cleared, {st['sourced']} sourced, "
        f"{st['placeholder']} placeholder")
    _log_run(storyboard, st)
    return stamped, json_path


def _log_run(storyboard: dict, stats: dict) -> None:
    mem = load_memory()
    mem["runs"].append({"scenes": len(storyboard.get("scenes", [])), **stats,
                        "generated": time.strftime("%Y-%m-%d %H:%M:%S")})
    save_memory(mem)
