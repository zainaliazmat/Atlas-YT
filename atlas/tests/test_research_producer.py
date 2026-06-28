"""The REAL research producer (Sage's pass-1) — driven OFFLINE via a fake engine.

Build step #8 wired Sage's research engine into the pipeline's `research` stage,
replacing the offline stub (mirroring how `produce_factcheck` replaced the factcheck
stub). These tests mock the ONE engine seam (`sage._research_engine`) so they run with
no network and no API, and assert the producer's boundary contract:

- the written research_brief.json validates against the frozen `research_brief` contract,
- it carries the engine's REAL facts/sources (not the stub's placeholder text),
- `schema_version` is stamped at the boundary,
- the real engine — NOT the stub — is the default; the stub is reachable only via the
  explicit ATLAS_RESEARCH_STUB switch, and a thin brief is flagged loudly.
"""
import pytest

import chat_state
import contracts
from adapters import sage, stubs


# ----------------------------------------------------------------------
# A tiny fake of Sage's research engine: run(topic, angle) -> (pack, json, md).
# The pack is a small-but-realistic brief — real-looking facts + real (non-example.org)
# sources — exactly the shape researcher.run() returns (minus schema_version, which the
# producer stamps at the boundary).
# ----------------------------------------------------------------------
def _real_pack(topic, angle):
    return {
        "topic": topic,
        "angle": angle or "",
        "generated": "2026-06-21 00:00:00",
        "overview": f"A substantive, sourced overview of {topic}.",
        "verified_facts": [
            {"claim": "GPT-4o, Claude, Gemini and DeepSeek differ on cost-per-token.",
             "confidence": "high",
             "sources": ["https://arxiv.org/abs/2403.00001"]},
            {"claim": "Independent benchmarks rank the models differently per task.",
             "confidence": "medium",
             "sources": ["https://en.wikipedia.org/wiki/Large_language_model"]},
        ],
        "myths_and_corrections": [],
        "contested_or_uncertain": [],
        "key_statistics": [], "timeline": [], "notable_quotes": [],
        "open_questions": [], "suggested_angles": [],
        "sources": [
            {"url": "https://arxiv.org/abs/2403.00001", "title": "Model comparison",
             "credibility_note": "primary / peer-reviewed"},
            {"url": "https://en.wikipedia.org/wiki/Large_language_model",
             "title": "LLM", "credibility_note": "encyclopedic"},
        ],
    }


class _FakeResearchEngine:
    def __init__(self, pack_fn=_real_pack):
        self._pack_fn = pack_fn
        self.calls = []

    def run(self, topic, angle=None, quiet=True):
        self.calls.append((topic, angle, quiet))
        return self._pack_fn(topic, angle), "/tmp/fake.json", "/tmp/fake.md"


@pytest.fixture
def fake_engine(monkeypatch):
    eng = _FakeResearchEngine()
    monkeypatch.setattr(sage, "_research_engine", lambda: eng)
    return eng


# ----------------------------------------------------------------------
# The real producer writes a contract-valid brief carrying the engine's content
# ----------------------------------------------------------------------
def test_real_producer_writes_validated_brief_with_injected_content(tmp_path, fake_engine):
    art = sage.produce_research(tmp_path, "GPT-4o vs Claude vs Gemini vs DeepSeek")

    # contract-valid (the pipeline validates exactly this on the artifact's data)
    ok, errors = contracts.validate("research_brief", art.data)
    assert ok, errors
    on_disk = chat_state.load_json(tmp_path / "research_brief.json", {})
    ok, errors = contracts.validate("research_brief", on_disk)
    assert ok, errors

    # schema_version stamped at the boundary (the engine doesn't set it)
    assert on_disk["schema_version"] == contracts.CONTRACT_VERSION

    # carries the engine's REAL facts + sources, NOT the stub's placeholder text
    facts = on_disk["verified_facts"]
    assert any("cost-per-token" in f["claim"] for f in facts)
    assert "placeholder" not in str(on_disk).lower()
    assert all("example.org" not in s["url"] for s in on_disk["sources"])
    assert on_disk["sources"][0]["url"] == "https://arxiv.org/abs/2403.00001"

    # the real engine was actually invoked (not the stub)
    assert fake_engine.calls and fake_engine.calls[0][0] == \
        "GPT-4o vs Claude vs Gemini vs DeepSeek"
    assert "research_quality" not in on_disk  # a real brief is not flagged thin


