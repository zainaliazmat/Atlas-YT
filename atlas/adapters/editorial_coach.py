"""Adapter for Quill (editorial-coach) — the Editorial / Content Coach.

ONE job: propose_addendum(band_id, direction, ...) -> a soft-tier coaching
addendum (markdown text) that nudges a CONTENT specialist (research / script /
factcheck framing / asset relevance) to move a quality metric into its rubric
band, without regressing the named sibling properties.

Quill is NOT a pipeline stage. It authors TEXT ONLY — it never writes files, never
reads or writes the rubric, never decides pass/fail. The DIRECTION to move a metric
is decided by the CEO-owned rubric and handed to Quill as data (the band decides;
the coach proposes). The improvement loop (atlas/eval/loop.py) takes the addendum
and persists it through the GUARDED soft-tier write path — so the privilege
asymmetry holds: the coach can influence only soft-tier persona/prompt text.

DECOUPLING: Quill's engine never imports atlas. The engine's LLM call is its own
`llm` seam (loaded isolated by the loader); tests inject a chat_fn.

PERSONA `ask` is inherited from base.
"""
from __future__ import annotations

from adapters.base import Adapter


class EditorialCoachAdapter(Adapter):
    module_name = "coach_engine"   # editorial-coach/coach_engine.py

    def run_job(self, job_name: str, progress, **params) -> dict:
        if job_name != "propose_addendum":
            return {"ok": False, "text": f"Quill has no job named {job_name!r}."}
        who = self.entry.display
        band_id = (params.get("band_id") or "").strip()
        direction = (params.get("direction") or "").strip()
        if not band_id or not direction:
            return {"ok": False,
                    "text": "Quill needs a band_id and a direction to coach toward."}
        if progress is not None:
            progress.start(self.entry.emoji, who, "coaching", band_id)
        try:
            res = self.engine().propose_addendum(
                band_id=band_id, direction=direction,
                preserve=params.get("preserve", "") or "",
                measured_value=params.get("measured_value"),
                owner=params.get("owner", "") or "",
                research=bool(params.get("research", False)),
            )
        except Exception as exc:
            if progress is not None:
                progress.fail(who, str(exc))
            return {"ok": False, "text": str(exc)}
        if progress is not None:
            progress.done(who, f"proposed a coaching note ({res.get('source')})")
        return {"ok": True, "text": res.get("addendum", ""), **res}
