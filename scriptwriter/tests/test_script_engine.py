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


def test_creative_treatment_is_injected_when_present():
    captured = {}

    def chat_fn(system, user):
        captured["user"] = user
        return json.dumps(_script_json())

    treatment = {"rhythm": "hook-BUILD-PEAK-breathe-CTA",
                 "emphasis": "the loop is the whole trick",
                 "beats": [{"beat": "hook", "concept": "open cold", "emphasis_word": "loop"}]}
    engine.write_script(BRIEF, chat_fn=chat_fn, treatment=treatment)
    assert "DIRECTOR'S CREATIVE TREATMENT" in captured["user"]
    assert "the loop is the whole trick" in captured["user"]


def test_no_treatment_means_no_treatment_section():
    captured = {}

    def chat_fn(system, user):
        captured["user"] = user
        return json.dumps(_script_json())

    engine.write_script(BRIEF, chat_fn=chat_fn)            # treatment absent
    assert "DIRECTOR'S CREATIVE TREATMENT" not in captured["user"]


_NARRATIVE_INTENT = {
    "video_level": {"core_thesis": "an agent is a loop with tools",
                    "emotional_journey": "from confusion to confident clarity",
                    "tone_profile": "curious_exploration"},
    "per_scene_intent": [
        {"scene_index": 0, "arc_phase": "hook", "primary_emotion": "curiosity",
         "intensity": 9, "pacing_directive": "punchy_staccato",
         "texture_directive": "clean_high_contrast",
         "delivery_note": "open like you just learned a secret"},
        {"scene_index": 1, "arc_phase": "peak", "primary_emotion": "awe",
         "intensity": 10, "pacing_directive": "contemplative",
         "texture_directive": "cinematic_widescreen", "delivery_note": "let it land"}],
}


def test_narrative_intent_is_injected_per_scene_when_present():
    captured = {}

    def chat_fn(system, user):
        captured["user"] = user
        return json.dumps(_script_json())

    engine.write_script(BRIEF, chat_fn=chat_fn, narrative_intent=_NARRATIVE_INTENT)
    user = captured["user"]
    assert "THE EMOTIONAL SCORE" in user
    # the per-scene emotional directive lands as a concrete instruction
    assert "SCENE 1: This scene is in the 'hook' phase" in user
    assert "curiosity at intensity 9/10" in user
    assert "punchy_staccato" in user
    assert "open like you just learned a secret" in user
    # the pacing/awe word-shape rules are present so Marlow constrains sentence length
    assert "max 12 words" in user
    assert "ellipsis" in user


def test_no_narrative_intent_means_no_emotional_score_section():
    captured = {}

    def chat_fn(system, user):
        captured["user"] = user
        return json.dumps(_script_json())

    engine.write_script(BRIEF, chat_fn=chat_fn)            # intent absent
    assert "THE EMOTIONAL SCORE" not in captured["user"]


_MOTION_MOOD_BOARD = {
    "video_level": {"global_tempo": "brisk_and_urgent", "global_texture": "grain",
                    "dominant_motion_philosophy": "motion is punctuation, not decoration"},
    "beat_map": [
        {"beat_id": "b-hook", "arc_phase": "hook", "primary_emotion": "curiosity",
         "intensity": 9, "pacing_profile": "rapid_staccato",
         "dominant_effect": "stutter-12fps", "transition_in": "cut",
         "layout_family": "centered-statement", "scene_duration_target_sec": 8,
         "visual_mood_ref": "the 2001 corridor"},
        {"beat_id": "b-peak", "arc_phase": "peak", "primary_emotion": "awe",
         "intensity": 10, "pacing_profile": "held_stillness",
         "dominant_effect": "highlighter-FFD000", "transition_in": "dip-to-black",
         "layout_family": "big-number", "scene_duration_target_sec": 15}],
}


