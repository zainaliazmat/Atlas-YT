"""Offline tests for the ensembled judged-eval harness (atlas/eval/judged.py).

NEVER calls the real LLM: every test injects a fake ``chat_fn``. The fakes
exercise the ensemble math, the seeded A/B order, abstention handling, graceful
degradation, and rubric-contract conformance.
"""
from __future__ import annotations

import pathlib

import pytest

import rubric
from eval.judged import analyze, judge_pairwise, _parse_winner
from eval.types import EvalContext

FIX = (pathlib.Path(__file__).resolve().parents[1] / "projects"
       / "gpt-4o-vs-claude-vs-gemini-vs-deepseek-comparison--20260621-013345-67a3")


# --- fake chat_fns ---------------------------------------------------------

def _always_a(system: str, user: str) -> str:
    return "WINNER: A"


def _always_b(system: str, user: str) -> str:
    return "WINNER: B"


def _favor_candidate(system: str, user: str) -> str:
    """Pick whichever slot is NOT a reference. References are the rubric pool
    items; the candidate is anything else. This makes 'candidate always wins'
    independent of the seeded A/B placement."""
    refs = rubric.judged_pool("hook_strength")["references"]
    refs += rubric.judged_pool("overall_polish")["references"]
    a_block = user.split("=== A ===", 1)[1].split("=== END A ===", 1)[0].strip()
    a_is_ref = any(r.strip() and r.strip() in a_block for r in refs)
    return "WINNER: B" if a_is_ref else "WINNER: A"


def _favor_reference(system: str, user: str) -> str:
    refs = rubric.judged_pool("hook_strength")["references"]
    refs += rubric.judged_pool("overall_polish")["references"]
    a_block = user.split("=== A ===", 1)[1].split("=== END A ===", 1)[0].strip()
    a_is_ref = any(r.strip() and r.strip() in a_block for r in refs)
    return "WINNER: A" if a_is_ref else "WINNER: B"


class _Alternator:
    """Alternates WINNER: A / WINNER: B on each call."""
    def __init__(self):
        self.i = 0

    def __call__(self, system: str, user: str) -> str:
        self.i += 1
        return "WINNER: A" if self.i % 2 else "WINNER: B"


def _raises(system: str, user: str) -> str:
    raise RuntimeError("simulated LLM outage")


def _unsure(system: str, user: str) -> str:
    return "i'm not sure, they're both quite good"


# ---------------------------------------------------------------------------
# (a) deterministic winner mapping -> rate 1.0 / 0.0, n votes recorded.
# ---------------------------------------------------------------------------

def test_candidate_always_wins_rate_one():
    refs = rubric.judged_pool("hook_strength")["references"]
    res = judge_pairwise("MY HOOK", list(refs), _favor_candidate, n=5, seed=0)
    assert res["rate"] == 1.0
    assert res["valid"] == 5
    assert len(res["votes"]) == 5
    assert res["wins"] == 5


def test_reference_always_wins_rate_zero():
    refs = rubric.judged_pool("hook_strength")["references"]
    res = judge_pairwise("MY HOOK", list(refs), _favor_reference, n=5, seed=0)
    assert res["rate"] == 0.0
    assert res["valid"] == 5
    assert len(res["votes"]) == 5
    assert res["wins"] == 0


# ---------------------------------------------------------------------------
# (b) alternating winners -> rate ~0.5 AND variance > 0 (ensemble spread).
# ---------------------------------------------------------------------------

def test_alternating_has_variance():
    refs = rubric.judged_pool("hook_strength")["references"]
    res = judge_pairwise("MY HOOK", list(refs), _Alternator(), n=6, seed=0)
    assert 0.0 < res["rate"] < 1.0
    assert res["variance"] is not None and res["variance"] > 0.0
    assert res["valid"] == 6
    assert abs(res["rate"] - 0.5) <= 0.5  # somewhere in the mixed middle


# ---------------------------------------------------------------------------
# (c) determinism: same seed + same fake -> identical votes/rate.
# ---------------------------------------------------------------------------

def test_seeded_determinism():
    refs = list(rubric.judged_pool("hook_strength")["references"])
    r1 = judge_pairwise("MY HOOK", refs, _Alternator(), n=5, seed=42)
    r2 = judge_pairwise("MY HOOK", refs, _Alternator(), n=5, seed=42)
    slots1 = [v["candidate_slot"] for v in r1["votes"]]
    slots2 = [v["candidate_slot"] for v in r2["votes"]]
    assert slots1 == slots2          # A/B randomization is seeded
    assert r1["rate"] == r2["rate"]
    assert [v["candidate_won"] for v in r1["votes"]] == \
           [v["candidate_won"] for v in r2["votes"]]


def test_different_seed_changes_order():
    refs = list(rubric.judged_pool("hook_strength")["references"])
    a = [v["candidate_slot"]
         for v in judge_pairwise("H", refs, _always_a, n=8, seed=0)["votes"]]
    b = [v["candidate_slot"]
         for v in judge_pairwise("H", refs, _always_a, n=8, seed=999)["votes"]]
    assert a != b  # different seeds -> different placement sequence


