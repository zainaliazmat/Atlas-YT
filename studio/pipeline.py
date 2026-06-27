"""studio.pipeline — the linear v2 production flow (front wired to real engines).

One deterministic spine. The front three stages REUSE the proven sibling engines
(via studio.engines — never forked):

    research ─▶ script ─▶ factcheck (GATE) ─▶ vo ─▶ compose ─▶ draft ─▶ review ─▶ final (GATE)

  research   — Sage's researcher.run -> research_brief.json.
  script     — Marlow's script_engine.write_script(brief) -> script.json
               (one-point-per-scene: narration + on_screen_text + claims).
  factcheck  — Sage's factcheck.factcheck(script, brief) behind a HARD gate:
               a ``block`` verdict can NEVER be approved away — it routes back to
               fix the script and re-checks (same rule as atlas/pipeline.py).
  vo/compose/draft/review/final — later phases (TODO).

Projects are HyperFrames-native: ``studio/projects/<slug>/`` mirrors
``reference/dark-truth-social/`` exactly (meta.json, hyperframes.json,
package.json pinned to hyperframes@0.7.10, assets/, compositions/, and later
index.html), plus a ``state.json`` holding resumable stage + gate state.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import config, engines
from . import HYPERFRAMES_VERSION

SCHEMA_VERSION = "studio-1"

# Ordered stage ids. factcheck runs AFTER a script exists; gates are marked.
STAGES: tuple[str, ...] = (
    "research",
    "script",
    "factcheck",    # GATE (hard, un-approvable on "block")
    "storyboard",   # Iris tags each scene with an archetype from the closed LAYOUTS vocab
    "vo",
    "compose",
    "draft",
    "review",
    "final",        # GATE (human pause; approvable)
)
GATES: frozenset[str] = frozenset({"factcheck", "final"})

GATE_FACTCHECK = "factcheck"
STATUS_BLOCKED = "blocked_at_factcheck"


class PipelineError(Exception):
    """A stage produced an unusable / malformed artifact."""


# --- small fs helpers --------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _stamp(doc: dict) -> dict:
    """Stamp the studio envelope field (studio owns the contract boundary)."""
    if doc.get("schema_version"):
        return doc
    return {"schema_version": SCHEMA_VERSION, **doc}


# --- project scaffolding (HyperFrames-native, mirrors the reference) ---------
def project_dir(slug: str) -> Path:
    return config.PROJECTS_DIR / slug


def scaffold_project(slug: str) -> Path:
    """Create studio/projects/<slug>/ mirroring reference/dark-truth-social/.

    Writes meta.json, hyperframes.json, package.json (pinned to
    hyperframes@<HYPERFRAMES_VERSION>, scripts dev/check/render), and the
    assets/ + compositions/ dirs. Idempotent — existing files are left intact so
    a resume never clobbers prior work. index.html is authored later (compose).
    """
    pdir = project_dir(slug)
    (pdir / "assets").mkdir(parents=True, exist_ok=True)
    (pdir / "compositions").mkdir(parents=True, exist_ok=True)

    meta = pdir / "meta.json"
    if not meta.exists():
        _write_json(meta, {"id": slug, "name": slug, "createdAt": _now()})

    hf = pdir / "hyperframes.json"
    if not hf.exists():
        _write_json(hf, {
            "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
            "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
            "paths": {
                "blocks": "compositions",
                "components": "compositions/components",
                "assets": "assets",
            },
        })

    pkg = pdir / "package.json"
    if not pkg.exists():
        v = HYPERFRAMES_VERSION
        _write_json(pkg, {
            "name": slug,
            "private": True,
            "type": "module",
            "scripts": {
                "dev": f"npx --yes hyperframes@{v} preview",
                "check": f"npx --yes hyperframes@{v} lint && npx --yes hyperframes@{v} validate && npx --yes hyperframes@{v} inspect",
                "render": f"npx --yes hyperframes@{v} render",
                "publish": f"npx --yes hyperframes@{v} publish",
            },
        })
    return pdir


# --- resumable state ---------------------------------------------------------
def _state_path(pdir: Path) -> Path:
    return pdir / "state.json"


def _load_state(pdir: Path, slug: str, brief: dict) -> dict:
    state = _read_json(_state_path(pdir))
    if state is None:
        state = {
            "slug": slug,
            "brief": brief,
            "status": "in_progress",
            "stages": {s: {"status": "pending"} for s in STAGES},
            "gates": {g: {"status": "pending"} for g in GATES},
            "artifacts": {},
            "log": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
    return state


def _save_state(pdir: Path, state: dict) -> None:
    state["updated_at"] = _now()
    _write_json(_state_path(pdir), state)


def _stage_status(state: dict, stage: str) -> str:
    return state["stages"].get(stage, {}).get("status", "pending")


def _set_stage(state: dict, stage: str, status: str) -> None:
    state["stages"].setdefault(stage, {})["status"] = status


def _log(state: dict, stage: str, msg: str, detail: str = "") -> None:
    state["log"].append({"at": _now(), "stage": stage, "msg": msg, "detail": detail})


# --- artifact validation (light; studio owns the boundary) -------------------
def _validate_research(brief: dict) -> None:
    if not isinstance(brief, dict) or not brief.get("topic"):
        raise PipelineError("research_brief: missing topic")
    for k in ("verified_facts", "sources"):
        if k not in brief:
            raise PipelineError(f"research_brief: missing {k!r}")


def _validate_script(script: dict) -> None:
    scenes = script.get("scenes") if isinstance(script, dict) else None
    if not isinstance(scenes, list) or not scenes:
        raise PipelineError("script: no scenes")
    for sc in scenes:
        for k in ("scene_no", "narration", "on_screen_text", "claims"):
            if k not in sc:
                raise PipelineError(f"script scene missing {k!r}")


# --- the hard fact-check gate ------------------------------------------------
def _factcheck_gate(state: dict, pdir: Path, factcheck_fn) -> dict:
    """Run the pass-2 fact-check and enforce the hard rule.

    A ``block`` verdict is NEVER approvable — it routes back to the script and
    re-checks. ``pass`` is EARNED (not human-granted). Persists the report and
    the gate state. Returns ``{"blocked": bool, "report": dict}``.
    """
    script = _read_json(pdir / "script.json", {})
    brief = _read_json(pdir / "research_brief.json", {})
    report = _stamp(factcheck_fn(script, brief))
    _write_json(pdir / "factcheck_report.json", report)
    state["artifacts"]["factcheck_report"] = "factcheck_report.json"

    verdict = report.get("verdict")
    summary = report.get("summary", {})
    if verdict == "block":
        flagged = [c.get("claim_id") for c in report.get("claims", [])
                   if c.get("status") in ("flagged", "unverifiable")]
        state["gates"][GATE_FACTCHECK] = {
            "status": "rejected", "verdict": "block", "approvable": False,
            "details": f"flagged claim_ids: {flagged}",
        }
        _set_stage(state, GATE_FACTCHECK, "blocked")
        state["status"] = STATUS_BLOCKED
        _log(state, GATE_FACTCHECK,
             "BLOCKED — unverified claims route back to the script",
             "a block can never be approved away; fix the script and re-check")
        return {"blocked": True, "report": report}

    state["gates"][GATE_FACTCHECK] = {
        "status": "passed", "verdict": "pass", "approvable": False,
    }
    _set_stage(state, GATE_FACTCHECK, "done")
    state["status"] = "passed_factcheck"
    _log(state, GATE_FACTCHECK, "fact-check passed",
         f"verified {summary.get('verified', 0)}, flagged {summary.get('flagged', 0)}, "
         f"unverifiable {summary.get('unverifiable', 0)}")
    return {"blocked": False, "report": report}


GATE_FINAL = "final"
STATUS_AWAITING_FINAL = "awaiting_final_gate"
STATUS_COMPLETE = "complete"
STATUS_RENDER_FAILED = "render_failed"
STATUS_BLOCKED_BY_GATE = "blocked_at_gate"


# --- determinism enforcement (a hard rule, throughout) -----------------------
def _enforce_determinism(pdir: Path) -> None:
    """The composed ``index.html`` MUST be byte-stable + seek-safe before we spend a
    render on it: NO Math.random/Date.now/new Date/fetch/XMLHttpRequest, and the master
    timeline MUST be registered on window.__timelines. Reuses
    ``studio.review.critics.technical_scan`` (the same deterministic grep the technical
    critic uses) so the rule is enforced in ONE place. Raises PipelineError on violation
    — a determinism break in our own composer is a bug, not something to render around."""
    index = pdir / "index.html"
    if not index.is_file():
        raise PipelineError("compose produced no index.html")
    from .review.critics import technical_scan
    scan = technical_scan(index.read_text(encoding="utf-8"))
    if scan["nondeterminism"]:
        raise PipelineError(
            f"determinism violation in index.html: {scan['nondeterminism']} "
            "(no Math.random/Date.now/fetch allowed — the render must be byte-stable)")
    if not scan["registers_timeline"]:
        raise PipelineError(
            "index.html does not register a window.__timelines master timeline "
            "(required for a driven, seek-safe render)")


# --- default back-half seams (real toolchain; tests inject fakes) ------------
def _default_render(pdir: Path, *, final: bool = False) -> dict:
    """Render the project's composition via composition-engineer/hf_tools (REUSE). The
    draft render produces renders/draft.mp4; the final render copies the result to
    ``video.mp4`` (the deliverable). Best-effort dict, never raises."""
    import shutil
    import sys
    ce = str((config.REPO_ROOT / "composition-engineer").resolve())
    if ce not in sys.path:
        sys.path.insert(0, ce)
    try:
        import hf_tools  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"ok": None, "skipped": True, "error": f"hf_tools unavailable: {exc}",
                "video": None}
    try:
        res = hf_tools.run_render(Path(pdir))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "video": None}
    ok = bool(res.get("ok"))
    out = res.get("output")
    if ok and final and out:
        dest = Path(pdir) / "video.mp4"
        try:
            shutil.copyfile(out, dest)
            out = str(dest)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"final copy failed: {exc}", "video": None}
    return {"ok": ok, "video": out, "detail": res}


def _estimate_render_sec(pdir: Path) -> float | None:
    """Estimated render cost for the budget check — the composition's runtime (the same
    'est_runtime_sec' proxy atlas/dispatcher uses). Read from vo.grid.json; None if it
    can't be sized (callers then never auto-approve, matching the dispatcher rule)."""
    grid = _read_json(pdir / "vo.grid.json", {}) or {}
    total = grid.get("total_duration_sec")
    if total is None:
        total = (grid.get("grid") or {}).get("total")
    return float(total) if isinstance(total, (int, float)) else None