def test_get_pacing_rules_returns_concrete_rules_per_profile():
    assert "max 12 words" in engine.get_pacing_rules("rapid_staccato").lower() \
        or "12 words" in engine.get_pacing_rules("rapid_staccato")
    # every profile resolves to a non-empty rule block; an unknown one falls back
    for p in ("rapid_staccato", "steady_build", "slow_reveal", "held_stillness",
              "conversational_flow"):
        assert engine.get_pacing_rules(p).strip()
    assert engine.get_pacing_rules("nonsense") == engine.get_pacing_rules("conversational_flow")


def test_motion_mood_board_is_injected_per_beat_when_present():
    captured = {}

    def chat_fn(system, user):
        captured["user"] = user
        return json.dumps(_script_json())

    engine.write_script(BRIEF, chat_fn=chat_fn, motion_mood_board=_MOTION_MOOD_BOARD)
    user = captured["user"]
    assert "MOTION MOOD BOARD" in user
    assert "brisk_and_urgent" in user           # the global tempo governs pacing
    assert "motion is punctuation" in user       # the motion philosophy is surfaced
    assert "rapid_staccato" in user              # the hook beat's pacing profile
    assert "15" in user                          # the peak beat's duration target
    # the concrete pacing RULES (from get_pacing_rules) are injected so Marlow obeys them
    assert "max 12 words" in user


def test_no_motion_mood_board_means_no_motion_section():
    captured = {}

    def chat_fn(system, user):
        captured["user"] = user
        return json.dumps(_script_json())

    engine.write_script(BRIEF, chat_fn=chat_fn)            # mood board absent
    assert "MOTION MOOD BOARD" not in captured["user"]


def test_motion_mood_board_does_not_relax_the_brief_fence():
    # The mood board shapes HOW (pacing), never WHAT — every claim still resolves.
    script = engine.write_script(BRIEF, chat_fn=_chat_returning(_script_json()),
                                 motion_mood_board=_MOTION_MOOD_BOARD)
    for scene in script["scenes"]:
        for c in scene["claims"]:
            ok, _ = engine.resolve_source_ref(c["source_ref"], BRIEF["sources"])
            assert ok, (c["claim_id"], c["source_ref"])


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


# ----------------------------------------------------------------------
# Label-aware numeric citation — the collision fix. When two key_statistics
# entries share a figure (e.g. 72.7% for different model+benchmark), a claim must
# be cited by figure AND model/benchmark label, not the bare number.
# ----------------------------------------------------------------------
BRIEF_COLLIDE = {
    "topic": "frontier model coding scores",
    "angle": "what the benchmarks really say",
    "target_audience": "developers",
    "overview": "Two different results share 72.7% — different model, different test.",
    "verified_facts": [
        {"claim": "Frontier coding models now cluster tightly at the top.",
         "sources": ["https://ai.example/cluster"], "confidence": "high"},
    ],
    "key_statistics": [
        {"stat": "Claude Opus 4.6 — OSWorld", "value": "72.7%", "date": "June 2026",
         "source": "https://osworld.example/opus"},                              # idx 1
        {"stat": "Claude Sonnet 4.6 — SWE-bench Verified", "value": "72.7%",
         "date": "June 2026", "source": "https://swebench.example/sonnet"},      # idx 2
    ],
    "sources": [
        {"url": "https://ai.example/cluster", "title": "Cluster"},          # 0
        {"url": "https://osworld.example/opus", "title": "OSWorld board"},   # 1
        {"url": "https://swebench.example/sonnet", "title": "SWE board"},    # 2
    ],
}


def _collide_script(support):
    """Brain output asserting the Sonnet/SWE 72.7% — tagged however the test wants."""
    return {
        "working_title": "Reading the Benchmarks",
        "hook": "Two headlines, the same 72.7% — and it's not the same result.",
        "cta": "Which benchmark matters for your work?",
        "scenes": [
            {"beat": "hook", "point": "same number, different test",
             "narration": "Two headlines cite 72.7% for different models on different "
                          "tests.", "on_screen_text": "same number?",
             "visual_note": "split", "duration_est_sec": 5, "claims": []},
            {"beat": "point", "point": "sonnet on swe-bench",
             "narration": "By a June 2026 board, Claude Sonnet 4.6 hit 72.7% on "
                          "SWE-bench Verified.",
             "on_screen_text": "SWE-bench result", "visual_note": "a",
             "duration_est_sec": 8,
             "claims": [{"text": "By a June 2026 board, Claude Sonnet 4.6 scored 72.7% "
                         "on SWE-bench Verified.", "support": support}]},
            {"beat": "cta", "point": "turn outward", "narration": "Which one?",
             "on_screen_text": "", "visual_note": "host", "duration_est_sec": 4,
             "claims": []},
        ],
    }


