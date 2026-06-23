"""Flux — the Production Coach engine. Pure + injectable.

Given a diagnosed quality target (band + the direction to move it, decided by the
CEO-owned rubric — NOT by Flux), Flux authors a SOFT-TIER coaching addendum: a few
imperative sentences appended to a craft specialist's persona/prompt to move the
metric into band on the next render, WITHOUT regressing the named sibling
properties. Flux never reads the rubric, never writes files, never decides
pass/fail — it only writes persuasive, domain-aware coaching text.
"""
from __future__ import annotations
from typing import Callable, Optional
import llm

DOMAIN = "production"
# production / craft stages this coach owns (must match atlas diagnose stages)
COACHED_STAGES = ("style", "storyboard", "narration", "compose", "audiomix", "render")

COACHING_PHILOSOPHY = (
    "You are Flux, an expert PRODUCTION coach for a video studio. You coach the craft "
    "specialists — the art director, the storyboard, the audio/narration, and the "
    "composition engineer. You think in terms of pacing and cut rhythm, motion energy "
    "and modulation, layout variety, effect discipline (the single signature beat), "
    "type, loudness and ducking, intelligibility, and audio-visual coherence. You give "
    "crisp, concrete, imperative coaching a craftsperson can act on in the next render. "
    "You NEVER invent new goals beyond the metric named, and you NEVER let a fix break a "
    "sibling property you're told to preserve."
)

def coaches_stage(stage: str) -> bool:
    return stage in COACHED_STAGES

# --- bounded research / self-study (Phase-2 step 4) -------------------------
# A coach may STUDY current best practice for a metric (web search, like Sage) —
# but research only ever produces HYPOTHESES. The rubric + held-out set PRUNE:
# a researched technique is adopted ONLY if the coaching addendum that uses it
# passes the loop's gates (and the held-out generalization check). Research widens
# what to try; the eval decides what's kept. Hard budget: at most RESEARCH_MAX_QUERIES
# web calls per proposal (the expensive op), capped well below a runaway.
RESEARCH_MAX_QUERIES = 2
RESEARCH_MAX_HYPOTHESES = 4


def _default_search(query: str, max_results: int = 5) -> list:
    """The coach's own web seam (DuckDuckGo default, no key). Lazy so offline
    tests that inject a search_fn never import it."""
    import search  # production-coach/search.py (copied from Sage's proven seam)
    return search.web_search(query, max_results=max_results)


def _research_query(band_id: str) -> str:
    prop = band_id.split(":", 1)[-1].replace("_", " ")
    return f"{DOMAIN} best practice: how to improve {prop} in a short explainer video"


def research_hypotheses(*, band_id: str, direction: str,
                        search_fn: Optional[Callable] = None,
                        chat_fn: Optional[Callable] = None,
                        max_queries: int = RESEARCH_MAX_QUERIES,
                        max_hypotheses: int = RESEARCH_MAX_HYPOTHESES) -> dict:
    """Gather current best-practice HYPOTHESES for moving `band_id`. Returns
    {hypotheses:[str], sources:[url], queries:[str], n_results, budget}. Pure +
    injectable (search_fn + chat_fn seams). Hard budget = max_queries web calls.
    Never raises — research must never crash a loop; a failure yields no hypotheses."""
    sf = search_fn or _default_search
    queries = [_research_query(band_id)][:max(0, max_queries)]
    results: list = []
    for q in queries:
        try:
            results.extend(sf(q, max_results=5) or [])
        except Exception:
            continue
    sources = [r.get("url") for r in results if isinstance(r, dict) and r.get("url")]
    hyps = _distill_hypotheses(band_id, direction, results, chat_fn, max_hypotheses)
    return {"hypotheses": hyps, "sources": sources[: max_hypotheses * 2],
            "queries": queries, "n_results": len(results),
            "budget": {"max_queries": max_queries, "queries_used": len(queries)}}