# ----------------------------------------------------------------------
# The stub is NO LONGER the default — the real producer routes through the engine
# ----------------------------------------------------------------------
def test_real_producer_is_not_the_stub():
    assert sage.produce_research is not stubs.produce_research


def test_no_flag_selects_the_real_engine(tmp_path, fake_engine, monkeypatch):
    monkeypatch.delenv(sage.RESEARCH_STUB_ENV, raising=False)
    sage.produce_research(tmp_path, "anything real")
    assert fake_engine.calls, "with no flag set, the real engine must be used"
    brief = chat_state.load_json(tmp_path / "research_brief.json", {})
    assert "placeholder" not in brief["overview"].lower()


# ----------------------------------------------------------------------
# The stub stays reachable ONLY via the explicit opt-in switch (logged loudly)
# ----------------------------------------------------------------------
def test_stub_only_via_explicit_flag(tmp_path, fake_engine, monkeypatch, caplog):
    monkeypatch.setenv(sage.RESEARCH_STUB_ENV, "1")
    with caplog.at_level("WARNING"):
        art = sage.produce_research(tmp_path, "espresso")
    # the real engine was NOT called; the offline placeholder was written instead
    assert not fake_engine.calls
    assert "STUB research" in art.summary
    assert any(sage.RESEARCH_STUB_ENV in r.message for r in caplog.records)
    brief = chat_state.load_json(tmp_path / "research_brief.json", {})
    ok, errors = contracts.validate("research_brief", brief)
    assert ok, errors
    assert "placeholder" in brief["overview"].lower()


# ----------------------------------------------------------------------
# Root-cause guardrail: a brief with ZERO verified facts is a FAILED research run
# (search unreachable / rate-limited), not a weak one. The producer must RAISE so the
# spine attributes the failure to `research` (transient → re-runnable) instead of
# silently flowing an empty brief downstream to Marlow, who would then fail at `script`
# with a confusing "send it back to research" message.
# ----------------------------------------------------------------------
def _empty_pack(topic, angle):
    return {
        "topic": topic, "angle": angle or "", "generated": "2026-06-21 00:00:00",
        "overview": "No sources could be gathered. Nothing is verified here.",
        "verified_facts": [], "myths_and_corrections": [], "contested_or_uncertain": [],
        "key_statistics": [], "timeline": [], "notable_quotes": [],
        "open_questions": [], "suggested_angles": [],
        "sources": [],
    }


def test_empty_brief_raises_a_transient_research_failure(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(sage, "_research_engine",
                        lambda: _FakeResearchEngine(_empty_pack))
    with caplog.at_level("WARNING"):
        with pytest.raises(sage.ThinResearchError) as exc:
            sage.produce_research(tmp_path, "obscure topic")

    # the failure reason names WHY (so the operator sees a re-runnable research failure)
    assert "verified" in str(exc.value).lower()
    assert any("thin" in r.message.lower() for r in caplog.records)
    # the brief is STILL persisted (for inspection) with the thin flag, even though the
    # stage failed — so the Inspector can show what came back.
    on_disk = chat_state.load_json(tmp_path / "research_brief.json", {})
    assert on_disk["research_quality"]["thin"] is True
    assert "zero verified facts" in on_disk["research_quality"]["reasons"]
    assert "no sources" in on_disk["research_quality"]["reasons"]


def test_thin_research_error_is_seen_as_transient_by_the_spine():
    # the spine maps a producer RAISE to failure_kind="transient" (re-runnable); a
    # ThinResearchError must therefore be an ordinary Exception (no special exemption).
    assert issubclass(sage.ThinResearchError, Exception)


def test_all_example_org_sources_are_flagged_thin(tmp_path, monkeypatch):
    def example_pack(topic, angle):
        p = _real_pack(topic, angle)
        p["sources"] = [{"url": "https://example.org/1", "title": "x",
                         "credibility_note": "placeholder"}]
        for f in p["verified_facts"]:
            f["sources"] = ["https://example.org/1"]
        return p

    monkeypatch.setattr(sage, "_research_engine",
                        lambda: _FakeResearchEngine(example_pack))
    art = sage.produce_research(tmp_path, "placeholder topic")
    on_disk = chat_state.load_json(tmp_path / "research_brief.json", {})
    assert on_disk["research_quality"]["thin"] is True
    assert "example.org" in " ".join(on_disk["research_quality"]["reasons"])