def test_colliding_figure_is_cited_by_label_not_bare_number():
    # The brain mis-tags the Sonnet/SWE-bench claim to the Opus/OSWorld entry (S0);
    # both stats carry 72.7%. The reconciler must re-point it to the SWE-bench source
    # (idx 2) using the model/benchmark label, not leave it on the bare-number match.
    script = engine.write_script(BRIEF_COLLIDE,
                                 chat_fn=_chat_returning(_collide_script("S0")))
    stamped = {"schema_version": contracts.CONTRACT_VERSION, **script}
    ok, errors = contracts.validate("script", stamped)
    assert ok, errors
    claim = next(c for s in script["scenes"] for c in s["claims"]
                 if "72.7" in engine._figures(c["text"]))
    assert claim["source_ref"] == 2          # Sonnet/SWE-bench, NOT Opus/OSWorld (1)
    assert engine.find_numeric_citation_problems(script, BRIEF_COLLIDE) == []

    # And tagging it correctly (S1) keeps it on the same correct source.
    script2 = engine.write_script(BRIEF_COLLIDE,
                                  chat_fn=_chat_returning(_collide_script("S1")))
    claim2 = next(c for s in script2["scenes"] for c in s["claims"]
                  if "72.7" in engine._figures(c["text"]))
    assert claim2["source_ref"] == 2


def test_number_only_match_with_wrong_label_is_flagged():
    # A claim whose figure (72.7%) matches a stat value but whose model/benchmark
    # matches NEITHER entry's label must be flagged — not silently number-matched.
    bad = {"scenes": [{"scene_no": 1, "point": "p", "narration": "n", "claims": [
        {"claim_id": "s1c1", "text": "GPT-5 scored 72.7% on MMLU.",
         "source_ref": 1}]}]}
    probs = engine.find_numeric_citation_problems(bad, BRIEF_COLLIDE)
    assert len(probs) == 1
    assert probs[0]["claim_id"] == "s1c1"
    assert probs[0]["expected_source_idx"] == []   # no entry agreed on the label

    # such a claim is also DROPPED by assemble (it can't be correctly cited)
    obj = _collide_script("S1")
    obj["scenes"][1]["claims"] = [{"text": "GPT-5 scored 72.7% on MMLU.",
                                   "support": "S1"}]
    script = engine.write_script(BRIEF_COLLIDE, chat_fn=_chat_returning(obj))
    assert all("72.7" not in engine._figures(c["text"])
               for s in script["scenes"] for c in s["claims"])