# ---------------------------------------------------------------------------
# (d) every Measurement maps to a real rubric band, kind/rolls_up_to match.
# ---------------------------------------------------------------------------

def test_measurements_conform_to_rubric():
    ctx = EvalContext(FIX)
    ms = analyze(ctx, chat_fn=_favor_candidate, n=5, seed=0)
    assert len(ms) == 2
    by_prop = {m.prop: m for m in ms}
    assert set(by_prop) == {"hook_strength", "overall_polish"}
    for m in ms:
        bnd = rubric.band(m.stage, m.prop)
        assert bnd is not None, f"no band for {m.stage}:{m.prop}"
        assert m.kind == "judged"
        assert bnd["kind"] == "judged"
        assert m.rolls_up_to == tuple(bnd["rolls_up_to"])
        assert m.owner == bnd["owner"]
        # candidate wins under _favor_candidate -> rate 1.0
        assert m.value == 1.0
        assert m.error is None
        assert m.detail["n"] == 5
        assert m.detail["valid_votes"] == 5
        assert "variance" in m.detail
        assert m.detail["mean"] == m.value
        assert "anchored" in m.detail


def test_overall_polish_is_textual_proxy():
    ctx = EvalContext(FIX)
    ms = analyze(ctx, chat_fn=_favor_candidate, n=5, seed=0)
    polish = next(m for m in ms if m.prop == "overall_polish")
    assert polish.detail["candidate_kind"] == "textual_digest_proxy"
    assert "proxy" in polish.detail["proxy_note"].lower()


def test_ceo_anchor_hook_wired():
    # Stubbed default: no labels file -> anchored False with an explanatory note.
    ctx = EvalContext(FIX)
    ms = analyze(ctx, chat_fn=_favor_candidate, n=5, seed=0)
    for m in ms:
        assert m.detail["anchored"] is False
        assert "anchor_note" in m.detail
    # And confirm the hook reads the rubric-provided path.
    assert rubric.ceo_anchor_path().name


# ---------------------------------------------------------------------------
# (e) graceful degradation: raising chat_fn, and empty project dir.
# ---------------------------------------------------------------------------

def test_raising_chat_fn_degrades():
    ctx = EvalContext(FIX)
    ms = analyze(ctx, chat_fn=_raises, n=5, seed=0)  # must not raise
    assert len(ms) == 2
    for m in ms:
        assert m.value is None
        assert m.error is not None
        # audit trail preserved even on degradation
        assert m.detail.get("n") == 5
        assert m.detail.get("valid_votes") == 0


def test_empty_project_dir_degrades(tmp_path):
    ctx = EvalContext(tmp_path)  # no script.json
    ms = analyze(ctx, chat_fn=_favor_candidate, n=5, seed=0)
    assert len(ms) == 2
    for m in ms:
        assert m.value is None
        assert m.error is not None


def test_judge_pairwise_empty_pool():
    res = judge_pairwise("cand", [], _always_a, n=5, seed=0)
    assert res["rate"] is None
    assert res["error"] == "empty reference pool"
    assert res["votes"] == []


# ---------------------------------------------------------------------------
# (f) unparseable vote -> abstention, excluded from the rate.
# ---------------------------------------------------------------------------

def test_unparseable_vote_abstains():
    refs = list(rubric.judged_pool("hook_strength")["references"])
    res = judge_pairwise("H", refs, _unsure, n=5, seed=0)
    assert res["valid"] == 0
    assert res["abstentions"] == 5
    assert res["rate"] is None          # excluded from the rate entirely
    assert res["error"] is not None     # all abstained


class _OneAbstainer:
    """First vote abstains; the rest favor the A slot."""
    def __init__(self):
        self.i = 0

    def __call__(self, system, user):
        self.i += 1
        return "unclear" if self.i == 1 else "WINNER: A"


def test_partial_abstention_excluded_from_rate():
    refs = list(rubric.judged_pool("hook_strength")["references"])
    res = judge_pairwise("H", refs, _OneAbstainer(), n=5, seed=0)
    assert res["abstentions"] == 1
    assert res["valid"] == 4            # 5 cast, 1 abstained
    assert res["rate"] is not None      # computed over the 4 valid votes
    # rate denominator is valid votes, not n
    assert res["wins"] / res["valid"] == res["rate"]


def test_parse_winner_robustness():
    assert _parse_winner("WINNER: A") == "A"
    assert _parse_winner("winner: b") == "B"
    assert _parse_winner("The winner is A, clearly.") == "A"
    assert _parse_winner("WINNER - B") == "B"
    assert _parse_winner("i'm not sure") is None
    assert _parse_winner("could be A or B") is None  # both named -> abstain
    assert _parse_winner(None) is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
