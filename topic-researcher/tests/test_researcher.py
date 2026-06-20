"""Offline proof for the engine — NO network, NO API keys.

Run (from the project folder):  python tests/test_researcher.py
Or:                             pytest tests/test_researcher.py

The LLM (llm.chat) and the search seam (search.*) are MOCKED throughout, so we
assert the engine's PLUMBING only:
  - topic validation rejects garbage / accepts real topics
  - route_claims sends each classification to the right pack bucket
    (VERIFIED -> verified_facts, MYTH -> myths, CONTESTED & DEVELOPING -> contested)
  - assemble_pack builds the exact final schema and never invents sources
  - gather dedupes by url and annotates credibility
  - a full run() assembles + saves JSON+MD from mocked search + LLM

HONEST NOTE: whether the real search returns good sources and whether the real LLM
classifies correctly is a MANUAL/integration check (a real `run.py research ...`).
Only the plumbing is unit-tested here.
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import researcher  # noqa: E402


# ----------------------------------------------------------------------
# topic validation
# ----------------------------------------------------------------------
def test_topic_validation_rejects_garbage():
    for bad in ("", "  ", "ab", "!!!", "1234", "asdfghjk"):
        ok, _ = researcher.validate_topic(bad)
        assert not ok, f"expected reject: {bad!r}"
    for good in ("chess", "James Webb Space Telescope", "ozempic safety"):
        ok, reason = researcher.validate_topic(good)
        assert ok, f"expected accept: {good!r} ({reason})"


# ----------------------------------------------------------------------
# route_claims — each claim lands in the right bucket
# ----------------------------------------------------------------------
def test_route_claims_buckets():
    claims = [
        {"claim": "JWST launched in Dec 2021", "classification": "VERIFIED",
         "sources": ["u1", "u2"], "confidence": "high"},
        {"claim": "JWST replaced Hubble", "classification": "MYTH",
         "correction": "It complements Hubble, not replaces it.", "sources": ["u3"]},
        {"claim": "It found definitive signs of life", "classification": "CONTESTED",
         "why": "Single preliminary study, disputed.", "sources": ["u4"]},
        {"claim": "New galaxy candidate reported this week", "classification": "DEVELOPING",
         "why": "Just announced; unconfirmed.", "sources": ["u5"]},
    ]
    routed = researcher.route_claims(claims)

    assert len(routed["verified_facts"]) == 1
    vf = routed["verified_facts"][0]
    assert vf["claim"].startswith("JWST launched") and vf["confidence"] == "high"
    assert vf["sources"] == ["u1", "u2"]

    assert len(routed["myths_and_corrections"]) == 1
    assert routed["myths_and_corrections"][0]["myth"] == "JWST replaced Hubble"
    assert "complements" in routed["myths_and_corrections"][0]["correction"]

    # BOTH contested and developing land in contested_or_uncertain
    contested = routed["contested_or_uncertain"]
    assert len(contested) == 2
    dev = [c for c in contested if c["why"].startswith("Developing")]
    assert len(dev) == 1, "DEVELOPING should be marked in its 'why'"


def test_route_claims_unknown_class_is_not_a_fact():
    # An unknown/blank classification must NOT be promoted to verified_facts.
    routed = researcher.route_claims([{"claim": "x", "classification": "", "sources": []}])
    assert routed["verified_facts"] == []
    assert len(routed["contested_or_uncertain"]) == 1


def test_route_claims_drops_empty_and_nondict():
    routed = researcher.route_claims([{"claim": "  "}, "not a dict", None])
    assert routed["verified_facts"] == routed["myths_and_corrections"] == []
    assert routed["contested_or_uncertain"] == []


# ----------------------------------------------------------------------
# assemble_pack — exact final schema, sources from gathered set only
# ----------------------------------------------------------------------
def test_assemble_pack_schema_and_sources():
    llm_pack = {
        "overview": "A space telescope.",
        "claims": [{"claim": "It is in orbit", "classification": "VERIFIED",
                    "sources": ["https://nasa.gov/x"], "confidence": "high"}],
        "key_statistics": [{"stat": "mirror", "value": "6.5 m",
                            "source": "https://nasa.gov/x", "date": "2021"}],
        "timeline": [{"date": "2021-12-25", "event": "Launch",
                      "source": "https://nasa.gov/x"}],
        "notable_quotes": [{"quote": "A new era", "who": "NASA",
                            "source": "https://nasa.gov/x"}],
        "open_questions": ["What's next?"],
        "suggested_angles": ["The engineering story"],
    }
    sources = [{"url": "https://nasa.gov/x", "title": "NASA JWST",
                "credibility_note": "Primary — government space agency."}]
    pack = researcher.assemble_pack("JWST", "the build", llm_pack, sources)

    expected_keys = {"topic", "angle", "generated", "overview", "verified_facts",
                     "key_statistics", "timeline", "myths_and_corrections",
                     "contested_or_uncertain", "notable_quotes", "open_questions",
                     "suggested_angles", "sources"}
    assert set(pack.keys()) == expected_keys
    assert pack["topic"] == "JWST" and pack["angle"] == "the build"
    assert pack["verified_facts"][0]["claim"] == "It is in orbit"
    assert pack["sources"][0]["url"] == "https://nasa.gov/x"
    assert "credibility_note" in pack["sources"][0]
    # missing buckets default to []
    assert pack["myths_and_corrections"] == []


def test_assemble_pack_coerces_bad_types():
    pack = researcher.assemble_pack("t", None, {"overview": None, "claims": None,
                                                "timeline": "oops"}, [])
    assert pack["overview"] == "" and pack["timeline"] == []
    assert pack["verified_facts"] == [] and pack["angle"] == ""


# ----------------------------------------------------------------------
# gather — dedupe by url + credibility annotation (search seam mocked)
# ----------------------------------------------------------------------
def test_gather_dedupes_and_annotates(monkeypatch):
    web = [{"url": "https://example.com/a", "title": "A", "snippet": "s",
            "source_type": "web"},
           {"url": "https://example.com/a", "title": "A dup", "snippet": "s",
            "source_type": "web"}]  # duplicate url
    wiki = [{"url": "https://en.wikipedia.org/wiki/Q", "title": "Q", "snippet": "w",
             "source_type": "wikipedia"}]
    monkeypatch.setattr(researcher.search, "web_search", lambda *a, **k: list(web))
    monkeypatch.setattr(researcher.search, "wiki_search", lambda *a, **k: list(wiki))
    monkeypatch.setattr(researcher.search, "news_search", lambda *a, **k: [])
    monkeypatch.setattr(researcher.search, "fetch_text", lambda *a, **k: "body text")

    sources = researcher.gather("Q", ["q1"], quiet=True)
    urls = [s["url"] for s in sources]
    assert urls.count("https://example.com/a") == 1, "dedupe by url failed"
    assert all("credibility_note" in s for s in sources)
    assert any(s.get("text") == "body text" for s in sources)


# ----------------------------------------------------------------------
# full run() with mocked search + LLM (no network), JSON+MD saved
# ----------------------------------------------------------------------
def test_run_end_to_end(monkeypatch, tmp_path=None):
    monkeypatch.setattr(researcher.search, "web_search",
                        lambda *a, **k: [{"url": "https://nasa.gov/x", "title": "NASA",
                                          "snippet": "telescope", "source_type": "web"}])
    monkeypatch.setattr(researcher.search, "wiki_search", lambda *a, **k: [])
    monkeypatch.setattr(researcher.search, "news_search", lambda *a, **k: [])
    monkeypatch.setattr(researcher.search, "fetch_text", lambda *a, **k: "full page text")

    fake_llm_pack = {
        "overview": "ov",
        "claims": [{"claim": "in orbit", "classification": "VERIFIED",
                    "sources": ["https://nasa.gov/x"], "confidence": "high"}],
        "key_statistics": [], "timeline": [], "notable_quotes": [],
        "open_questions": [], "suggested_angles": [],
    }

    def fake_decompose(topic, angle):
        return ["q1", "q2"]

    monkeypatch.setattr(researcher, "decompose", fake_decompose)
    monkeypatch.setattr(researcher, "classify", lambda *a, **k: fake_llm_pack)

    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(researcher, "PACKS_DIR", pathlib.Path(d))
        monkeypatch.setattr(researcher, "MEMORY", pathlib.Path(d) / "memory.json")
        pack, json_path, md_path = researcher.run("James Webb", quiet=True)

        assert pack["verified_facts"][0]["claim"] == "in orbit"
        assert json_path.exists() and md_path.exists()
        saved = json.loads(json_path.read_text())
        assert saved["topic"] == "James Webb"
        assert "# Research Pack" in md_path.read_text()
        # run was recorded in memory
        mem = json.loads((pathlib.Path(d) / "memory.json").read_text())
        assert mem["runs"][-1]["topic"] == "James Webb"


def test_run_no_sources_yields_honest_empty_pack(monkeypatch):
    # Every search source down -> no fabricated facts, an honest empty pack.
    monkeypatch.setattr(researcher.search, "web_search", lambda *a, **k: [])
    monkeypatch.setattr(researcher.search, "wiki_search", lambda *a, **k: [])
    monkeypatch.setattr(researcher.search, "news_search", lambda *a, **k: [])
    monkeypatch.setattr(researcher, "decompose", lambda t, a: ["q"])
    # classify must NOT be called when there are no sources
    def boom(*a, **k):
        raise AssertionError("classify must not run without sources")
    monkeypatch.setattr(researcher, "classify", boom)

    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(researcher, "PACKS_DIR", pathlib.Path(d))
        monkeypatch.setattr(researcher, "MEMORY", pathlib.Path(d) / "memory.json")
        pack, _, _ = researcher.run("some real topic", quiet=True)
    assert pack["verified_facts"] == []
    assert pack["sources"] == []
    assert pack["open_questions"], "should flag that nothing could be gathered"


# ----------------------------------------------------------------------
# _strip_json robustness
# ----------------------------------------------------------------------
def test_strip_json_unwraps_prose_and_fences():
    assert json.loads(researcher._strip_json('Here: {"a": 1} done')) == {"a": 1}
    assert json.loads(researcher._strip_json('```json\n[1,2,3]\n```')) == [1, 2, 3]


# ----------------------------------------------------------------------
# standalone runner (mirrors Scout's tests; no pytest required)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import types

    class _MP:
        """A tiny monkeypatch shim so tests run without pytest."""
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