# ----------------------------------------------------------------------
# Qualitative citation auto-repair — the qualitative analog of the numeric
# reconciler. A TRUE claim must not hard-block at the fact-check gate because the
# brain mis-tagged it to the WRONG fact's source. The engine measures content-token
# overlap between the claim and each verified_fact, repairs a clearly-better unique
# match, and FLAGS (never silently re-points) weak/ambiguous ones. All offline.
# ----------------------------------------------------------------------
# Modeled on the live failure: a legacy-GPT-4o claim (matches fact F0) was mis-tagged
# to the DeepSeek-pricing fact's source (F1). Each fact carries its OWN sources.
BRIEF_QUAL = {
    "topic": "the 2026 model landscape",
    "angle": "what's legacy and what's cheap",
    "target_audience": "developers",
    "overview": "Some models are legacy; some are far cheaper.",
    "verified_facts": [
        {"claim": "GPT-4o is now a legacy model, superseded at the frontier by newer "
         "GPT-5.x releases.",
         "sources": ["https://cutoffs.example/gpt", "https://council.example/bench"],
         "confidence": "high"},                                          # F0 -> {1,2}
        {"claim": "DeepSeek's API pricing is dramatically lower than Western frontier "
         "models — roughly an order of magnitude cheaper.",
         "sources": ["https://pricing.example/deepseek", "https://tracker.example/api"],
         "confidence": "high"},                                          # F1 -> {3,4}
    ],
    # A price stat whose figure (5) collides with the version digit in "GPT-5.x": it
    # would mis-route the legacy claim to the numeric path if model-version digits were
    # treated as figures. The glue-fix in _figures keeps the qualitative repair firing.
    "key_statistics": [
        {"stat": "API input price — GPT-4o", "value": "$5 / 1M tokens", "date": "",
         "source": "https://tracker.example/api"},                       # S0 -> idx 4
    ],
    "sources": [
        {"url": "https://ai.example/intro", "title": "Intro"},               # 0
        {"url": "https://cutoffs.example/gpt", "title": "Cutoffs"},          # 1
        {"url": "https://council.example/bench", "title": "Bench council"},  # 2
        {"url": "https://pricing.example/deepseek", "title": "DeepSeek price"},  # 3
        {"url": "https://tracker.example/api", "title": "API tracker"},      # 4
    ],
}

_LEGACY = "GPT-4o is now a legacy model, superseded by GPT-5.x releases."
_PRICING = "DeepSeek's API pricing is roughly an order of magnitude cheaper."
_OFFTOPIC = "Espresso crema is mostly carbon dioxide and emulsified oils."


def _qual_script(point_claims):
    """A well-formed brain reply: claimless hook, one point scene, claimless cta."""
    return {
        "working_title": "Legacy and Cheap",
        "hook": "One of these models is already a generation behind.",
        "cta": "Which one are you still paying for?",
        "scenes": [
            {"beat": "hook", "point": "behind", "narration": "One is already behind.",
             "on_screen_text": "behind", "visual_note": "a", "duration_est_sec": 5,
             "claims": []},
            {"beat": "point", "point": "the point", "narration": "Here's the point.",
             "on_screen_text": "x", "visual_note": "b", "duration_est_sec": 8,
             "claims": point_claims},
            {"beat": "cta", "point": "out", "narration": "Which one?",
             "on_screen_text": "", "visual_note": "c", "duration_est_sec": 4,
             "claims": []},
        ],
    }


def test_content_tokens_drops_prose_connectors():
    toks = engine._content_tokens("GPT-4o is now a legacy model, superseded by GPT-5.x")
    assert {"gpt", "4o", "legacy", "model", "superseded", "5.x"} <= toks
    assert "is" not in toks and "now" not in toks and "by" not in toks
    assert engine._content_tokens("") == set()


def test_is_stat_tag_distinguishes_families():
    assert engine._is_stat_tag("S3") and engine._is_stat_tag("s0")
    assert not engine._is_stat_tag("F3")
    assert not engine._is_stat_tag("3")
    assert not engine._is_stat_tag(None)


def test_model_version_digits_are_not_read_as_statistics():
    # "GPT-4o" / "GPT-5.x" / "V3" are identities, not figures — so a version claim is
    # never misrouted to the numeric path (which would starve the qualitative repair).
    assert engine._figures("GPT-4o is superseded by GPT-5.x") == set()
    assert engine._figures("DeepSeek V3 and V4") == set()
    # genuine statistics are still captured ("1M" is an identity-glued digit, dropped)
    assert engine._figures("priced at $5 per 1M tokens") == {"5"}
    assert engine._figures("scored 76.8% on SWE-bench") == {"76.8"}


def test_qualitative_repair_fires_despite_a_colliding_version_digit():
    # BRIEF_QUAL carries a "$5" price stat; the legacy claim mentions "GPT-5.x". Without
    # the glue-fix the stray "5" would route the claim to the numeric path. It must
    # still take the qualitative path and be repaired to F0's source.
    obj = _qual_script([{"text": _LEGACY, "support": "F1"}])
    script = engine.write_script(BRIEF_QUAL, chat_fn=_chat_returning(obj))
    claim = next(c for s in script["scenes"] for c in s["claims"])
    assert claim["source_ref"] == 1            # repaired qualitatively, not dropped
    assert engine.find_numeric_citation_problems(script, BRIEF_QUAL) == []
    assert engine.find_qualitative_citation_problems(script, BRIEF_QUAL) == []


