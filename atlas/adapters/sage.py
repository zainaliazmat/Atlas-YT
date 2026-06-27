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

import logging
import os
import pathlib

import chat_state
from adapters.base import Adapter
from adapters.loader import load_engine

log = logging.getLogger(__name__)

# Opt-in switch back to the OFFLINE placeholder research (dev / no-network). The real
# Sage engine is the DEFAULT; set this truthy to force the stub, which is logged loudly
# so a stub run is never silently mistaken for real research.
RESEARCH_STUB_ENV = "ATLAS_RESEARCH_STUB"


def _truthy(value: str) -> bool:
    return (value or "").strip().lower() in ("1", "yes", "true", "on")


class ThinResearchError(Exception):
    """A research run came back with NO verified facts — an unusable brief.

    Raised by the research producer so the spine attributes the failure to the
    `research` stage and classifies it TRANSIENT (a plain Exception → re-runnable; see
    pipeline._run_stage). This is the genuinely-failed case (search unreachable /
    rate-limited), distinct from a weak-but-usable brief which still flows on (flagged).
    """


# ----------------------------------------------------------------------
# The shared fact-check engine seam (one place; tests monkeypatch this)
# ----------------------------------------------------------------------
def _factcheck_engine():
    """Load Sage's `factcheck` engine module (isolated, cached by the loader)."""
    import registry  # lazy: registry imports this module, so avoid a top-level cycle
    sage_dir = registry.get_entry("sage").project_dir
    return load_engine(sage_dir, "factcheck")


# ----------------------------------------------------------------------
# The shared research engine seam (one place; tests monkeypatch this)
# ----------------------------------------------------------------------
def _research_engine():
    """Load Sage's `researcher` engine module (isolated, cached by the loader)."""
    import registry  # lazy: registry imports this module, so avoid a top-level cycle
    sage_dir = registry.get_entry("sage").project_dir
    return load_engine(sage_dir, "researcher")


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
# Research: run Sage's engine and write the brief (the REAL pass-1 path)
# ----------------------------------------------------------------------
def run_research(pdir: pathlib.Path, topic: str, angle: str | None = None) -> dict:
    """Run Sage's research engine for `topic`/`angle`, stamp + write the brief.

    Mirrors `run_factcheck`: calls the sibling engine in-process (the whole pipeline
    runs OFF the SDK loop via asyncio.to_thread in tools.py, so the engine's own
    `asyncio.run` inside `llm.chat` is safe), stamps `schema_version` at the boundary,
    writes research_brief.json, and returns the stamped brief. The caller validates it
    against the frozen contract. Sage never imports atlas — Atlas owns the envelope.
    """
    from contracts import CONTRACT_VERSION
    pdir = pathlib.Path(pdir)
    pack, _json_path, _md_path = _research_engine().run(topic, angle, quiet=True)
    brief = {"schema_version": CONTRACT_VERSION, **pack}
    chat_state.atomic_write_json(pdir / "research_brief.json", brief)
    return brief


def _read_angle(pdir: pathlib.Path) -> str:
    """Optional research angle, read from the project's project.json (default '')."""
    proj = chat_state.load_json(pathlib.Path(pdir) / "project.json", {})
    return (proj.get("angle") or "").strip()


def _thin_brief_reasons(brief: dict) -> list[str]:
    """Visibility check: reasons a brief looks like placeholder / failed research.

    Empty list = looks real. This is NOT a hard block (that would diverge from the
    existing fact-check-gate convention); the producer records the warning LOUDLY so a
    thin run is visible instead of silently flowing downstream to be rubber-stamped.
    """
    reasons = []
    sources = brief.get("sources") or []
    if not (brief.get("verified_facts") or []):
        reasons.append("zero verified facts")
    if not sources:
        reasons.append("no sources")
    elif all("example.org" in (s.get("url") or "") for s in sources):
        reasons.append("all sources are example.org placeholders")
    return reasons


