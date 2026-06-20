"""Adapter for Sage (topic-researcher) — the researcher AND fact-checker.

TWO real jobs now (Sage was evolved in build step #2):
- research(topic, angle) -> a fact-checked research pack, via `researcher.run`.
- factcheck(topic)       -> a pass-2 fact-check of a drafted script against its
  research brief, via Sage's `factcheck.factcheck` engine.

DECOUPLING: Sage's engine emits the report as a plain dict in the frozen shape; ATLAS
owns the contract — it stamps `schema_version` and validates against
`factcheck_report.schema.json` here, at the boundary. Sage never imports atlas.

PERSONA `ask` is inherited from base.
"""
from __future__ import annotations

import pathlib

import chat_state
from adapters.base import Adapter
from adapters.loader import load_engine


# ----------------------------------------------------------------------
# The shared fact-check engine seam (one place; tests monkeypatch this)
# ----------------------------------------------------------------------
def _factcheck_engine():
    """Load Sage's `factcheck` engine module (isolated, cached by the loader)."""
    import registry  # lazy: registry imports this module, so avoid a top-level cycle
    sage_dir = registry.get_entry("sage").project_dir
    return load_engine(sage_dir, "factcheck")


def run_factcheck(pdir: pathlib.Path) -> dict:
    """Read script + brief from `pdir`, run Sage's engine, stamp + write the report.

    Returns the stamped report dict (with `schema_version`). The caller validates it
    against the frozen contract (the pipeline does this per-stage; the adapter below
    does it explicitly for the conversational path).
    """
    from contracts import CONTRACT_VERSION
    pdir = pathlib.Path(pdir)
    script = chat_state.load_json(pdir / "script.json", {})
    brief = chat_state.load_json(pdir / "research_brief.json", {})
    report = _factcheck_engine().factcheck(script, brief, quiet=True)
    report = {"schema_version": CONTRACT_VERSION, **report}
    chat_state.atomic_write_json(pdir / "factcheck_report.json", report)
    return report


def _digest(report: dict) -> str:
    """Compact digest for the Showrunner to surface at the gate: verdict + counts +
    the flagged claim_ids."""
    s = report.get("summary", {})
    flagged = [c.get("claim_id") for c in report.get("claims", [])
               if c.get("status") in ("flagged", "unverifiable")]
    lines = [f"Fact-check verdict: {str(report.get('verdict', '?')).upper()} "
             f"(verified {s.get('verified', 0)}, flagged {s.get('flagged', 0)}, "
             f"unverifiable {s.get('unverifiable', 0)})."]
    if flagged:
        lines.append("Flagged claim_ids: " + ", ".join(str(c) for c in flagged) + ".")
        lines.append("A block routes back to the script/research — it cannot be "
                     "approved away.")
    else:
        lines.append("Every claim holds against the brief.")
    return " ".join(lines)


# ----------------------------------------------------------------------
# Pipeline producer (the real factcheck stage worker; signature (pdir, topic))
# ----------------------------------------------------------------------
def produce_factcheck(pdir: pathlib.Path, topic: str):
    """REAL pass-2 producer: Sage's engine fact-checks the on-disk script vs brief.

    Replaces the step-#1 stub producer for the pipeline's `factcheck` stage. Reads
    script.json + research_brief.json from `pdir`, runs Sage's engine, writes a
    stamped factcheck_report.json (the pipeline validates it against the frozen
    contract), and returns the Artifact the spine records.
    """
    from adapters.stubs import Artifact  # lazy: stubs imports nothing of ours circular-y
    report = run_factcheck(pdir)
    s = report.get("summary", {})
    return Artifact("factcheck_report.json", "factcheck_report", report,
                    f"{s.get('verified', 0)} verified, {s.get('flagged', 0)} flagged, "
                    f"{s.get('unverifiable', 0)} unverifiable")


