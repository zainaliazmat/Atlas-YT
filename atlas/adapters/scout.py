"""Adapter for Viral Scout (youtube-topic-agent) — the topic finder.

JOB: find_topics(niche) -> ranked viral topic ideas, via Scout's engine
`agent.run(niche, quiet=True)`. PERSONA `ask` is inherited from the base adapter.
"""
from __future__ import annotations

from adapters.base import Adapter


class ScoutAdapter(Adapter):
    module_name = "agent"   # youtube-topic-agent/agent.py

    def run_job(self, job_name: str, progress, **params) -> dict:
        if job_name != "find_topics":
            return {"ok": False, "text": f"Scout has no job named {job_name!r}."}

        niche = (params.get("niche") or "").strip()
        who = self.entry.display
        progress.start(self.entry.emoji, who, "scanning", niche)

        ideas = self.engine().run(niche, quiet=True)  # may raise -> tool layer wraps

        if not ideas:
            progress.fail(who, "found no usable videos")
            return {"ok": False,
                    "text": f"Scout found no usable videos for '{niche}' — the niche "
                            "may be too narrow, or the YouTube quota is exhausted."}

        progress.done(who, f"returned {len(ideas)} topic ideas")
        return {"ok": True, "text": _digest(niche, ideas), "count": len(ideas),
                "ideas": ideas}


def _digest(niche: str, ideas: list, limit: int = 10) -> str:
    """A compact, numbered digest of the ranked ideas for the orchestrator to weigh.

    NOT the raw wall — just enough signal for Atlas to pick the strongest topic and
    say why. Numbering is stable so the CEO can redirect ("research #2 instead").
    """
    out = [f"Scout's ranked topic ideas for '{niche}' (strongest first):"]
    for i, idea in enumerate(ideas[:limit], 1):
        titles = idea.get("titles") or []
        title = titles[0] if titles else "(untitled)"
        conf = idea.get("confidence", "?")
        angle = (idea.get("why") or idea.get("angle") or "").strip()
        line = f"{i}. [{conf}] {title}"
        if angle:
            line += f" — {angle}"
        out.append(line)
    return "\n".join(out)
