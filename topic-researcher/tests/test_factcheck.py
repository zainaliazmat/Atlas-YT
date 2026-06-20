"""Offline proof for the pass-2 fact-check engine — NO network, NO API keys.

Run (from the project folder):  python tests/test_factcheck.py
Or:                             pytest tests/test_factcheck.py

The brain (the JSON-mapping LLM call) and the search seam are MOCKED throughout, so
we assert the engine's PLUMBING and routing only:
  - resolve_source_ref: int index / digit string / url / null / out-of-range
  - finalize_claim sends each map result to the right terminal status, covering
    EVERY path (verified_fact, contested, myth, overstated, mis_sourced, new->verified,
    new->unverifiable, mis-sourced-by-broken-ref, unknown)
  - verdict aggregation: block iff any flagged/unverifiable
  - factcheck() end-to-end assembles the frozen report shape from mocked map+reverify
  - map_claims_against_brief parses the brain's JSON; reverify_claim uses the seam

HONEST NOTE: whether the REAL brain maps a paraphrased claim correctly and whether a
real bounded re-search corroborates a new claim is a MANUAL/integration check (a real
`/factcheck <project>` or a live pipeline run). Only the plumbing is unit-tested here.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import factcheck  # noqa: E402


BRIEF = {
    "verified_facts": [{"claim": "Water boils at 100C at sea level",
                        "sources": ["https://nist.gov/x"], "confidence": "high"}],
    "contested_or_uncertain": [{"claim": "Coffee causes longevity", "why": "observational"}],
    "myths_and_corrections": [{"myth": "We use 10% of our brains",
                               "correction": "Activity spans the whole brain."}],
    "sources": [{"url": "https://nist.gov/x", "title": "NIST"},
                {"url": "https://who.int/y", "title": "WHO"}],
}


def _claim(cid, text, source_ref):
    return {"claim_id": cid, "text": text, "source_ref": source_ref}


# ----------------------------------------------------------------------
# resolve_source_ref
# ----------------------------------------------------------------------
def test_resolve_source_ref_variants():
    srcs = BRIEF["sources"]
    assert factcheck.resolve_source_ref(0, srcs) == (True, srcs[0])
    assert factcheck.resolve_source_ref("1", srcs) == (True, srcs[1])
    assert factcheck.resolve_source_ref("https://who.int/y", srcs) == (True, srcs[1])
    assert factcheck.resolve_source_ref(999, srcs)[0] is False       # out of range
    assert factcheck.resolve_source_ref(None, srcs) == (False, None)  # no citation
    assert factcheck.resolve_source_ref("", srcs) == (False, None)
    assert factcheck.resolve_source_ref("https://nope.example/z", srcs)[0] is False


# ----------------------------------------------------------------------
# finalize_claim — every routing path
# ----------------------------------------------------------------------
def _final(claim, mapped):
    # reverify is only hit by the "new" path; default it to a sentinel that fails
    # loudly if an unexpected path calls it.
    return factcheck.finalize_claim(1, claim, mapped, BRIEF, chat_fn=_no_chat)


def _no_chat(system, user):
    raise AssertionError("chat_fn must not be called on a non-'new' path")


def test_verified_fact_with_resolving_ref_is_verified():
    out = _final(_claim("c1", "Water boils at 100C at sea level", 0),
                 {"match": "verified_fact", "note": "", "sources": ["https://nist.gov/x"]})
    assert out["status"] == "verified"
    assert out["sources"] == ["https://nist.gov/x"]
    assert out["scene_no"] == 1 and out["claim_id"] == "c1"


def test_verified_fact_but_broken_ref_is_flagged_missourced():
    # Deterministic guard: a cited ref that doesn't resolve is flagged even if the
    # brain matched it to a verified fact.
    out = _final(_claim("c1", "Water boils at 100C", 999),
                 {"match": "verified_fact", "note": "", "sources": []})
    assert out["status"] == "flagged"
    assert "resolve" in out["note"].lower()


def test_contested_match_is_flagged():
    out = _final(_claim("c2", "Coffee causes longevity", 1),
                 {"match": "contested", "note": "", "sources": []})
    assert out["status"] == "flagged" and "soften" in out["note"].lower()


def test_myth_match_is_flagged():
    out = _final(_claim("c3", "We use 10% of our brains", 0),
                 {"match": "myth", "note": "", "sources": []})
    assert out["status"] == "flagged" and "correction" in out["note"].lower()


def test_overstated_match_is_flagged():
    out = _final(_claim("c4", "Coffee triples lifespan", 1),
                 {"match": "overstated", "note": "", "sources": []})
    assert out["status"] == "flagged"


def test_mis_sourced_match_is_flagged_even_with_resolving_ref():
    # The ref resolves, but the brain judged the source doesn't carry the claim.
    out = _final(_claim("c5", "Something true but wrongly cited", 0),
                 {"match": "mis_sourced", "note": "", "sources": []})
    assert out["status"] == "flagged"


def test_unknown_match_is_conservatively_flagged():
    out = _final(_claim("c6", "mystery", None),
                 {"match": "???", "note": "", "sources": []})
    assert out["status"] == "flagged"


def test_new_claim_reverified_to_verified(monkeypatch):
    monkeypatch.setattr(factcheck, "reverify_claim",
                        lambda text, **k: {"status": "verified",
                                           "sources": ["https://found.example/z"],
                                           "note": "ok"})
    out = factcheck.finalize_claim(2, _claim("c7", "a new claim", None),
                                   {"match": "new"}, BRIEF, chat_fn=_no_chat)
    assert out["status"] == "verified"
    assert out["sources"] == ["https://found.example/z"] and out["scene_no"] == 2


def test_new_claim_unverifiable(monkeypatch):
    monkeypatch.setattr(factcheck, "reverify_claim",
                        lambda text, **k: {"status": "unverifiable", "sources": [],
                                           "note": "no source"})
    out = factcheck.finalize_claim(2, _claim("c8", "another new claim", None),
                                   {"match": "new"}, BRIEF, chat_fn=_no_chat)
    assert out["status"] == "unverifiable"


# ----------------------------------------------------------------------
# verdict aggregation
# ----------------------------------------------------------------------
def test_verdict_and_summary():
    claims = [{"status": "verified"}, {"status": "verified"},
              {"status": "flagged"}, {"status": "unverifiable"}]
    assert factcheck.verdict_for(claims) == "block"
    assert factcheck.summarize(claims) == {"verified": 2, "flagged": 1, "unverifiable": 1}
    assert factcheck.verdict_for([{"status": "verified"}]) == "pass"


# ----------------------------------------------------------------------
# iter_claims robustness
# ----------------------------------------------------------------------
def test_iter_claims_skips_garbage():
    script = {"scenes": [
        {"scene_no": 1, "claims": [{"claim_id": "c1", "text": "a"}, {"text": "no id"}]},
        "not a scene",
        {"scene_no": 2, "claims": [None, {"claim_id": "  ", "text": "blank id"}]},
    ]}
    got = [(s, c["claim_id"]) for s, c in factcheck.iter_claims(script)]
    assert got == [(1, "c1")]


# ----------------------------------------------------------------------
# factcheck() end-to-end (map + reverify mocked) -> frozen report shape
# ----------------------------------------------------------------------
def test_factcheck_end_to_end_blocks_on_a_flag(monkeypatch):
    script = {"scenes": [
        {"scene_no": 1, "claims": [
            _claim("c1", "Water boils at 100C at sea level", 0),     # verified_fact
            _claim("c2", "Coffee causes longevity", 1)]},            # contested -> flag
        {"scene_no": 2, "claims": [
            _claim("c3", "A brand new claim", None)]},               # new -> reverify
    ]}
    monkeypatch.setattr(factcheck, "map_claims_against_brief", lambda s, b, **k: {
        "c1": {"match": "verified_fact", "note": "", "sources": ["https://nist.gov/x"]},
        "c2": {"match": "contested", "note": "", "sources": []},
        "c3": {"match": "new", "note": "", "sources": []},
    })
    monkeypatch.setattr(factcheck, "reverify_claim",
                        lambda text, **k: {"status": "verified",
                                           "sources": ["https://found.example/z"], "note": "ok"})
    report = factcheck.factcheck(script, BRIEF)

    assert set(report) == {"verdict", "summary", "claims"}
    assert report["verdict"] == "block"            # c2 flagged
    assert report["summary"] == {"verified": 2, "flagged": 1, "unverifiable": 0}
    # every claim has the contract's required + emitted fields
    for c in report["claims"]:
        assert set(c) >= {"claim_id", "scene_no", "claim_text", "status", "sources", "note"}
    assert [c["claim_id"] for c in report["claims"]] == ["c1", "c2", "c3"]


def test_factcheck_all_clean_passes(monkeypatch):
    script = {"scenes": [{"scene_no": 1, "claims": [
        _claim("c1", "Water boils at 100C at sea level", 0)]}]}
    monkeypatch.setattr(factcheck, "map_claims_against_brief", lambda s, b, **k: {
        "c1": {"match": "verified_fact", "note": "", "sources": ["https://nist.gov/x"]}})
    report = factcheck.factcheck(script, BRIEF)
    assert report["verdict"] == "pass"
    assert report["summary"]["verified"] == 1


def test_factcheck_no_claims_is_a_clean_pass():
    report = factcheck.factcheck({"scenes": [{"scene_no": 1, "point": "x"}]}, BRIEF)
    assert report["verdict"] == "pass"
    assert report["claims"] == []


# ----------------------------------------------------------------------
# map_claims_against_brief — parses the brain's JSON (chat_fn mocked)
# ----------------------------------------------------------------------
def test_map_claims_parses_brain_json():
    script = {"scenes": [{"scene_no": 1, "claims": [_claim("c1", "x", 0)]}]}
    fake = ('[{"claim_id": "c1", "match": "VERIFIED_FACT", '
            '"note": "ok", "sources": ["https://nist.gov/x"]}]')
    out = factcheck.map_claims_against_brief(script, BRIEF, chat_fn=lambda s, u: fake)
    assert out["c1"]["match"] == "verified_fact"       # normalized to lower
    assert out["c1"]["sources"] == ["https://nist.gov/x"]


def test_map_claims_empty_when_no_claims():
    assert factcheck.map_claims_against_brief({"scenes": []}, BRIEF,
                                              chat_fn=_no_chat) == {}


# ----------------------------------------------------------------------
# reverify_claim — bounded search seam (mocked) -> verified / unverifiable
# ----------------------------------------------------------------------
def test_reverify_no_sources_is_unverifiable(monkeypatch):
    monkeypatch.setattr(factcheck.search, "web_search", lambda *a, **k: [])
    monkeypatch.setattr(factcheck.search, "wiki_search", lambda *a, **k: [])
    out = factcheck.reverify_claim("some claim", chat_fn=_no_chat)
    assert out["status"] == "unverifiable" and out["sources"] == []


def test_reverify_supported_is_verified(monkeypatch):
    monkeypatch.setattr(factcheck.search, "web_search",
                        lambda *a, **k: [{"url": "https://nasa.gov/z", "title": "N",
                                          "snippet": "s", "source_type": "web"}])
    monkeypatch.setattr(factcheck.search, "wiki_search", lambda *a, **k: [])
    monkeypatch.setattr(factcheck.search, "fetch_text", lambda *a, **k: "evidence body")
    monkeypatch.setattr(factcheck.search, "credibility_note", lambda u: "primary")
    out = factcheck.reverify_claim(
        "some claim",
        chat_fn=lambda s, u: '{"supported": true, "sources": ["https://nasa.gov/z"], "note": "ok"}')
    assert out["status"] == "verified" and out["sources"] == ["https://nasa.gov/z"]


def test_reverify_unsupported_is_unverifiable(monkeypatch):
    monkeypatch.setattr(factcheck.search, "web_search",
                        lambda *a, **k: [{"url": "https://blog.example/z", "title": "B",
                                          "snippet": "s", "source_type": "web"}])
    monkeypatch.setattr(factcheck.search, "wiki_search", lambda *a, **k: [])
    monkeypatch.setattr(factcheck.search, "fetch_text", lambda *a, **k: "weak body")
    monkeypatch.setattr(factcheck.search, "credibility_note", lambda u: "user-generated")
    out = factcheck.reverify_claim(
        "some claim",
        chat_fn=lambda s, u: '{"supported": false, "sources": [], "note": "thin"}')
    assert out["status"] == "unverifiable"


# ----------------------------------------------------------------------
# standalone runner (mirrors test_researcher; no pytest required)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import types

    class _MP:
        def __init__(self):
            self._undo = []
        def setattr(self, obj, name, val):
            if isinstance(obj, str):
                mod, _, attr = obj.rpartition(".")
                obj, name = sys.modules[mod], attr
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def undo(self):
            for obj, name, val in reversed(self._undo):
                setattr(obj, name, val)
            self._undo.clear()

    passed = 0
    for fn_name, fn in sorted(globals().items()):
        if not (fn_name.startswith("test_") and isinstance(fn, types.FunctionType)):
            continue
        mp = _MP()
        try:
            if "monkeypatch" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                fn(mp)
            else:
                fn()
            print(f"  ok  {fn_name}")
            passed += 1
        finally:
            mp.undo()
    print(f"\n{passed} tests passed.")