# ----------------------------------------------------------------------
# Project-dir resolution for the conversational factcheck (params stay {topic})
# ----------------------------------------------------------------------
def _resolve_project_dir(topic: str) -> pathlib.Path | None:
    """Best-effort: find the project dir a conversational fact-check should target.

    The registry's factcheck job takes only `topic` (no registry change), so we look
    under the pipeline's projects/ for a project that (a) has both a script and a
    brief and (b) matches the topic — newest first. Returns None if none is usable.
    """
    import pipeline  # lazy to avoid an import cycle (pipeline imports this module)
    root = pipeline.PROJECTS_DIR
    if not root.exists():
        return None
    want = pipeline._slug(topic or "")
    best: list[tuple[float, pathlib.Path]] = []
    for d in root.iterdir():
        if not (d / "script.json").exists() or not (d / "research_brief.json").exists():
            continue
        proj = chat_state.load_json(d / "project.json", {})
        ptopic = proj.get("topic") or proj.get("brief") or ""
        if want and (pipeline._slug(ptopic) == want or want in d.name):
            best.append((proj.get("updated", 0) or 0, d))
    if not best:
        return None
    best.sort(reverse=True)
    return best[0][1]


class SageAdapter(Adapter):
    module_name = "researcher"   # topic-researcher/researcher.py (the research job)

    def run_job(self, job_name: str, progress, **params) -> dict:
        if job_name == "factcheck":
            return self._run_factcheck_job(progress, **params)

        if job_name != "research":
            return {"ok": False, "text": f"Sage has no job named {job_name!r}."}

        topic = (params.get("topic") or "").strip()
        angle = (params.get("angle") or "").strip() or None
        who = self.entry.display

        # Sage validates topics in its engine; mirror it so we fail fast/cleanly.
        ok, reason = self.engine().validate_topic(topic)
        if not ok:
            progress.fail(who, reason)
            return {"ok": False, "text": reason}

        progress.start(self.entry.emoji, who, "researching", topic)
        pack, json_path, _md_path = self.engine().run(topic, angle, quiet=True)
        progress.done(who, "finished the research pack")

        return {"ok": True, "text": _research_digest(pack), "topic": topic,
                "saved": str(json_path)}

    # ---- pass-2: fact-check a drafted script against its brief (REAL engine) ----
    def _run_factcheck_job(self, progress, **params) -> dict:
        from contracts import validate
        who = self.entry.display
        topic = (params.get("topic") or "").strip()

        pdir = _resolve_project_dir(topic)
        if pdir is None:
            msg = (f"Couldn't find a project with a script + brief to fact-check for "
                   f"{topic!r}. Run the pipeline (or research + script) first.")
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg}

        if progress is not None:
            progress.start(self.entry.emoji, who, "fact-checking the script", topic)
        report = run_factcheck(pdir)
        ok, errors = validate("factcheck_report", report)
        if not ok:
            msg = f"Fact-check report failed contract validation: {'; '.join(errors)}"
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg, "saved": str(pdir / "factcheck_report.json")}
        if progress is not None:
            progress.done(who, "finished the fact-check")
        return {"ok": True, "text": _digest(report), "topic": topic,
                "saved": str(pdir / "factcheck_report.json")}


def _research_digest(pack: dict, limit: int = 5) -> str:
    """A short, talk-through-able digest of a research pack (not the raw pack).

    The full pack is already saved to topic-researcher/research_packs/ by the engine;
    this is just enough for Atlas to brief the CEO: verified facts, myths, contested
    claims, open questions, and source count.
    """
    out = []
    if pack.get("overview"):
        out.append(f"Overview: {pack['overview']}")

    vf = pack.get("verified_facts") or []
    if vf:
        out.append("\nVerified (multiple credible sources):")
        for f in vf[:limit]:
            out.append(f"  - [{f.get('confidence', '?')}] {f.get('claim', '')}")

    myths = pack.get("myths_and_corrections") or []
    if myths:
        out.append("\nMyths / corrections:")
        for m in myths[:limit]:
            out.append(f"  - MYTH: {m.get('myth', '')}  ->  {m.get('correction', '')}")

    contested = pack.get("contested_or_uncertain") or []
    if contested:
        out.append("\nContested / uncertain:")
        for c in contested[:limit]:
            out.append(f"  - {c.get('claim', '')}  (why: {c.get('why', '')})")

    oq = pack.get("open_questions") or []
    if oq:
        out.append("\nOpen questions: " + "; ".join(str(q) for q in oq[:limit]))

    n_src = len(pack.get("sources") or [])
    out.append(f"\n({n_src} sources gathered; full pack saved under "
               "topic-researcher/research_packs/.)")
    return "\n".join(out)