# ----------------------------------------------------------------------
# Pipeline producer (the real research stage worker; signature (pdir, topic))
# ----------------------------------------------------------------------
def produce_research(pdir: pathlib.Path, topic: str):
    """REAL pass-1 producer: Sage's engine researches `topic` into research_brief.json.

    The DEFAULT for the pipeline's `research` stage (replaces the offline stub, mirroring
    how `produce_factcheck` replaced the factcheck stub). Set ATLAS_RESEARCH_STUB truthy
    to force the offline placeholder instead (dev / no-network) — that path is logged
    loudly so a stub run is never silently mistaken for real research.
    """
    from adapters.stubs import Artifact
    from adapters.stubs import produce_research as _stub_research

    if _truthy(os.environ.get(RESEARCH_STUB_ENV, "")):
        log.warning("%s is set — using the OFFLINE placeholder research brief, NOT "
                    "Sage's real engine.", RESEARCH_STUB_ENV)
        art = _stub_research(pdir, topic)
        return Artifact(art.rel_path, art.contract, art.data,
                        f"⚠️ STUB research ({RESEARCH_STUB_ENV}) — {art.summary}")

    brief = run_research(pdir, topic, _read_angle(pdir))
    n_facts = len(brief.get("verified_facts") or [])
    n_src = len(brief.get("sources") or [])
    summary = f"{n_facts} verified facts, {n_src} sources"

    thin = _thin_brief_reasons(brief)
    if thin:
        joined = "; ".join(thin)
        log.warning("Research brief for %r looks thin: %s", topic, joined)
        # Persist a flag on the artifact (additive; the contract allows it) so the
        # thin run is inspectable downstream, even if we fail the stage below.
        brief["research_quality"] = {"thin": True, "reasons": thin}
        chat_state.atomic_write_json(pathlib.Path(pdir) / "research_brief.json", brief)
        # A brief with ZERO verified facts is not weak research — it's a FAILED run
        # (search unreachable / rate-limited): Marlow can't assert anything from it, so
        # letting it flow on only moves the failure downstream to `script` and mis-
        # attributes the blame. Fail HERE so the spine marks `research` failed
        # (transient → the belt auto-retries and the operator's RETRY re-runs research).
        if n_facts == 0:
            raise ThinResearchError(
                f"Research produced no verified facts ({joined}). Search sources may be "
                f"unreachable or rate-limited — this is a re-runnable research failure; "
                f"retry the research stage (and check network / SAGE_SEARCH).")
        # A weak-but-usable brief (has facts, but e.g. example.org sources) still flows
        # on, flagged loudly in the narrated summary.
        summary = f"⚠️ thin research ({joined}) — {summary}"

    return Artifact("research_brief.json", "research_brief", brief, summary)


class SageAdapter(Adapter):
    module_name = "researcher"   # topic-researcher/researcher.py (the research job)

    def run_job(self, job_name: str, progress, **params) -> dict:
        if job_name == "factcheck":
            return self._run_factcheck_job(progress, **params)

        if job_name != "research":
            return {"ok": False, "text": f"Sage has no job named {job_name!r}."}

        import projects
        from contracts import validate
        topic = (params.get("topic") or "").strip()
        angle = (params.get("angle") or "").strip() or None
        slug = (params.get("slug") or "").strip()
        who = self.entry.display

        pdir = self.resolve_pdir(slug)
        if pdir is None:
            msg = ("No project to research into. Start one with start_project, then pass "
                   "its slug so the research brief is saved into the video's workspace.")
            progress.fail(who, msg)
            return {"ok": False, "text": msg}

        # Sage validates topics in its engine; mirror it so we fail fast/cleanly.
        ok, reason = self.engine().validate_topic(topic)
        if not ok:
            progress.fail(who, reason)
            return {"ok": False, "text": reason}

        progress.start(self.entry.emoji, who, "researching", topic)
        # Write research_brief.json INTO the project workspace (run_research stamps +
        # persists it), so downstream jobs in this slug read it off disk.
        try:
            brief = run_research(pdir, topic, angle)
        except Exception as exc:  # ThinResearchError or a search failure, said plainly
            progress.fail(who, str(exc))
            return {"ok": False, "text": str(exc)}
        ok, errors = validate("research_brief", brief)
        if not ok:
            msg = f"Research brief failed contract validation: {'; '.join(errors)}"
            progress.fail(who, msg)
            return {"ok": False, "text": msg, "saved": str(pdir / "research_brief.json")}
        projects.mark_artifact(slug, "research_brief", pdir / "research_brief.json")
        progress.done(who, "finished the research pack")
        return {"ok": True, "text": _research_digest(brief), "topic": topic, "slug": slug,
                "saved": str(pdir / "research_brief.json")}

    # ---- pass-2: fact-check a drafted script against its brief (REAL engine) ----
    def _run_factcheck_job(self, progress, **params) -> dict:
        import projects
        from contracts import validate
        who = self.entry.display
        topic = (params.get("topic") or "").strip()
        slug = (params.get("slug") or "").strip()

        pdir = self.resolve_pdir(slug)
        if pdir is None or not (pdir / "script.json").exists() \
                or not (pdir / "research_brief.json").exists():
            msg = ("No project with a script + brief to fact-check. Run research + script "
                   "for this slug first.")
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg}

        if progress is not None:
            progress.start(self.entry.emoji, who, "fact-checking the script", topic or slug)
        report = run_factcheck(pdir)
        ok, errors = validate("factcheck_report", report)
        if not ok:
            msg = f"Fact-check report failed contract validation: {'; '.join(errors)}"
            if progress is not None:
                progress.fail(who, msg)
            return {"ok": False, "text": msg, "saved": str(pdir / "factcheck_report.json")}
        projects.mark_artifact(slug, "factcheck_report", pdir / "factcheck_report.json",
                               verdict=report.get("verdict"))
        if progress is not None:
            progress.done(who, "finished the fact-check")
        return {"ok": True, "text": _digest(report), "topic": topic, "slug": slug,
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