def _distill_hypotheses(band_id, direction, results, chat_fn, max_hypotheses) -> list:
    """Turn raw search results into a few concise, actionable coaching hypotheses.
    Uses chat_fn when available; else falls back to the result titles/snippets."""
    snippets = [f"- {r.get('title','').strip()}: {r.get('snippet','').strip()}"
                for r in results if isinstance(r, dict)][: max_hypotheses * 3]
    if not snippets:
        return []
    if chat_fn is not None:
        try:
            sys_p = (COACHING_PHILOSOPHY + "\n\nYou are reviewing web findings for CURRENT "
                     "best practice. Distill them into at most "
                     f"{max_hypotheses} concise, concrete, TESTABLE coaching hypotheses "
                     f"for improving the metric — one per line, imperative. These are "
                     "HYPOTHESES to be tested against the rubric, not facts; do not assert "
                     "anything you can't act on in a render.")
            usr_p = (f"Metric: {band_id}\nNeeded change: {direction}\n\nWeb findings:\n"
                     + "\n".join(snippets) + "\n\nWrite the hypotheses only, one per line.")
            txt = chat_fn(sys_p, usr_p)
            if isinstance(txt, str) and txt.strip():
                lines = [ln.strip(" -*\t") for ln in txt.splitlines() if ln.strip()]
                return lines[:max_hypotheses]
        except Exception:
            pass
    return [s.lstrip("- ") for s in snippets[:max_hypotheses]]


def _wrap(band_id: str, body: str) -> str:
    return f"## Coach note (Flux · production · target {band_id})\n{body.strip()}\n"

def _rule_addendum(band_id: str, direction: str, preserve: str) -> str:
    return _wrap(band_id, (
        f"Your last output missed the craft quality band for **{band_id}**: "
        f"{direction}. Adjust to land inside the band on the next render without "
        f"sacrificing the other quality properties.{preserve}"))

def propose_addendum(*, band_id: str, direction: str, preserve: str = "",
                     measured_value=None, owner: str = "",
                     chat_fn: Optional[Callable] = None,
                     research: bool = False, search_fn: Optional[Callable] = None,
                     max_queries: int = RESEARCH_MAX_QUERIES) -> dict:
    """Author a soft-tier production coaching addendum. Returns
    {band_id, direction, domain, owner, addendum, source, research}. source is 'llm'
    when the brain authored it, else 'rule' (deterministic fallback). When
    research=True the coach STUDIES current best practice (bounded web search) and
    folds the hypotheses into the note — but the rubric/held-out set still prunes
    (research only widens what's tried). Never raises."""
    out = {"band_id": band_id, "direction": direction, "domain": DOMAIN,
           "owner": owner, "addendum": _rule_addendum(band_id, direction, preserve),
           "source": "rule", "research": None}

    research_block = ""
    if research:
        r = research_hypotheses(band_id=band_id, direction=direction,
                                search_fn=search_fn, chat_fn=chat_fn, max_queries=max_queries)
        out["research"] = r
        if r["hypotheses"]:
            research_block = ("\n\nCurrent best-practice HYPOTHESES from research (treat as "
                              "options to try — the eval will prune what doesn't help):\n"
                              + "\n".join(f"- {h}" for h in r["hypotheses"]))

    fn = chat_fn or llm.chat
    system = (COACHING_PHILOSOPHY + "\n\nWrite ONLY the coaching addendum: 2-4 crisp "
              "imperative sentences in markdown. Respect the band and the direction "
              "exactly; do not invent new goals; keep the named sibling properties in range. "
              "If research hypotheses are provided, you MAY weave in the most relevant one, "
              "but the named band + direction still govern.")
    user = (f"Specialist being coached: {owner or 'the craft specialist'}\n"
            f"Metric to fix: {band_id}\nCurrent measured value: {measured_value}\n"
            f"Needed change: {direction}\n{preserve}{research_block}\n\n"
            "Write the coaching addendum only.")
    try:
        txt = fn(system, user)
        if isinstance(txt, str) and txt.strip():
            out["addendum"] = _wrap(band_id, txt)
            out["source"] = "llm-research" if research and out["research"] and out["research"]["hypotheses"] else "llm"
    except Exception:
        pass  # graceful: keep the rule addendum
    return out
