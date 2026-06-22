"""Offline proof for Magpie's asset-sourcing engine — NO network, NO API keys.

Run (from the project folder):  python tests/test_source_engine.py
Or:                             pytest tests/test_source_engine.py

The network seam (search + download) is MOCKED throughout via an injected client, so we
assert the engine's PLUMBING and the HARD INVARIANTS only:
  - normalize_license + classify: the license truth table, incl. PDM-accept /
    NoC-US-reject, Flickr-Commons "no known copyright restrictions" reject, NC/ND
    reject, and the proprietary-but-permissive (Pexels/Pixabay/NASA) force-sourced rows
  - off-allowlist candidates are dropped (membership, in code)
  - classify_shot: typography skip / data-viz generate / the named-archival map split /
    image-video-icon source; type mapping
  - derive_query: era extraction + monochrome bias + filler trimming (deterministic)
  - rank_candidates: deterministic, fully-ordered; rejects dropped before the walk
  - build_attribution: TASL string + completeness (PD/CC0 vs BY/BY-SA)
  - source_assets end-to-end with a mocked client:
      * asset_id == the shot's asset_ref; scene_no carried; type ∈ the enum
      * cleared / sourced / placeholder transitions — incl. the `sourced` path
        (accept-license BY with unresolvable attribution -> sourced, NOT placeholder)
      * every placeholder/sourced uri resolves to a real LOCAL file (no remote URLs)
      * within-run content-hash dedupe SHARES the file, not the provenance
      * the emitted dict validates against atlas's frozen asset_manifest schema
  - graceful degradation: a missing optional key skips a source; a dead/timing-out
    source is skipped; a failed download falls through to a placeholder
  - the REPL [y/N] gate (standalone gate; pipeline/adapter is gate-free)

HONEST NOTE: whether the LIVE archives' real responses parse correctly is a
MANUAL/integration check (a real `run.py source`, or the optional network smoke). Only
the plumbing + the invariants + the per-source PARSERS (with canned dicts) are
unit-tested here.
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import source_engine as engine  # noqa: E402
import sources  # noqa: E402
from sources import Candidate  # noqa: E402

# Make atlas's frozen contracts importable so we can assert real schema validity.
# APPEND (not insert-at-0): atlas/ also ships chat.py / llm.py / chat_state.py, and we
# must NOT let those shadow asset-sourcer's own modules.
_ATLAS = pathlib.Path(__file__).resolve().parent.parent.parent / "atlas"
sys.path.append(str(_ATLAS))
import contracts  # noqa: E402


# ======================================================================
# Helpers
# ======================================================================
def cand(source, title, license_raw, *, author="", url=None, ext="jpg",
         w=800, h=600, license_url=""):
    url = url or f"https://example.test/{source}/{title.replace(' ', '-')}.{ext}"
    return Candidate(source=source, title=title, author=author,
                     source_url=f"https://example.test/{source}/page",
                     license_raw=license_raw, download_url=url, ext=ext,
                     width=w, height=h, extra={"license_url": license_url})


class FakeClient:
    """A network-free stand-in: per-source candidates filtered by query relevance."""

    def __init__(self, by_source=None, *, dead=(), unavailable=(), downloads=None,
                 bad_downloads=()):
        self.by_source = by_source or {}
        self.dead = set(dead)
        self.unavailable = set(unavailable)
        self.downloads = downloads          # url -> bytes (None entry -> 404)
        self.bad_downloads = set(bad_downloads)

    def available(self, source):
        return source.name not in self.unavailable

    def search(self, source, query_text, filters):
        if source.name in self.dead:
            raise RuntimeError(f"{source.name} timed out")
        qtokens = set(query_text.lower().split())
        out = []
        for c in self.by_source.get(source.name, []):
            if qtokens & set(c.title.lower().split()):
                out.append(c)
        return out

    def download(self, url):
        if url in self.bad_downloads:
            raise RuntimeError("404")
        if self.downloads is not None:
            data = self.downloads.get(url)
            if data is None:
                raise RuntimeError("404")
            return data
        return ("IMG:" + url).encode()      # deterministic, distinct per url


def _scene(n, shots):
    return {"scene_no": n, "layout": "centered-statement", "shots": shots,
            "on_screen_text": "", "transition": "cut", "signature_beat": (n == 1)}


def _shot(kind, content, ref):
    return {"kind": kind, "content": content, "asset_ref": ref}


COLOR_STYLE = {"palette": {"primary": "#111111", "bg": "#FFFFFF", "text": "#111111",
                           "accents": ["#2E6FF2"], "signature_highlight": "#FFD000"}}
MONO_STYLE = {"palette": {"primary": "#111111", "bg": "#FAFAF7", "text": "#222222",
                          "accents": [], "signature_highlight": "#FFD000"}}


# ======================================================================
# 1. The license truth table (the crux)
# ======================================================================
def test_license_truth_table():
    accept_pd = {
        "CC0": "cc0", "cc0": "cc0",
        "https://creativecommons.org/publicdomain/zero/1.0/": "cc0",
        "Public Domain Mark 1.0": "pdm", "PDM": "pdm",
        "https://creativecommons.org/publicdomain/mark/1.0/": "pdm",
        "Public Domain": "pd",
    }
    for raw, code in accept_pd.items():
        assert engine.normalize_license(raw) == code, raw
        d = engine.classify(code)
        assert d.verdict == "accept" and not d.requires_attribution and not d.force_sourced

    # Attribution licenses accept (BY-SA flags share-alike).
    assert engine.normalize_license("CC BY 4.0") == "by"
    assert engine.classify("by").verdict == "accept"
    assert engine.classify("by").requires_attribution
    assert engine.normalize_license("https://creativecommons.org/licenses/by/4.0/") == "by"
    assert engine.normalize_license("CC BY-SA 4.0") == "by-sa"
    assert engine.classify("by-sa").share_alike
    assert engine.classify("by-sa").requires_attribution

    # EXTRA ROW: PDM accepts, NoC-US rejects (the intentional distinction).
    assert engine.classify(engine.normalize_license("Public Domain Mark")).verdict == "accept"
    for noc in ("No Copyright - United States",
                "http://rightsstatements.org/vocab/NoC-US/1.0/"):
        assert engine.normalize_license(noc) == "noc-us", noc
        assert engine.classify("noc-us").verdict == "reject"

    # Flickr Commons / "no known copyright restrictions" -> reject (not a license).
    for nk in ("No known copyright restrictions", "Flickr Commons",
               "http://rightsstatements.org/vocab/NKC/1.0/"):
        assert engine.normalize_license(nk) == "no-known", nk
        assert engine.classify("no-known").verdict == "reject"

    # NC / ND in any combination -> reject (monetized + composited = commercial + derivative).
    for nd in ("CC BY-NC 4.0", "CC BY-ND", "CC BY-NC-SA 4.0", "CC BY-NC-ND 4.0",
               "https://creativecommons.org/licenses/by-nc/4.0/"):
        code = engine.normalize_license(nd)
        assert engine.classify(code).verdict == "reject", nd

    # Unknown / missing / all-rights-reserved -> reject.
    for bad in ("", "   ", "All rights reserved", "© 2020 Someone", "no idea"):
        assert engine.classify(engine.normalize_license(bad)).verdict == "reject", bad

    # Proprietary-but-permissive: ACCEPT but force-sourced, never auto-cleared.
    for raw, code in (("Pexels License", "pexels"), ("Pixabay License", "pixabay"),
                      ("NASA", "nasa")):
        assert engine.normalize_license(raw) == code
        d = engine.classify(code)
        assert d.verdict == "accept" and d.force_sourced and d.note


# ======================================================================
# 2. Off-allowlist rejection (membership, in code)
# ======================================================================
def test_off_allowlist_dropped():
    q = engine.derive_query("anything", COLOR_STYLE)
    good = cand("met", "anything here", "CC0")
    bad = cand("sketchy-blog", "anything here", "CC0")   # not in the allowlist
    ranked = engine.rank_candidates(q, [good, bad])
    assert good in ranked and bad not in ranked
    assert all(c.source in sources.ALLOWLIST_NAMES for c in ranked)


# ======================================================================
# 3. Shot classification — source / generate / skip + the map split
# ======================================================================
def test_classify_shot():
    assert engine.classify_shot(_shot("title", "THE TITLE", "x")).action == "skip"
    assert engine.classify_shot(_shot("quote", "a pulled quote", "x")).action == "skip"

    p = engine.classify_shot(_shot("photo", "a cotton plant", "x"))
    assert p.action == "source" and p.asset_type == "image"
    assert engine.classify_shot(_shot("footage", "city b-roll", "x")).asset_type == "video"
    assert engine.classify_shot(_shot("icon", "a gear", "x")).asset_type == "icon"

    # data-driven chart -> generate (data-viz placeholder for #6)
    gen = engine.classify_shot(_shot("chart", "cases by county over time", "x"))
    assert gen.action == "generate" and gen.asset_type == "data-viz"

    # the map split: a NAMED period artifact -> SOURCE as image (the scavenger hunt)
    arch = engine.classify_shot(_shot("map", "1929 Rand McNally map of Chicago", "x"))
    assert arch.action == "source" and arch.asset_type == "image"
    # a data-driven map -> generate
    data_map = engine.classify_shot(_shot("map", "cases by county heatmap", "x"))
    assert data_map.action == "generate" and data_map.asset_type == "data-viz"


def test_classify_shot_brand_kinds_skip():
    # Brand/chip shots are rendered in HTML by the Composition Engineer (Mason) — the
    # logos are trademarked and un-sourceable from the CC0/PD/CC allowlist, so Magpie
    # must NOT source them (and emits no asset_manifest row), like typography.
    for kind in ("brand", "chip"):
        plan = engine.classify_shot(_shot(kind, "GPT-4o vs Claude vs Gemini", "x"))
        assert plan.action == "skip", f"{kind!r} should skip"


def test_brand_shot_emits_no_asset_row():
    sb = {"scenes": [{"scene_no": 1, "shots": [
        _shot("brand", "the four logos GPT-4o Claude Gemini DeepSeek", "logos"),
        _shot("photo", "a cotton plant", "plant")]}]}
    client = FakeClient(by_source={"openverse": [cand("openverse", "cotton plant", "CC0")]},
                        downloads={"https://example.test/openverse/cotton-plant.jpg": b"\x89PNG"})
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        manifest = engine.source_assets(sb, {}, client=client, pdir=d)
    ids = [a["asset_id"] for a in manifest["assets"]]
    assert "logos" not in ids          # brand shot skipped
    assert "plant" in ids              # ordinary shot still sourced


# ======================================================================
# 4. Query derivation — era, monochrome bias, filler trim (deterministic)
# ======================================================================
def test_derive_query():
    q = engine.derive_query("a single 1929 Rand McNally map of Chicago", MONO_STYLE)
    assert q.era == "1929"
    assert q.monochrome is True
    assert "rand" in q.text and "mcnally" in q.text and "chicago" in q.text
    assert "1929" in q.text
    assert "black and white" in q.text            # monochrome bias applied
    assert "single" not in q.text and " a " not in f" {q.text} "  # filler trimmed

    # Determinism: same input, same query.
    assert engine.derive_query("a single 1929 Rand McNally map of Chicago", MONO_STYLE) == q

    # A color palette does NOT add the b/w bias; a decade is also an era.
    q2 = engine.derive_query("protest crowd in the 1960s", COLOR_STYLE)
    assert q2.era == "1960s" and not q2.monochrome and "black and white" not in q2.text

    assert engine.is_monochrome(MONO_STYLE) and not engine.is_monochrome(COLOR_STYLE)


# ======================================================================
# 5. Ranking — deterministic, license-first, rejects dropped
# ======================================================================
def test_ranking_is_deterministic_and_license_first():
    q = engine.derive_query("harbor scene", COLOR_STYLE)
    cc0 = cand("met", "harbor scene", "CC0")
    by = cand("wikimedia", "harbor scene", "CC BY 4.0", author="A. Photographer")
    bysa = cand("wikimedia", "harbor scene", "CC BY-SA 4.0", author="B")
    pexels = cand("pexels", "harbor scene", "Pexels License", author="C")
    nc = cand("openverse", "harbor scene", "CC BY-NC 4.0")   # rejected -> dropped
    ranked = engine.rank_candidates(q, [pexels, bysa, nc, cc0, by])
    assert nc not in ranked
    assert [c.license_raw for c in ranked] == ["CC0", "CC BY 4.0", "CC BY-SA 4.0",
                                               "Pexels License"]
    # Stable across runs (same key, no randomness).
    assert engine.rank_candidates(q, [cc0, by, bysa, pexels]) == \
        engine.rank_candidates(q, [pexels, bysa, by, cc0])


# ======================================================================
# 5b. Direction B — relevance-first sourcing (issue #2)
# ======================================================================
def test_relevance_first_beats_a_zero_relevance_cc0_painting():
    # The reproduced bug, INVERTED: a relevant accept-licensed stock photo must now
    # outrank a zero-relevance CC0 museum painting.
    q = engine.derive_query("a clean document with a blinking cursor", COLOR_STYLE)
    painting = cand("met", "The Crucifixion; The Last Judgment", "CC0")   # relevance 0
    photo = cand("pexels", "person typing a document, cursor blinking",
                 "Pexels License", author="P")                           # relevant
    ranked = engine.rank_candidates(q, [painting, photo])
    assert ranked[0] is photo, "a relevant photo must beat a zero-relevance CC0 painting"


def test_relevance_is_a_normalized_fraction_of_query_tokens():
    q = engine.derive_query("harbor crane cargo", COLOR_STYLE)           # 3 core tokens
    full = cand("openverse", "harbor crane cargo", "CC0")
    two = cand("openverse", "harbor crane at dusk", "CC0")               # 2 of 3
    one = cand("openverse", "harbor at dusk", "CC0")                     # 1 of 3
    none = cand("openverse", "a quiet meadow", "CC0")
    # A full / multi-token match is the clean fraction of subject tokens.
    assert engine.relevance(q, full) == 1.0
    assert abs(engine.relevance(q, two) - (2 / 3)) < 1e-9
    # A SINGLE coincidental token is discounted (it is weak evidence, not 1/3 confident):
    # it must stay strictly below the WEAK gate so it can never present as a sure match.
    assert engine.relevance(q, one) < engine.RELEVANCE_WEAK
    assert engine.relevance(q, none) == 0.0


def test_single_token_query_match_does_not_score_full_confidence():
    # The degeneracy (audit H2 / live C3): a 1-subject-token query where ONE coincidental
    # title word used to score a perfect 1.0 and sail past BOTH thresholds. A single
    # token is now capped below the WEAK gate — never a confident match.
    q = engine.derive_query("energy", COLOR_STYLE)
    assert q.tokens == ("energy",)
    hit = cand("openverse", "renewable energy wind farm at sunset", "CC0")
    assert engine.relevance(q, hit) < engine.RELEVANCE_WEAK
    assert engine.relevance(q, hit) != 1.0


def test_coal_plant_single_incidental_token_lands_below_floor():
    # REGRESSION for the live failure: a "coffee vs tea energy" scene rendered a full-bleed
    # COAL POWER PLANT image because the one token 'energy' matched and scored 1.0. The
    # subject is multi-word ("coffee tea energy"); an asset matching only that one
    # incidental token must now land BELOW the floor -> a clean placeholder ships, not the
    # off-topic coal plant.
    q = engine.derive_query("coffee vs tea energy comparison", COLOR_STYLE)
    assert "vs" not in q.tokens                       # comparison stopword stripped
    coal = cand("pexels", "coal power plant energy grid emissions", "Pexels License",
                author="P")
    assert engine.relevance(q, coal) < engine.RELEVANCE_FLOOR

    sb = {"scenes": [_scene(1, [_shot(
        "image", "coffee vs tea energy comparison", "s1-1")])]}
    client = FakeClient({"pexels": [coal]}, downloads={coal.download_url: b"\x89PNG"})
    with tempfile.TemporaryDirectory() as tmp:
        pdir = pathlib.Path(tmp)
        a = engine.source_assets(sb, COLOR_STYLE, client=client, pdir=pdir)["assets"][0]
    assert a["status"] == "placeholder", "off-topic coal plant must NOT ship as an asset"
    assert a["uri"] == engine.PLACEHOLDER_REL


def test_relevance_first_sort_keeps_more_relevant_over_better_license():
    # Direction B intent, re-confirmed after the scoring change: a clearly-more-relevant
    # candidate must outrank a better-licensed but less-relevant one (license breaks only
    # GENUINE relevance ties).
    q = engine.derive_query("harbor crane cargo ship dock pier", COLOR_STYLE)
    assert len(q.tokens) == 6
    strong = cand("pexels", "harbor crane cargo ship dock view", "Pexels License",
                  author="P")                                  # 5/6 = 0.833, worst license
    weaker = cand("met", "harbor crane cargo barge", "CC0")    # 3/6 = 0.5, best license
    assert engine.relevance(q, strong) > engine.relevance(q, weaker)
    ranked = engine.rank_candidates(q, [weaker, strong])
    assert ranked[0] is strong, "higher relevance must win over a better license"


def test_sort_bucket_is_fine_enough_to_separate_near_relevances():
    # Fix #2: the OLD primary sort key was round(relevance, 1) — a coarse 0.1 bucket that
    # collapses DIFFERENT raw relevances into one tie, then falls back to license-rank
    # (the partial re-introduction of the license-first bias). 5/6 = 0.833 and 3/4 = 0.75
    # both round to the SAME 0.8 bucket yet are clearly different; the new key (round _, 3)
    # keeps them ordered. We assert the bucket COLLISION exists and that the live key
    # SEPARATES them, so a near-but-better relevance can't be demoted to license-rank.
    five_sixths = engine.relevance(
        engine.derive_query("a b c d e f", COLOR_STYLE),
        cand("pexels", "a b c d e scene", "CC0"))             # 5/6 = 0.8333…
    three_quarters = engine.relevance(
        engine.derive_query("alpha beta gamma delta", COLOR_STYLE),
        cand("pexels", "alpha beta gamma scene", "CC0"))     # 3/4 = 0.75
    assert round(five_sixths, 1) == round(three_quarters, 1) == 0.8      # OLD key: a TIE
    assert round(five_sixths, 3) != round(three_quarters, 3)            # NEW key: separated
    assert five_sixths > three_quarters


def test_relevance_floor_prefers_placeholder_over_irrelevant_image():
    # A candidate the search RETURNS (shares a token) but that barely matches the shot
    # (below the floor) -> a clean placeholder ships, NOT the weak real image.
    sb = {"scenes": [_scene(1, [_shot(
        "image", "a busy harbor with cranes loading cargo ships", "s1-1")])]}
    weak = cand("openverse", "a busy meadow at noon", "CC0")   # shares only 'busy'
    client = FakeClient({"openverse": [weak]},
                        downloads={weak.download_url: b"\x89PNG"})
    with tempfile.TemporaryDirectory() as tmp:
        pdir = pathlib.Path(tmp)
        a = engine.source_assets(sb, COLOR_STYLE, client=client, pdir=pdir)["assets"][0]
    assert engine.relevance(engine.derive_query(
        "a busy harbor with cranes loading cargo ships", COLOR_STYLE), weak) < \
        engine.RELEVANCE_FLOOR
    assert a["status"] == "placeholder"
    assert a["uri"] == engine.PLACEHOLDER_REL
    assert "relevance" in a["flag"].lower() or "match" in a["flag"].lower()


def test_relevant_candidate_records_relevance_on_the_manifest():
    sb = {"scenes": [_scene(1, [_shot("image", "an office drawer with files", "s1-1")])]}
    good = cand("pexels", "an open office drawer full of files", "Pexels License",
                author="P")
    client = FakeClient({"pexels": [good]},
                        downloads={good.download_url: b"\x89PNG"})
    with tempfile.TemporaryDirectory() as tmp:
        a = engine.source_assets(sb, COLOR_STYLE, client=client,
                                 pdir=pathlib.Path(tmp))["assets"][0]
    assert a["status"] == "sourced"          # Pexels force-sourced
    assert a.get("relevance", 0) >= 0.5      # genuinely relevant


def test_museum_sources_dropped_without_an_archival_cue():
    avail = list(sources.SOURCES)
    plain = engine.derive_query("a scrolling code editor on a laptop", COLOR_STYLE)
    kept = {s.name for s in engine._sources_for_query(avail, plain)}
    assert "met" not in kept and "smithsonian" not in kept
    assert "loc" not in kept and "internet_archive" not in kept
    assert "openverse" in kept and "pexels" in kept     # general/stock kept

    # An era/archival cue brings the museums back.
    historical = engine.derive_query("a 1929 photograph of the harbor", COLOR_STYLE)
    kept2 = {s.name for s in engine._sources_for_query(avail, historical)}
    assert "met" in kept2 and "loc" in kept2


def test_query_construction_caps_length_and_drops_filler():
    long = ("the words 'fast and cheap' set big on a white screen, with a logo beneath, "
            "ready for a yellow highlighter band to sweep under it")
    q = engine.derive_query(long, COLOR_STYLE)
    assert len(q.tokens) <= engine.MAX_QUERY_TOKENS
    # filler/stage words trimmed
    for filler in ("the", "set", "big", "screen", "ready", "under", "with"):
        assert filler not in q.tokens
    # a salient subject word survives
    assert any(t in q.tokens for t in ("fast", "cheap", "highlighter", "yellow", "band"))


# ======================================================================
# 6. TASL attribution + completeness
# ======================================================================
def test_build_attribution():
    by = cand("wikimedia", "Harbor", "CC BY 4.0", author="A. Photographer",
              license_url="https://creativecommons.org/licenses/by/4.0/")
    tasl, complete, fields = engine.build_attribution(by, engine.classify("by"))
    assert complete
    assert "Harbor" in tasl and "A. Photographer" in tasl
    assert "Wikimedia Commons" in tasl and "CC BY" in tasl

    # CC0 needs no attribution to be complete, but provenance is still captured.
    cc0 = cand("met", "Vase", "CC0")
    _t, complete0, _f = engine.build_attribution(cc0, engine.classify("cc0"))
    assert complete0

    # CC BY with NO findable author -> attribution INCOMPLETE.
    by_noauthor = cand("wikimedia", "Harbor", "CC BY 4.0", author="")
    _t2, complete2, _f2 = engine.build_attribution(by_noauthor, engine.classify("by"))
    assert complete2 is False


# ======================================================================
# 7. source_assets end-to-end (mocked client) — statuses, ids, schema, local files
# ======================================================================
def _full_storyboard():
    return {"schema_version": "1.1", "total_scenes": 8, "scenes": [
        _scene(1, [_shot("photo", "cotton plant", "s1-1")]),
        _scene(2, [_shot("image", "harbor portrait", "s2-1")]),
        _scene(3, [_shot("image", "manhattan skyline", "s3-1")]),
        _scene(4, [_shot("image", "obscure widget", "s4-1")]),
        _scene(5, [_shot("image", "nonexistent thing", "s5-1")]),
        _scene(6, [_shot("chart", "growth by county over time", "s6-1")]),
        _scene(7, [_shot("title", "THE BIG TITLE", "s7-1")]),
        _scene(8, [_shot("map", "1929 rand mcnally chicago", "s8-1")]),
    ]}


def _full_client():
    # Non-historical subjects come from general/stock sources (museum sources are
    # deweighted unless the shot has an archival/era cue — Direction B). The one
    # historical shot ("1929 …") DOES carry an era cue, so LoC is queried for it.
    return FakeClient({
        "openverse": [cand("openverse", "cotton plant", "CC0")],           # -> cleared
        "wikimedia": [
            cand("wikimedia", "harbor portrait", "CC BY 4.0", author="A. Smith"),  # cleared
            cand("wikimedia", "obscure widget", "CC BY 4.0", author=""),    # -> sourced
        ],
        "pexels": [cand("pexels", "manhattan skyline", "Pexels License", author="P")],  # sourced
        "loc": [cand("loc", "rand mcnally chicago 1929",
                     "https://creativecommons.org/publicdomain/mark/1.0/")],  # cleared (PDM)
    })


def test_source_assets_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        pdir = pathlib.Path(tmp)
        manifest = engine.source_assets(_full_storyboard(), COLOR_STYLE,
                                        client=_full_client(), pdir=pdir)
        by_id = {a["asset_id"]: a for a in manifest["assets"]}

        # Typography shot skipped entirely (not an asset).
        assert "s7-1" not in by_id
        # Everything else present, keyed by the shot's asset_ref (the spine).
        assert set(by_id) == {"s1-1", "s2-1", "s3-1", "s4-1", "s5-1", "s6-1", "s8-1"}

        # scene_no carried; type ∈ the enum.
        assert by_id["s1-1"]["scene_no"] == 1
        assert all(a["type"] in {"image", "video", "icon", "data-viz"}
                   for a in manifest["assets"])

        # Status transitions.
        assert by_id["s1-1"]["status"] == "cleared"      # Openverse CC0
        assert by_id["s8-1"]["status"] == "cleared"      # LoC PDM (era cue keeps museums)
        assert by_id["s2-1"]["status"] == "cleared"      # CC BY + author
        assert by_id["s3-1"]["status"] == "sourced"      # Pexels (force-sourced)
        assert by_id["s4-1"]["status"] == "sourced"      # CC BY, no author -> incomplete
        assert by_id["s5-1"]["status"] == "placeholder"  # nothing found
        assert by_id["s6-1"]["status"] == "placeholder"  # data-viz -> generated
        assert "data-viz" in by_id["s6-1"]["flag"]

        # Nothing `cleared` without an accept license AND attribution AND a local uri.
        for a in manifest["assets"]:
            if a["status"] == "cleared":
                assert engine.is_acceptable(a.get("license_code", a["license"]))
                assert a["uri"] and not a["uri"].startswith("http")
        # The two sourced ones carry a flag explaining why they didn't clear.
        assert by_id["s3-1"]["flag"] and by_id["s4-1"]["flag"]

        # Every uri resolves to a real LOCAL file (no remote URLs, no dangling paths).
        for a in manifest["assets"]:
            assert not a["uri"].startswith("http")
            assert (pdir / a["uri"]).exists(), a["uri"]
        # Placeholders point at the one shared local placeholder file.
        assert by_id["s5-1"]["uri"] == engine.PLACEHOLDER_REL
        assert (pdir / engine.PLACEHOLDER_REL).exists()

        # Validates against atlas's frozen asset_manifest contract.
        stamped = {"schema_version": engine.SCHEMA_VERSION, **manifest}
        ok, errs = contracts.validate("asset_manifest", stamped)
        assert ok, errs


# ======================================================================
# 8. Graceful degradation — missing key, dead source, failed download
# ======================================================================
def test_missing_optional_key_skips_source(monkeypatch):
    # A real client reads keys from env: keyless sources are available; keyed ones are
    # NOT, when the key is absent — and that is NOT an error.
    for var in ("PEXELS_API_KEY", "PIXABAY_API_KEY", "SMITHSONIAN_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    client = sources.SourceClient()
    by_name = sources.SOURCE_BY_NAME
    assert client.available(by_name["openverse"]) is True
    assert client.available(by_name["wikimedia"]) is True
    assert client.available(by_name["pexels"]) is False
    assert client.available(by_name["pixabay"]) is False
    assert client.available(by_name["smithsonian"]) is False


def test_dead_source_is_skipped():
    sb = {"scenes": [_scene(1, [_shot("image", "harbor scene", "s1-1")])]}
    client = FakeClient({"wikimedia": [cand("wikimedia", "harbor scene", "CC0")]},
                        dead=("openverse",))   # this raises on search
    with tempfile.TemporaryDirectory() as tmp:
        manifest = engine.source_assets(sb, COLOR_STYLE, client=client, pdir=tmp)
    # The dead source didn't crash the run; the Wikimedia candidate still cleared.
    assert manifest["assets"][0]["status"] == "cleared"
    assert manifest["assets"][0]["source"] == "wikimedia"


def test_failed_download_falls_to_placeholder():
    sb = {"scenes": [_scene(1, [_shot("image", "harbor scene", "s1-1")])]}
    c = cand("met", "harbor scene", "CC0")
    client = FakeClient({"met": [c]}, bad_downloads=(c.download_url,))
    with tempfile.TemporaryDirectory() as tmp:
        pdir = pathlib.Path(tmp)
        manifest = engine.source_assets(sb, COLOR_STYLE, client=client, pdir=pdir)
        a = manifest["assets"][0]
        assert a["status"] == "placeholder"
        assert a["uri"] == engine.PLACEHOLDER_REL and (pdir / a["uri"]).exists()
        assert not a["uri"].startswith("http")    # never a dangling remote URL


def test_within_run_dedupe_shares_file_not_provenance():
    # Two shots whose winning candidates return IDENTICAL bytes share one local file,
    # but stay two asset entries with their own ids + attribution.
    sb = {"scenes": [_scene(1, [_shot("image", "twin photo", "s1-1")]),
                     _scene(2, [_shot("image", "twin photo", "s2-1")])]}
    c1 = cand("wikimedia", "twin photo", "CC0", url="https://example.test/a.jpg")
    c2 = cand("wikimedia", "twin photo", "CC0", url="https://example.test/b.jpg")
    same = b"IDENTICAL-BYTES"
    client = FakeClient({"wikimedia": [c1, c2]},
                        downloads={c1.download_url: same, c2.download_url: same})
    with tempfile.TemporaryDirectory() as tmp:
        manifest = engine.source_assets(sb, COLOR_STYLE, client=client, pdir=tmp)
    a1, a2 = manifest["assets"]
    assert a1["asset_id"] == "s1-1" and a2["asset_id"] == "s2-1"
    assert a1["uri"] == a2["uri"]                 # file shared
    assert a1["attribution"] and a2["attribution"]  # each its own provenance record


# ======================================================================
# 9. Per-source PARSERS (canned dicts, no network) — the awkward extractors
# ======================================================================
def test_source_parsers_extract_license_and_provenance():
    # Openverse — the clean case.
    ov = sources._openverse_parse({"results": [
        {"title": "Harbor", "creator": "A", "foreign_landing_url": "http://x/p",
         "url": "http://x/i.jpg", "license": "by-sa", "license_version": "4.0",
         "license_url": "http://x/l", "width": 1, "height": 1}]})
    assert ov[0].source == "openverse"
    assert engine.normalize_license(ov[0].license_raw) == "by-sa"

    # Wikimedia — license buried in extmetadata.
    wm = sources._wikimedia_parse({"query": {"pages": {"1": {
        "title": "File:Thing.jpg",
        "imageinfo": [{"url": "http://x/t.jpg", "descriptionurl": "http://x/d",
                       "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"},
                                       "Artist": {"value": "<a>B. Maker</a>"}}}]}}}})
    assert wm[0].author == "B. Maker"            # HTML flattened
    assert engine.normalize_license(wm[0].license_raw) == "by-sa"

    # The Met — isPublicDomain -> CC0; non-PD -> rejected.
    pd = sources._met_parse_object({"title": "Vase", "isPublicDomain": True,
                                    "primaryImage": "http://x/v.jpg",
                                    "objectURL": "http://x/o", "artistDisplayName": ""})
    assert engine.normalize_license(pd.license_raw) == "cc0"
    nonpd = sources._met_parse_object({"title": "X", "isPublicDomain": False,
                                       "primaryImage": "http://x/x.jpg"})
    assert engine.classify(engine.normalize_license(nonpd.license_raw)).verdict == "reject"

    # Conservative parsers: missing rights -> a raw string the truth table REJECTS.
    ia = sources._ia_parse({"response": {"docs": [
        {"identifier": "item1", "title": "Old Thing", "creator": "C"}]}})  # no licenseurl
    assert engine.classify(engine.normalize_license(ia[0].license_raw)).verdict == "reject"


# ======================================================================
# 10. The REPL [y/N] gate (standalone gate; pipeline/adapter is gate-free)
# ======================================================================
def test_repl_gate(monkeypatch):
    import chat
    with tempfile.TemporaryDirectory() as tmp:
        pdir = pathlib.Path(tmp)
        # A minimal storyboard on disk so the run-log step can load it.
        import chat_state
        chat_state.atomic_write_json(pdir / "storyboard.json",
                                     {"scenes": [_scene(1, [_shot("title", "T", "s1-1")])]})
        canned = {"schema_version": "1.0", "assets": [
            {"asset_id": "s1-1", "scene_no": 1, "type": "image", "source": "met",
             "uri": "assets/s1-1.jpg", "license": "CC0 1.0", "status": "cleared"}]}

        monkeypatch.setattr(chat, "compute_manifest", lambda path: (canned, pdir))
        monkeypatch.setattr(chat.engine, "_log_run", lambda *a, **k: None)
        out = pdir / "asset_manifest.json"

        # Gate ON + declined -> nothing written.
        monkeypatch.setattr(chat, "ask_yes_no", lambda prompt: False)
        assert chat.run_source_job(str(pdir), gate=True) is None
        assert not out.exists()

        # Gate ON + approved -> written.
        monkeypatch.setattr(chat, "ask_yes_no", lambda prompt: True)
        assert chat.run_source_job(str(pdir), gate=True) is not None
        assert out.exists()
        out.unlink()

        # Gate OFF (the pipeline/adapter path) -> written without asking.
        monkeypatch.setattr(chat, "ask_yes_no",
                            lambda prompt: (_ for _ in ()).throw(AssertionError("asked!")))
        assert chat.run_source_job(str(pdir), gate=False) is not None
        assert out.exists()


# ======================================================================
# Standalone runner (pytest-free)
# ======================================================================
def _run_all():
    import types

    class _MP:
        """A tiny monkeypatch stand-in for the __main__ runner."""
        def __init__(self):
            self._undo = []
        def setattr(self, obj, name, val):
            old = getattr(obj, name)
            self._undo.append((obj, name, old))
            setattr(obj, name, val)
        def delenv(self, name, raising=False):
            import os
            old = os.environ.pop(name, None)
            self._undo.append(("env", name, old))
        def undo(self):
            import os
            for obj, name, old in reversed(self._undo):
                if obj == "env":
                    if old is not None:
                        os.environ[name] = old
                else:
                    setattr(obj, name, old)
            self._undo = []

    passed = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and isinstance(fn, types.FunctionType)):
            continue
        mp = _MP()
        try:
            if fn.__code__.co_argcount == 1:
                fn(mp)
            else:
                fn()
            print(f"  ✓ {name}")
            passed += 1
        finally:
            mp.undo()
    print(f"\n{passed} tests passed (network off).")


if __name__ == "__main__":
    _run_all()