def test_qualitative_claim_is_repointed_to_best_matching_fact():
    # (i) The brain mis-tags the legacy claim to F1 (DeepSeek pricing -> source 3),
    # but its TEXT matches F0. The engine re-points it to F0's first source (1).
    obj = _qual_script([{"text": _LEGACY, "support": "F1"}])
    script = engine.write_script(BRIEF_QUAL, chat_fn=_chat_returning(obj))
    claim = next(c for s in script["scenes"] for c in s["claims"])
    assert claim["source_ref"] == 1            # repaired to F0's first source, not 3
    assert engine.find_qualitative_citation_problems(script, BRIEF_QUAL) == []
    stamped = {"schema_version": contracts.CONTRACT_VERSION, **script}
    ok, errors = contracts.validate("script", stamped)
    assert ok, errors


def test_qualitative_claim_with_no_fact_match_is_flagged_not_repointed():
    # (ii) A claim matching NO fact above threshold is left where it is and FLAGGED —
    # never silently re-pointed (we don't guess a fact for an off-topic line).
    obj = _qual_script([{"text": _OFFTOPIC, "support": "F0"}])  # tagged F0 -> source 1
    script = engine.write_script(BRIEF_QUAL, chat_fn=_chat_returning(obj))
    claim = next(c for s in script["scenes"] for c in s["claims"])
    assert claim["source_ref"] == 1            # untouched, NOT re-pointed
    probs = engine.find_qualitative_citation_problems(script, BRIEF_QUAL)
    assert len(probs) == 1
    assert probs[0]["reason"] == "low_confidence"
    assert probs[0]["expected_source_idx"] == []


def test_correctly_cited_qualitative_claim_is_left_untouched():
    # (iii) Both claims tagged correctly -> neither ref moves, nothing flagged.
    obj = _qual_script([{"text": _LEGACY, "support": "F0"},
                        {"text": _PRICING, "support": "F1"}])
    script = engine.write_script(BRIEF_QUAL, chat_fn=_chat_returning(obj))
    legacy_ref = next(c["source_ref"] for s in script["scenes"] for c in s["claims"]
                      if "legacy" in c["text"].lower())
    pricing_ref = next(c["source_ref"] for s in script["scenes"] for c in s["claims"]
                       if "pricing" in c["text"].lower())
    assert legacy_ref == 1                      # F0's source, unchanged
    assert pricing_ref == 3                     # F1's source, unchanged
    assert engine.find_qualitative_citation_problems(script, BRIEF_QUAL) == []


def test_qualitative_guard_flags_a_miscited_claim_directly():
    # The guard (run on a raw script, before auto-repair) flags a legacy claim cited
    # to F1's source as a `mismatch`, naming F0's sources; the same claim cited to F0
    # is clean; a claim cited to a non-fact source is out of scope (skipped).
    bad = {"scenes": [{"scene_no": 1, "point": "p", "narration": "n", "claims": [
        {"claim_id": "s1c1", "text": _LEGACY, "source_ref": 3}]}]}
    probs = engine.find_qualitative_citation_problems(bad, BRIEF_QUAL)
    assert len(probs) == 1
    assert probs[0]["claim_id"] == "s1c1"
    assert probs[0]["reason"] == "mismatch"
    assert probs[0]["expected_source_idx"] == [1, 2]

    good = {"scenes": [{"scene_no": 1, "point": "p", "narration": "n", "claims": [
        {"claim_id": "s1c1", "text": _LEGACY, "source_ref": 1}]}]}
    assert engine.find_qualitative_citation_problems(good, BRIEF_QUAL) == []

    off = {"scenes": [{"scene_no": 1, "point": "p", "narration": "n", "claims": [
        {"claim_id": "s1c1", "text": _LEGACY, "source_ref": 0}]}]}  # non-fact source
    assert engine.find_qualitative_citation_problems(off, BRIEF_QUAL) == []


