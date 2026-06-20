"""Offline proof for Marlow's scriptwriting engine — NO network, NO API keys.

Run (from the project folder):  python tests/test_script_engine.py
Or:                             pytest tests/test_script_engine.py

The brain (the one arc-writing LLM call) is MOCKED throughout via an injected
chat_fn, so we assert the engine's PLUMBING and the claim-traceability guard only:
  - validate_brief: rejects a brief with no facts / no sources / not a dict
  - resolve_source_ref: int / digit-string / url / null / out-of-range (parity with
    the Fact-Checker's resolver)
  - resolve_support: F-tag -> source index, off-brief source -> None, bad tag -> None
  - assemble_script: every shipped claim's source_ref resolves; ungroundable claims
    are dropped and a point/detour scene that loses all its claims is dropped too
  - claim_id uniqueness, stability, and scene-addressability
  - the emitted script is contract-valid against atlas's frozen script.schema.json
  - the hook throat-clearing heuristic
  - a fully-ungroundable draft raises (nothing ships) rather than shipping junk
  - a malformed brief is handled gracefully (validate rejects before any "call")

HONEST NOTE: whether the REAL brain writes a watchable arc, a strong hook, and a
genuinely vivid detour is a MANUAL/integration check (a real `run.py write` or a
live pipeline run). Only the plumbing + the traceability guarantee are unit-tested.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import script_engine as engine  # noqa: E402

# Make atlas's frozen contract importable so we can assert real schema validity.
_ATLAS = pathlib.Path(__file__).resolve().parent.parent.parent / "atlas"
sys.path.insert(0, str(_ATLAS))
import contracts  # noqa: E402


BRIEF = {
    "topic": "espresso",
    "angle": "what crema actually tells you",
    "target_audience": "coffee nerds",
    "overview": "Espresso myths and what's real.",
    "verified_facts": [
        {"claim": "Crema is mostly CO2 and emulsified oils.",
         "sources": ["https://sci.example/crema"], "confidence": "high"},
        {"claim": "Crema is not a reliable sign of quality.",
         "sources": ["https://coffee.example/quality"], "confidence": "high"},
        {"claim": "A fact whose source is NOT in the brief table.",
         "sources": ["https://orphan.example/x"], "confidence": "high"},
        {"claim": "A fact with no sources at all.",
         "sources": [], "confidence": "medium"},
    ],
    "sources": [
        {"url": "https://sci.example/crema", "title": "Crema science"},
        {"url": "https://coffee.example/quality", "title": "Quality myths"},
    ],
}


def _script_json(**overrides):
    """A well-formed brain reply: hook (F0), point (F1), cta (no claims)."""
    base = {
        "working_title": "What Crema Really Tells You",
        "hook": "Crema isn't the sign of quality everyone thinks it is.",
        "cta": "What did you used to believe about espresso?",
        "scenes": [
            {"beat": "hook", "point": "crema is misunderstood",
             "narration": "Crema isn't the sign of quality everyone thinks it is.",
             "on_screen_text": "CREMA", "visual_note": "macro shot of crema",
             "duration_est_sec": 5,
             "claims": [{"text": "Crema is mostly CO2 and emulsified oils.", "support": "F0"}]},
            {"beat": "point", "point": "not a quality signal",
             "narration": "It's mostly gas and oil — not a grade.",
             "on_screen_text": "not a grade", "visual_note": "side-by-side pull",
             "duration_est_sec": 8,
             "claims": [{"text": "Crema is not a reliable sign of quality.", "support": "F1"}]},
            {"beat": "cta", "point": "turn outward", "narration": "What did you believe?",
             "on_screen_text": "", "visual_note": "host to camera",
             "duration_est_sec": 4, "claims": []},
        ],
    }
    base.update(overrides)
    return base


def _chat_returning(obj):
    def chat_fn(system, user):
        return json.dumps(obj)
    return chat_fn


# ----------------------------------------------------------------------
# validate_brief
# ----------------------------------------------------------------------
def test_validate_brief_accepts_a_usable_brief():
    ok, _ = engine.validate_brief(BRIEF)
    assert ok is True


def test_validate_brief_rejects_missing_facts_sources_and_nondict():
    assert engine.validate_brief({"sources": [{"url": "x"}]})[0] is False  # no facts
    assert engine.validate_brief({"verified_facts": [{"claim": "x"}]})[0] is False  # no sources
    assert engine.validate_brief("not a dict")[0] is False
    assert engine.validate_brief({})[0] is False


# ----------------------------------------------------------------------
# resolve_source_ref — parity with the Fact-Checker's resolver
# ----------------------------------------------------------------------
def test_resolve_source_ref_variants():
    srcs = BRIEF["sources"]
    assert engine.resolve_source_ref(0, srcs) == (True, srcs[0])
    assert engine.resolve_source_ref("1", srcs) == (True, srcs[1])
    assert engine.resolve_source_ref("https://coffee.example/quality", srcs) == (True, srcs[1])
    assert engine.resolve_source_ref(999, srcs)[0] is False
    assert engine.resolve_source_ref(None, srcs) == (False, None)
    assert engine.resolve_source_ref("", srcs) == (False, None)
    assert engine.resolve_source_ref(True, srcs)[0] is False  # bool is not an index


# ----------------------------------------------------------------------
# resolve_support — the F-tag -> source index path
# ----------------------------------------------------------------------
def test_resolve_support_paths():
    facts = BRIEF["verified_facts"]
    u2i = engine._source_url_index(BRIEF)
    assert engine.resolve_support("F0", facts, u2i) == 0    # resolves to sources[0]
    assert engine.resolve_support("F1", facts, u2i) == 1
    assert engine.resolve_support("1", facts, u2i) == 1     # bare index tolerated
    assert engine.resolve_support("F2", facts, u2i) is None  # source not in brief table
    assert engine.resolve_support("F3", facts, u2i) is None  # fact has no sources
    assert engine.resolve_support("F9", facts, u2i) is None  # tag out of range
    assert engine.resolve_support(None, facts, u2i) is None
    # direct-URL fallback (brain disobeyed): honored iff real
    assert engine.resolve_support("https://sci.example/crema", facts, u2i) == 0
    assert engine.resolve_support("https://orphan.example/x", facts, u2i) is None


# ----------------------------------------------------------------------
# write_script — the happy path + the contract
# ----------------------------------------------------------------------
def test_write_script_emits_contract_valid_script():
    script = engine.write_script(BRIEF, chat_fn=_chat_returning(_script_json()))
    stamped = {"schema_version": contracts.CONTRACT_VERSION, **script}
    ok, errors = contracts.validate("script", stamped)
    assert ok, errors
    assert script["total_scenes"] == 3
    assert script["est_runtime_sec"] == 17.0


def test_every_shipped_claim_resolves_to_a_brief_source():
    script = engine.write_script(BRIEF, chat_fn=_chat_returning(_script_json()))
    for scene in script["scenes"]:
        for c in scene["claims"]:
            ok, _ = engine.resolve_source_ref(c["source_ref"], BRIEF["sources"])
            assert ok, (c["claim_id"], c["source_ref"])


def test_ungroundable_claims_are_dropped_with_their_scene():
    # Two extra point scenes tagged to ungroundable facts (F2 off-brief, F9 bad).
    obj = _script_json()
    obj["scenes"].insert(2, {
        "beat": "point", "point": "ungroundable", "narration": "off-brief claim.",
        "on_screen_text": "x", "visual_note": "y", "duration_est_sec": 6,
        "claims": [{"text": "A fact whose source is NOT in the brief table.", "support": "F2"}]})
    obj["scenes"].insert(3, {
        "beat": "point", "point": "bad tag", "narration": "made-up tag.",
        "on_screen_text": "z", "visual_note": "w", "duration_est_sec": 6,
        "claims": [{"text": "made up", "support": "F9"}]})
    script = engine.write_script(BRIEF, chat_fn=_chat_returning(obj))
    # The two ungroundable point scenes are gone; hook + point + cta survive.
    assert script["total_scenes"] == 3
    beats = [s["beat"] for s in script["scenes"]]
    assert beats == ["hook", "point", "cta"]


def test_claim_ids_are_unique_stable_and_scene_addressable():
    script = engine.write_script(BRIEF, chat_fn=_chat_returning(_script_json()))
    ids = [c["claim_id"] for s in script["scenes"] for c in s["claims"]]
    assert len(ids) == len(set(ids))                      # unique across the script
    assert ids == ["s1c1", "s2c1"]                        # scene-addressable + stable
    # re-running the same draft yields the same ids (determinism)
    again = engine.write_script(BRIEF, chat_fn=_chat_returning(_script_json()))
    assert [c["claim_id"] for s in again["scenes"] for c in s["claims"]] == ids


def test_hook_and_cta_scenes_may_carry_no_claims():
    script = engine.write_script(BRIEF, chat_fn=_chat_returning(_script_json()))
    assert script["scenes"][0]["beat"] == "hook"
    assert script["scenes"][-1]["beat"] == "cta"
    assert script["scenes"][-1]["claims"] == []           # legal, not an omission


def test_assert_traceable_guards_the_output():
    script = engine.write_script(BRIEF, chat_fn=_chat_returning(_script_json()))
    engine.assert_traceable(script, BRIEF)  # must not raise
    # a corrupted source_ref must trip the guard
    script["scenes"][1]["claims"][0]["source_ref"] = 999
    try:
        engine.assert_traceable(script, BRIEF)
    except AssertionError:
        pass
    else:
        raise AssertionError("guard should have rejected an unresolvable source_ref")


def test_fully_ungroundable_draft_raises():
    # Every scene is a point that asserts a claim that can't ground (F9). Each loses
    # its claim, each point scene is dropped, nothing survives -> the engine raises
    # rather than shipping an empty/ungrounded script. (Hook/CTA scenes are exempt
    # from the drop — see test_hook_and_cta_scenes_may_carry_no_claims — so this case
    # uses only point scenes to exercise the "nothing ships" guard.)
    obj = {
        "working_title": "x", "hook": "a hook", "cta": "a close",
        "scenes": [
            {"beat": "point", "point": "p1", "narration": "n1", "on_screen_text": "",
             "visual_note": "v", "duration_est_sec": 6,
             "claims": [{"text": "ungroundable one", "support": "F9"}]},
            {"beat": "point", "point": "p2", "narration": "n2", "on_screen_text": "",
             "visual_note": "v", "duration_est_sec": 6,
             "claims": [{"text": "ungroundable two", "support": "F2"}]},  # off-brief source
        ],
    }
    try:
        engine.write_script(BRIEF, chat_fn=_chat_returning(obj))
    except ValueError:
        pass
    else:
        raise AssertionError("a draft that grounds nothing must raise, not ship")


def test_malformed_brief_is_rejected_before_any_call():
    def exploding_chat(system, user):
        raise AssertionError("the brain must not be called on an invalid brief")
    try:
        engine.write_script({"verified_facts": []}, chat_fn=exploding_chat)
    except ValueError:
        pass
    else:
        raise AssertionError("an unusable brief must raise ValueError")


# ----------------------------------------------------------------------
# Hook discipline heuristic
# ----------------------------------------------------------------------
def test_hook_throat_clearing_heuristic():
    assert engine.hook_opens_with_throat_clearing("In this video, we'll explore taxes.")
    assert engine.hook_opens_with_throat_clearing("Welcome back to the channel!")
    assert engine.hook_opens_with_throat_clearing("  \"Have you ever wondered why...\"")
    assert not engine.hook_opens_with_throat_clearing(
        "Crema isn't the sign of quality everyone thinks it is.")
    assert not engine.hook_opens_with_throat_clearing("")


# ----------------------------------------------------------------------
# Citation discipline + numeric-citation guard (the hardening for the
# fact-check block: a statistic must be cited to the source carrying its figure,
# not a borrowed/constant ref). All offline — the brain is mocked.
# ----------------------------------------------------------------------
# A brief whose key_statistics has (i) a single-sourced/dated snapshot figure and
# (ii) a stable, multi-context figure — each with its OWN source in the table.
BRIEF_STATS = {
    "topic": "frontier LLM comparison",
    "angle": "why the leaderboard is a moving target",
    "target_audience": "developers",
    "overview": "Benchmarks move fast; some 'leads' are single-sourced snapshots.",
    "verified_facts": [
        {"claim": "The frontier leaderboard reshuffles with almost every release.",
         "sources": ["https://ai.example/churn"], "confidence": "high"},
    ],
    "key_statistics": [
        {"stat": "SWE-bench Verified top score", "value": "76.8%", "date": "Feb 2026",
         "source": "https://failingfast.io/swe"},
        {"stat": "reported training GPU-hours", "value": "2,000,000", "date": "",
         "source": "https://arxiv.org/abs/2401.00001"},
    ],
    "sources": [
        {"url": "https://ai.example/churn", "title": "Leaderboard churn"},          # 0
        {"url": "https://failingfast.io/swe", "title": "SWE-bench tracker"},        # 1
        {"url": "https://arxiv.org/abs/2401.00001", "title": "Training report"},    # 2
    ],
}


def _stats_script():
    """A well-formed brain reply that cites a verified fact (F0) and two stats (S0/S1)."""
    return {
        "working_title": "The Moving Target",
        "hook": "The benchmark leader changes almost every release.",
        "cta": "Which model are you watching?",
        "scenes": [
            {"beat": "hook", "point": "it churns",
             "narration": "The leaderboard reshuffles every release.",
             "on_screen_text": "it churns", "visual_note": "a", "duration_est_sec": 5,
             "claims": [{"text": "The frontier leaderboard reshuffles with almost "
                         "every release.", "support": "F0"}]},
            {"beat": "point", "point": "swe snapshot",
             "narration": "By one early-2026 snapshot, the SWE-bench Verified top "
                          "score was 76.8%.",
             "on_screen_text": "early-2026 snapshot", "visual_note": "b",
             "duration_est_sec": 8,
             "claims": [{"text": "By one early-2026 snapshot the SWE-bench Verified "
                         "top score was 76.8%.", "support": "S0"}]},
            {"beat": "point", "point": "compute",
             "narration": "One model reported about 2,000,000 training GPU-hours.",
             "on_screen_text": "~2M GPU-hours", "visual_note": "c",
             "duration_est_sec": 8,
             "claims": [{"text": "One report put training at about 2,000,000 "
                         "GPU-hours.", "support": "S1"}]},
            {"beat": "cta", "point": "turn outward", "narration": "Which one?",
             "on_screen_text": "", "visual_note": "host", "duration_est_sec": 4,
             "claims": []},
        ],
    }


def test_figures_normalizes_numeric_tokens():
    assert engine._figures("76.8%") == {"76.8"}
    assert engine._figures("$1,200") == {"1200"}
    assert engine._figures("about 2,000,000 GPU-hours") == {"2000000"}
    assert engine._figures("90.20") == {"90.2"}
    assert engine._figures("no numbers here") == set()


def test_resolve_support_handles_stat_tags():
    facts = BRIEF_STATS["verified_facts"]
    stats = BRIEF_STATS["key_statistics"]
    u2i = engine._source_url_index(BRIEF_STATS)
    assert engine.resolve_support("F0", facts, u2i, stats) == 0   # fact -> its source
    assert engine.resolve_support("S0", facts, u2i, stats) == 1   # stat -> ITS OWN source
    assert engine.resolve_support("S1", facts, u2i, stats) == 2
    assert engine.resolve_support("S9", facts, u2i, stats) is None  # out of range
    # backward-compatible: omitting key_statistics still resolves F-tags
    assert engine.resolve_support("F0", facts, u2i) == 0


def test_each_numeric_claim_cites_the_statistic_carrying_its_figure():
    script = engine.write_script(BRIEF_STATS, chat_fn=_chat_returning(_stats_script()))
    # contract-valid
    stamped = {"schema_version": contracts.CONTRACT_VERSION, **script}
    ok, errors = contracts.validate("script", stamped)
    assert ok, errors
    # the engine's own deterministic guard sees no numeric-citation problems
    assert engine.find_numeric_citation_problems(script, BRIEF_STATS) == []
    # and concretely: each figure is cited to the source that actually carries it
    by_fig = {}
    for s in script["scenes"]:
        for c in s["claims"]:
            for fig in engine._figures(c["text"]):
                by_fig.setdefault(fig, c["source_ref"])
    assert by_fig["76.8"] == 1        # failingfast.io/swe, not the borrowed fact source
    assert by_fig["2000000"] == 2     # arxiv training report


def test_mistagged_statistic_is_repaired_to_its_own_source():
    # The brain borrows the general fact's tag (F0 -> source 0) for the 76.8% stat.
    obj = _stats_script()
    obj["scenes"][1]["claims"][0]["support"] = "F0"
    script = engine.write_script(BRIEF_STATS, chat_fn=_chat_returning(obj))
    swe = next(c for s in script["scenes"] for c in s["claims"]
               if "76.8" in engine._figures(c["text"]))
    assert swe["source_ref"] == 1     # repaired from 0 -> the stat's real evidence
    assert engine.find_numeric_citation_problems(script, BRIEF_STATS) == []


def test_guard_flags_a_deliberately_miscited_numeric_claim():
    bad = {"scenes": [{"scene_no": 1, "point": "p", "narration": "n", "claims": [
        {"claim_id": "s1c1", "text": "The SWE-bench top score was 76.8%.",
         "source_ref": 0}]}]}                      # cites churn source, not the stat's
    probs = engine.find_numeric_citation_problems(bad, BRIEF_STATS)
    assert len(probs) == 1
    assert probs[0]["claim_id"] == "s1c1"
    assert probs[0]["expected_source_idx"] == [1]

    # the same claim cited correctly is clean
    good = {"scenes": [{"scene_no": 1, "point": "p", "narration": "n", "claims": [
        {"claim_id": "s1c1", "text": "The SWE-bench top score was 76.8%.",
         "source_ref": 1}]}]}
    assert engine.find_numeric_citation_problems(good, BRIEF_STATS) == []

    # a non-numeric claim (no statistic figure) is never flagged by this guard
    nonnum = {"scenes": [{"scene_no": 1, "point": "p", "narration": "n", "claims": [
        {"claim_id": "s1c1", "text": "The leaderboard reshuffles constantly.",
         "source_ref": 0}]}]}
    assert engine.find_numeric_citation_problems(nonnum, BRIEF_STATS) == []


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
