"""Niche intake (sub-project #1.5) — niche → Scout `find_topics` → candidate cards.

The discovery step that happens BEFORE a project exists: the CEO picks a niche, Scout
finds + ranks viral-leaning topic ideas, and the CEO (or an auto-pick policy) turns one
into a production that enters the belt via the normal T1 trigger.

Scout's `find_topics` is an LLM + YouTube-Data-API job, so the dashboard reaches it through
an INJECTABLE seam (`app.state.find_topics_fn`) — `default_find_topics` runs the real Scout
adapter in prod; tests inject a fake so the suite stays offline/deterministic. This module
itself imports nothing heavy at module load (the engine is built lazily inside the default).
"""
from __future__ import annotations


def normalize_candidates(ideas, limit: int = 6) -> list[dict]:
    """Scout's raw ranked ideas → compact candidate cards for the UI. Tolerant: a non-dict
    or title-less idea degrades to a clean row, never a crash."""
    out: list[dict] = []
    for i, idea in enumerate(ideas or []):
        if not isinstance(idea, dict):
            continue
        titles = idea.get("titles") or []
        title = (titles[0] if titles else idea.get("title")) or "(untitled)"
        out.append({
            "idx": i,
            "title": str(title)[:160],
            "confidence": idea.get("confidence", "?"),
            "why": str(idea.get("why") or idea.get("angle") or "")[:200],
        })
        if len(out) >= limit:
            break
    return out


def default_find_topics(niche: str) -> dict:
    """The real seam: build Scout's adapter from the registry and run its find_topics job.
    Returns Scout's `{ok, ideas, count, text}`. Heavy imports are deferred to call time so
    importing this module never pulls the engine graph."""
    import registry
    from progress import Progress
    entry = registry.get_entry("scout")
    if entry is None:
        return {"ok": False, "text": "Scout is not registered."}
    adapter = entry.adapter_cls(entry)
    return adapter.run_job("find_topics", Progress(sink=lambda _m: None), niche=niche)