# ----------------------------------------------------------------------
# Magnitude/ratio comparatives are quantitative — "an order of magnitude", "10x",
# "twice as fast", "half the price" assert a MAGNITUDE the brief must establish, not
# just a direction. The deterministic guard flags a magnitude no brief fact carries;
# directional language is never flagged. All offline. (The rule is prompt-driven; this
# is the advisory safety net, a sibling of the citation guards.)
# ----------------------------------------------------------------------
def test_magnitude_values_detects_ratio_phrases():
    assert engine._magnitude_values("an order of magnitude cheaper") == {10}
    assert engine._magnitude_values("10× faster") == {10}
    assert engine._magnitude_values("10x cheaper") == {10}
    assert engine._magnitude_values("twice as fast") == {2}
    assert engine._magnitude_values("half the price") == {2}
    assert engine._magnitude_values("a 5-fold increase") == {5}
    # purely directional / no ratio -> no magnitude
    assert engine._magnitude_values("dramatically cheaper") == set()
    assert engine._magnitude_values("far more capable") == set()
    assert engine._magnitude_values("") == set()


# A brief whose cheapness fact carries only the DIRECTION (no magnitude) — the live
# s5c2 case. The magnitude must NOT be asserted from this brief.
BRIEF_DIRECTIONAL = {
    "topic": "frontier model economics",
    "angle": "why open models undercut the closed frontier",
    "target_audience": "developers",
    "overview": "DeepSeek is cheaper per token; the exact multiple varies by source.",
    "verified_facts": [
        {"claim": "DeepSeek is dramatically cheaper per token than the US frontier "
         "models.", "sources": ["https://a.example/cost"], "confidence": "high"},  # F0
    ],
    "sources": [
        {"url": "https://a.example/cost", "title": "Cost comparison"},               # 0
    ],
}


def _mag_script(claim_text):
    """A script with one point scene asserting `claim_text`, cited to F0's source (0)."""
    return {"scenes": [
        {"scene_no": 1, "beat": "point", "point": "cost", "narration": "n",
         "on_screen_text": "", "visual_note": "v", "duration_est_sec": 6,
         "claims": [{"claim_id": "s1c1", "text": claim_text, "source_ref": 0}]}]}


def test_magnitude_guard_flags_unsupported_multiple():
    # The brief carries only "dramatically cheaper" (direction). A claim that adds
    # "roughly an order of magnitude" asserts a ~10× the brief doesn't establish.
    script = _mag_script("DeepSeek is dramatically cheaper than the US frontier models, "
                         "roughly an order of magnitude.")
    probs = engine.find_magnitude_comparative_problems(script, BRIEF_DIRECTIONAL)
    assert len(probs) == 1
    assert probs[0]["claim_id"] == "s1c1"
    assert probs[0]["unsupported_magnitudes"] == [10]


def test_magnitude_guard_passes_directional_language():
    # Purely directional — no magnitude asserted -> never flagged.
    script = _mag_script("DeepSeek is dramatically cheaper than the US frontier models.")
    assert engine.find_magnitude_comparative_problems(script, BRIEF_DIRECTIONAL) == []


def test_magnitude_guard_passes_when_brief_carries_the_magnitude():
    # If THIS brief's fact establishes the magnitude, the multiple is supported.
    brief = {
        "topic": "t", "overview": "o",
        "verified_facts": [
            {"claim": "DeepSeek is roughly an order of magnitude cheaper per token.",
             "sources": ["https://a.example/cost"], "confidence": "high"}],
        "sources": [{"url": "https://a.example/cost", "title": "Cost"}],
    }
    script = _mag_script("DeepSeek is about an order of magnitude cheaper.")
    assert engine.find_magnitude_comparative_problems(script, brief) == []