def _review_unresolved(review_report: dict) -> list[str]:
    """Blocker/Major fix IDs that the review did NOT auto-apply — the final gate's
    'is it good enough to ship' signal. Empty = the vision review is clean."""
    syn = (review_report or {}).get("synthesis") or {}
    ap = (review_report or {}).get("apply") or {}
    applied = {a.get("id") for a in (ap.get("applied") or [])}
    return [f["id"] for f in syn.get("fixes", [])
            if f.get("severity") in ("Blocker", "Major") and f.get("id") not in applied]


# --- the driver --------------------------------------------------------------
def produce(
    brief: dict,
    slug: str,
    *,
    approve: set[str] | None = None,
    gates: bool = True,
    unattended: bool = False,
    stop_after: str | None = None,
    run_config: dict | None = None,
    research_fn=None,
    script_fn=None,
    factcheck_fn=None,
    storyboard_fn=None,
    vo_fn=None,
    compose_fn=None,
    render_fn=None,
    review_fn=None,
    motion_fn=None,
    gate_fn=None,
    chat_fn=None,
) -> dict:
    """Run the full v2 spine, resumable end to end:

        research → script → factcheck★GATE → vo → compose → draft → review → final★GATE → video.mp4

    Resumable: persists state.json after each step and skips done stages. Both human
    gates pause-and-resume via state.json — factcheck is HARD (a ``block`` can never be
    approved away; it re-runs on a fixed script), final is approvable. ``--unattended``
    (``unattended=True``) auto-approves the FINAL gate ONLY when the motion check AND the
    vision review both pass and the estimated render cost is under the budget ceiling.
    Determinism is enforced on the composed index.html before any render.

    Engine/toolchain seams default to the REAL siblings; tests inject fakes via the
    ``*_fn`` params (or monkeypatch ``studio.engines`` / ``studio.review``). Returns the
    project state dict, paused with a status of ``blocked_at_factcheck`` /
    ``awaiting_final_gate`` when it stops at a gate.
    """
    approve = set(approve or [])
    run_config = run_config or config.resolve_run_config()
    research_fn = research_fn or (lambda topic, angle: engines.research(topic, angle))
    script_fn = script_fn or (lambda b: engines.write_script(b, chat_fn=chat_fn))
    factcheck_fn = factcheck_fn or (lambda script, b: engines.factcheck(script, b, chat_fn=chat_fn))
    render_fn = render_fn or _default_render

    if not brief.get("topic"):
        raise PipelineError("brief must carry a 'topic'")

    pdir = scaffold_project(slug)
    state = _load_state(pdir, slug, brief)
    # Record whether human gates are enforced + the resolved run config (for resume/audit).
    state["gates_enabled"] = bool(gates)
    state["unattended"] = bool(unattended)
    state["run_config"] = run_config

    # Re-earn: a block is NEVER approved away. Approving 'factcheck' on resume only
    # RESETS the gate so the check RE-RUNS (on a now-fixed script). A fresh block
    # still blocks; without approval the run stays paused awaiting the fix.
    if state.get("status") == STATUS_BLOCKED:
        if GATE_FACTCHECK not in approve:
            return state
        _set_stage(state, GATE_FACTCHECK, "pending")
        state["gates"][GATE_FACTCHECK] = {"status": "pending"}
        state["status"] = "in_progress"
        _log(state, GATE_FACTCHECK, "re-running fact-check on resume",
             "block cannot be approved away — re-checking the (fixed) script")
        _save_state(pdir, state)

    # 1. research
    if _stage_status(state, "research") != "done":
        pack = research_fn(brief["topic"], brief.get("angle"))
        doc = _stamp(pack)
        _validate_research(doc)
        _write_json(pdir / "research_brief.json", doc)
        state["artifacts"]["research_brief"] = "research_brief.json"
        _set_stage(state, "research", "done")
        _log(state, "research", "research brief produced",
             f"{len(doc.get('verified_facts', []))} verified facts")
        _save_state(pdir, state)

    # 2. script
    if _stage_status(state, "script") != "done":
        research_brief = _read_json(pdir / "research_brief.json", {})
        script = script_fn(research_brief)
        doc = _stamp(script)
        _validate_script(doc)
        _write_json(pdir / "script.json", doc)
        state["artifacts"]["script"] = "script.json"
        _set_stage(state, "script", "done")
        _log(state, "script", "script drafted", f"{len(doc.get('scenes', []))} scenes")
        _save_state(pdir, state)

    # 3. factcheck GATE
    if _stage_status(state, GATE_FACTCHECK) != "done":
        result = _factcheck_gate(state, pdir, factcheck_fn)
        _save_state(pdir, state)
        if result["blocked"]:
            return state  # paused — routes back to the script

    # Optional early stop (staged runs / front-half callers): halt once the named stage
    # has completed. The fact-check stop reports the earned "passed_factcheck" status.
    if stop_after == GATE_FACTCHECK:
        state["status"] = "passed_factcheck"
        _save_state(pdir, state)
        return state

    pack_id = run_config["pack_id"]

    # 3b. storyboard — Iris tags each scene with an archetype (compose reads the tag)
    if _stage_status(state, "storyboard") != "done":
        from . import storyboard as sb_mod
        script = _read_json(pdir / "script.json", {})
        run_sb = storyboard_fn or (lambda s, d: sb_mod.tag_archetypes(s, d))
        board = run_sb(script, pdir)
        _write_json(pdir / "storyboard.json", board)
        state["artifacts"]["storyboard"] = "storyboard.json"
        _set_stage(state, "storyboard", "done")
        _log(state, "storyboard", "scene archetypes tagged",
             f"{len(board.get('scenes', []))} scenes")
        _save_state(pdir, state)
    if stop_after == "storyboard":
        state["status"] = "stopped_after_storyboard"
        _save_state(pdir, state)
        return state

    # 4. vo (re-timer) — record VO, conform the NS/ND grid, write vo.grid.json
    if _stage_status(state, "vo") != "done":
        from . import vo as vo_mod
        from . import packs as packs_mod
        script = _read_json(pdir / "script.json", {})
        try:
            pack = packs_mod.load_pack(pack_id)
        except Exception as exc:  # noqa: BLE001
            raise PipelineError(f"vo: cannot load pack {pack_id!r}: {exc}")
        run_vo = vo_fn or (lambda s, d, **kw: vo_mod.produce_vo(s, d, **kw))
        manifest = run_vo(script, pdir, pack=pack, voice=run_config["voice"])
        total = (manifest or {}).get("total_duration_sec")
        state["artifacts"]["vo_grid"] = "vo.grid.json"
        state["stages"].setdefault("vo", {})["total_duration_sec"] = total
        _set_stage(state, "vo", "done")
        _log(state, "vo", "VO recorded + grid conformed",
             f"total {total}s" if total is not None else "")
        _save_state(pdir, state)
    if stop_after == "vo":
        state["status"] = "stopped_after_vo"
        _save_state(pdir, state)
        return state

    # 5. compose (pack + library) — author index.html, then ENFORCE determinism
    if _stage_status(state, "compose") != "done":
        run_compose = compose_fn or (lambda slug, pack_id: _default_compose(slug, pack_id))
        run_compose(slug, pack_id)
        _enforce_determinism(pdir)
        state["artifacts"]["index_html"] = "index.html"
        _set_stage(state, "compose", "done")
        _log(state, "compose", "composition authored + determinism enforced", pack_id)
        _save_state(pdir, state)
    if stop_after == "compose":
        state["status"] = "stopped_after_compose"
        _save_state(pdir, state)
        return state

    # 6. draft render — the artifact the vision review looks at
    if _stage_status(state, "draft") != "done":
        res = render_fn(pdir, final=False)
        if not res.get("ok"):
            state["stages"].setdefault("draft", {})["error"] = res.get("error") or "render failed"
            _set_stage(state, "draft", "error")
            state["status"] = STATUS_RENDER_FAILED
            _log(state, "draft", "draft render failed", str(res.get("error")))
            _save_state(pdir, state)
            return state
        state["artifacts"]["draft_render"] = res.get("video")
        _set_stage(state, "draft", "done")
        _log(state, "draft", "draft rendered", str(res.get("video")))
        _save_state(pdir, state)
    if stop_after == "draft":
        state["status"] = "stopped_after_draft"
        _save_state(pdir, state)
        return state

    # 7. vision review — auto-apply Blockers+Majors (may re-render affected scenes)
    if _stage_status(state, "review") != "done":
        _save_state(pdir, state)  # flush BEFORE review writes reviews[] into state.json
        run_review = review_fn or (lambda slug, mode: _default_review(slug, mode))
        report = run_review(slug, "auto")
        state = _load_state(pdir, slug, brief)  # reload to keep reviews[] the review wrote
        unresolved = _review_unresolved(report)
        counts = ((report or {}).get("synthesis") or {}).get("counts", {})
        st = state["stages"].setdefault("review", {})
        st["counts"] = counts
        st["unresolved"] = unresolved
        _set_stage(state, "review", "done")
        _log(state, "review", "vision review complete",
             f"counts={counts} unresolved={unresolved}")
        _save_state(pdir, state)
    if stop_after == "review":
        state["status"] = "stopped_after_review"
        _save_state(pdir, state)
        return state

    # 8. final GATE → video.mp4
    if _stage_status(state, GATE_FINAL) != "done":
        run_motion = motion_fn or (lambda slug: _default_motion(slug))
        motion = run_motion(slug)
        motion_ok = not bool((motion or {}).get("any_flag"))

        # The quality gate scorecard — the publish blocker. A BLOCKED verdict can NEVER be
        # approved away (same hard semantics as the factcheck gate): a sub-bar render is not
        # something a human or --unattended can sign off.
        run_gate = gate_fn or (lambda slug: _default_gate(slug))
        scorecard = run_gate(slug)
        gate_blocked = scorecard.get("verdict") == "BLOCKED"

        review_unresolved = state["stages"].get("review", {}).get("unresolved", []) or []
        review_ok = not review_unresolved
        est = _estimate_render_sec(pdir)
        budget = float(run_config.get("render_budget_sec", config.DEFAULT_RENDER_BUDGET_SEC))
        under_budget = isinstance(est, (int, float)) and est <= budget

        details = {"motion_ok": motion_ok, "review_ok": review_ok,
                   "review_unresolved": review_unresolved,
                   "est_runtime_sec": est, "render_budget_sec": budget,
                   "under_budget": under_budget,
                   "verdict": scorecard.get("verdict"), "overall": scorecard.get("overall"),
                   "reasons": scorecard.get("reasons", []), "scorecard": scorecard}

        if gate_blocked:
            state["gates"][GATE_FINAL] = {"status": "blocked", "approvable": False,
                                          "details": details,
                                          "reason": "quality gate BLOCKED — " + "; ".join(scorecard.get("reasons", [])[:4])}
            _set_stage(state, GATE_FINAL, "blocked")
            state["status"] = STATUS_BLOCKED_BY_GATE
            _log(state, GATE_FINAL, "BLOCKED by quality gate", "; ".join(scorecard.get("reasons", [])))
            _save_state(pdir, state)
            return state

        human_approved = GATE_FINAL in approve
        # --unattended auto-approves ONLY when both quality gates pass AND under budget.
        auto_ok = unattended and motion_ok and review_ok and under_budget
        # legacy --no-gates bypasses the human pause entirely.
        bypass = not gates

        if not (human_approved or auto_ok or bypass):
            reason = ("awaiting human approval" if not unattended else
                      f"unattended hold: motion_ok={motion_ok} review_ok={review_ok} "
                      f"under_budget={under_budget} (est {est}s vs {budget}s)")
            state["gates"][GATE_FINAL] = {"status": "awaiting_approval", "approvable": True,
                                          "details": details, "reason": reason}
            _set_stage(state, GATE_FINAL, "awaiting_approval")
            state["status"] = STATUS_AWAITING_FINAL
            _log(state, GATE_FINAL, "paused at final gate", reason)
            _save_state(pdir, state)
            return state

        approver = ("human" if human_approved else "unattended-auto" if auto_ok else "no-gates")
        res = render_fn(pdir, final=True)
        if not res.get("ok"):
            state["stages"].setdefault("final", {})["error"] = res.get("error") or "render failed"
            _set_stage(state, "final", "error")
            state["status"] = STATUS_RENDER_FAILED
            _log(state, GATE_FINAL, "final render failed", str(res.get("error")))
            _save_state(pdir, state)
            return state
        state["gates"][GATE_FINAL] = {"status": "passed", "approvable": True,
                                      "approved_by": approver, "details": details}
        state["artifacts"]["video"] = res.get("video")
        _set_stage(state, GATE_FINAL, "done")
        state["status"] = STATUS_COMPLETE
        _log(state, GATE_FINAL, f"final gate cleared ({approver}) → video.mp4",
             str(res.get("video")))
        _save_state(pdir, state)

    _save_state(pdir, state)
    return state


# --- back-half default seams that need lazy sibling imports ------------------
def _default_compose(slug: str, pack_id: str):
    from . import compose as compose_mod
    return compose_mod.compose(slug, pack_id=pack_id)


def _default_review(slug: str, mode: str) -> dict:
    from . import review as review_mod
    return review_mod.review(slug, mode=mode)


def _default_motion(slug: str) -> dict:
    from .review import motion_check
    try:
        return motion_check.motion_check(slug)
    except Exception as exc:  # noqa: BLE001
        return {"any_flag": True, "error": str(exc)}


def _default_gate(slug: str) -> dict:
    from . import gate
    try:
        return gate.score(slug=slug)
    except Exception as exc:  # noqa: BLE001
        # a gate that cannot run must NOT silently pass — block with the reason.
        return {"verdict": "BLOCKED", "reasons": [f"gate error: {exc}"],
                "overall": None, "dimensions": [], "compliance": []}