# ----------------------------------------------------------------------
# Creative Roundtable integration (Task 5) — write_script(use_roundtable=...)
# ----------------------------------------------------------------------
def _enhanced_script(**overrides):
    """A craftsman rewrite that STILL grounds (source_ref 0 == sci.example/crema)."""
    base = {
        "working_title": "What Crema Really Tells You",
        "hook": "Pros blind-tested crema and guessed quality no better than a coin flip.",
        "cta": "What did you used to believe about espresso?",
        "scenes": [
            {"scene_no": 1, "beat": "hook", "point": "crema is misunderstood",
             "narration": "Pros blind-tested crema and guessed no better than a coin flip.",
             "on_screen_text": "CREMA", "visual_note": "macro shot",
             "duration_est_sec": 5, "claims": []},
            {"scene_no": 2, "beat": "point", "point": "not a quality signal",
             "narration": "It's mostly gas and oil — not a grade.",
             "on_screen_text": "not a grade", "visual_note": "pull",
             "duration_est_sec": 8,
             "claims": [{"claim_id": "s2c1",
                         "text": "Crema is mostly CO2 and emulsified oils.",
                         "source_ref": 0}]},
        ],
    }
    base.update(overrides)
    return base


def _roundtable_chat(enhanced):
    """Routes the arc call vs the three sub-agent calls by a marker in the system prompt."""
    def chat(system, user):
        if "INTERNAL CRITIC" in system:
            return json.dumps([{"rank": 1, "severity": "major",
                                "principle_violated": "open on the sharpest true thing",
                                "target_text": "Crema isn't the sign of quality...",
                                "location": "scene 1 (hook)",
                                "diagnosis": "the hook is generic and abstract",
                                "impact": "the viewer doesn't lean in"}])
        if "RESEARCH ASSISTANT" in system:
            return json.dumps({"findings": [{"target_criticism_rank": 1,
                "found_detail": "blind-test detail", "detail_type": "case_study",
                "source_description": "a 2019 study", "source_url": "https://x.example",
                "suggested_use": "open on the blind test", "why_surprising": "pros assume they can read it"}]})
        if system.startswith("You are Marlow."):       # the Craftsman
            return json.dumps(enhanced)
        return json.dumps(_script_json())               # Marlow's first-pass arc
    return chat


def test_write_script_default_does_not_run_roundtable():
    calls = []

    def chat(system, user):
        calls.append(system)
        return json.dumps(_script_json())

    engine.write_script(BRIEF, chat_fn=chat)            # use_roundtable defaults off
    assert len(calls) == 1                              # only the arc call, no sub-agents


def test_write_script_with_roundtable_returns_the_enhanced_script(monkeypatch):
    monkeypatch.setattr(engine, "_roundtable_search", lambda: None)  # no network
    enhanced = _enhanced_script()
    script = engine.write_script(BRIEF, chat_fn=_roundtable_chat(enhanced),
                                 use_roundtable=True)
    # the craftsman's rewrite won, and it is still contract-valid + traceable
    assert script["hook"].startswith("Pros blind-tested crema")
    stamped = {"schema_version": contracts.CONTRACT_VERSION, **script}
    assert contracts.validate("script", stamped)[0]


def test_write_script_keeps_draft_when_enhanced_is_untraceable(monkeypatch):
    monkeypatch.setattr(engine, "_roundtable_search", lambda: None)
    # the craftsman returns a script whose claim cites a source that doesn't resolve
    broken = _enhanced_script()
    broken["scenes"][1]["claims"][0]["source_ref"] = 999
    script = engine.write_script(BRIEF, chat_fn=_roundtable_chat(broken),
                                 use_roundtable=True)
    # untraceable enhancement is rejected — the grounded draft ships instead
    assert not script["hook"].startswith("Pros blind-tested crema")
    assert engine.assert_traceable(script, BRIEF) is None  # draft is traceable


def test_write_script_roundtable_saves_log(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_roundtable_search", lambda: None)
    engine.write_script(BRIEF, chat_fn=_roundtable_chat(_enhanced_script()),
                        use_roundtable=True, project_dir=tmp_path)
    assert (tmp_path / "roundtable_log.json").exists()


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
