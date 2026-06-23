"""The production spine — the deterministic pipeline the Showrunner runs.

The conversational orchestrator (orchestrator.py) is the room: it picks the brief,
announces decisions in Atlas's voice, and handles the gate conversations. But it is
NOT trusted to GUARANTEE stage order, contract validity, or that a failed gate halts
the line. That guarantee lives HERE, in a deterministic state machine that:

- runs the stages in one fixed order (the build-plan playbook),
- VALIDATES each artifact against its frozen contract before advancing,
- never advances a stage whose validation (or auto-gate) failed,
- enforces the two human gates as PAUSE-AND-RESUME via project.json (this function
  returns `blocked` + details and persists state; it does NOT block mid-tool), and
- is RESUMABLE: re-invoke with the same slug (and the gate approved) and it picks up
  exactly where it left off — already-done stages are skipped.

Transparency stays split as designed: the deterministic 🔎/📝/✅ status lines are
emitted from here as work happens (progress.py); the decisions/synthesis are Atlas's
streamed words in the meeting room.

Real engines, end-to-end: all 10 stages now bind real specialist producers (every
former stub slot was filled). The only stub that survives is `research`, and only as
an OPT-IN offline fallback behind the `ATLAS_RESEARCH_STUB` env flag (dev / no-network);
by default `research` runs Sage's real engine like every other stage. Each stage is one
producer, so swapping a specialist later = replacing ONE producer; nothing else changes.
"""
from __future__ import annotations

import contextlib
import pathlib
import time
import uuid
from dataclasses import dataclass
from typing import Callable

import chat_state
import contracts
import registry
from adapters import (art_director, asset_sourcer, audio, composition_engineer, sage,
                      scriptwriter)
from progress import Progress

HERE = pathlib.Path(__file__).parent
PROJECTS_DIR = HERE / "projects"


# ----------------------------------------------------------------------
# The playbook — one fixed order. (assets ∥ narration are independent; the spine
# runs them sequentially to stay rate-limit-safe, marked as a parallel group.)
# ----------------------------------------------------------------------
@dataclass
class Stage:
    key: str
    role: str                       # registry entry name -> emoji/display
    label: str                      # present-participle for the status line
    producer: Callable              # (pdir, topic) -> stubs.Artifact
    contract: str | None = None     # contract to validate the artifact (None = binary)
    autogate: bool = False          # composition auto-gate (lint+validate+inspect)
    group: str = ""                 # "" sequential; "parallel" = independent of sibling


STAGES: list[Stage] = [
    # REAL pass-1 (build step #1): Sage's engine researches the topic into the brief.
    # The offline placeholder stays in the tree (stubs.produce_research) reachable only
    # via ATLAS_RESEARCH_STUB=1 — the real engine is the default. (The other stages stay
    # offline stubs until their specialist lands.)
    Stage("research", "sage", "researching", sage.produce_research, "research_brief"),
    # REAL script stage: Marlow's engine drafts script.json from the brief. (The
    # other stages stay offline stubs until their specialist lands.)
    Stage("script", "scriptwriter", "drafting the script", scriptwriter.produce_script,
          "script"),
    # REAL pass-2 (build step #2): Sage's engine fact-checks the on-disk script vs
    # brief. The other stages stay offline stubs until their specialist lands.
    Stage("factcheck", "sage", "fact-checking the script", sage.produce_factcheck,
          "factcheck_report"),
    # REAL style + storyboard stages: Iris's engine reads the fact-checked script and
    # emits each spec (the other stages stay offline stubs until their specialist lands).
    Stage("style", "art_director", "setting the style", art_director.produce_style,
          "style_guide"),
    Stage("storyboard", "art_director", "building the storyboard",
          art_director.produce_storyboard, "storyboard"),
    # REAL assets stage: Magpie's engine sources + clears each shot's asset from a
    # PD/CC allowlist (the other stages stay offline stubs until their specialist lands).
    Stage("assets", "asset_sourcer", "sourcing assets", asset_sourcer.produce_assets,
          "asset_manifest", group="parallel"),
    # REAL narration stage: Cadence's engine voices the script per-scene (tts -> concat)
    # and writes the transcript (the downstream timing authority). Same slot/contract as
    # the step-#1 stub — only the producer changed.
    Stage("narration", "audio", "recording narration", audio.produce_narration,
          "narration_transcript", group="parallel"),
    # REAL compose + render stages: Mason's engine builds + auto-gates each scene
    # (composition_manifest.json, validated at the boundary) and assembles the final
    # video. (The other stages stay offline stubs until their specialist lands.)
    Stage("compose", "composition_engineer", "composing scenes",
          composition_engineer.produce_compose, "composition_manifest", autogate=True),
    # REAL mix stage: Cadence sources a cleared bed, places the one signature accent,
    # pre-mixes the documentary master.wav, and emits the manifest (the narration track's
    # uri points at the master, so the renderer muxes the full mix).
    Stage("audiomix", "audio", "mixing audio", audio.produce_audiomix, "audio_manifest"),
    Stage("render", "composition_engineer", "rendering the final cut",
          composition_engineer.produce_render, None),
]

# Human gates (pause-and-resume). factcheck = AFTER its stage; final_render = BEFORE.
GATE_FACTCHECK = "factcheck"
GATE_FINAL_RENDER = "final_render"

DEFAULT_GATES = {GATE_FACTCHECK: True, GATE_FINAL_RENDER: True}


# ----------------------------------------------------------------------
# project.json helpers (atomic via chat_state)
# ----------------------------------------------------------------------
def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in (text or "video").strip()]
    s = "".join(keep).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    return (s or "video")[:50]


def _save(project: dict, pdir: pathlib.Path) -> None:
    project["updated"] = time.time()
    chat_state.atomic_write_json(pdir / "project.json", project)


def _new_project(brief: str, topic: str, slug: str, cfg_gates: dict) -> dict:
    return {
        "schema_version": contracts.CONTRACT_VERSION,
        "project_id": uuid.uuid4().hex[:12],
        "slug": slug,
        "created": time.time(),
        "updated": time.time(),
        "title": "",
        "niche": "",
        "topic": topic,
        "brief": brief,
        "status": "created",
        "config": {"gates": dict(cfg_gates), "unattended": not any(cfg_gates.values())},
        "stages": {s.key: {"status": "pending", "artifact": None, "validated": False}
                   for s in STAGES},
        "gates": {GATE_FACTCHECK: {"status": "pending", "details": None},
                  GATE_FINAL_RENDER: {"status": "pending", "details": None}},
        "artifacts": {},
        "history": [],
    }


def _log(project: dict, stage: str, decision: str, why: str = "") -> None:
    project["history"].append({"ts": time.time(), "stage": stage,
                               "decision": decision, "why": why})


def _result(project: dict, pdir: pathlib.Path, **extra) -> dict:
    base = {"slug": project["slug"], "project_dir": str(pdir),
            "project_json": str(pdir / "project.json")}
    base.update(extra)
    return base


def _resolve_blocked_slug(root: pathlib.Path, approve: set) -> tuple:
    """Approve-only resume (slug omitted): find the project waiting at the named gate.

    Scoped to status == f"blocked_at_{gate}" for the gate(s) in `approve` — this is
    NOT a generic 'latest project' latch, and it is unreachable from a fresh start
    (which never passes `approve`). Returns (slug, None) on a unique match, else
    (None, error) — a clean message on zero or ambiguous (>1) candidates.
    """
    targets = {f"blocked_at_{g}" for g in approve}
    matches: list[tuple[float, str]] = []
    if root.exists():
        for d in sorted(root.iterdir()):
            proj = chat_state.load_json(d / "project.json", None)
            if isinstance(proj, dict) and proj.get("status") in targets:
                matches.append((proj.get("updated", 0) or 0, proj.get("slug") or d.name))
    gate_names = "/".join(sorted(approve))
    if not matches:
        return None, (f"No project is waiting at the {gate_names} gate to resume. "
                      "Start a NEW video with a brief instead.")
    if len(matches) > 1:
        names = ", ".join(s for _, s in sorted(matches, reverse=True))
        return None, (f"More than one project is waiting at the {gate_names} gate "
                      f"({names}). Resume with an explicit slug to disambiguate.")
    return matches[0][1], None


# ----------------------------------------------------------------------
# The runner
# ----------------------------------------------------------------------
@contextlib.contextmanager
def _station(station_locks: dict | None, key: str):
    """Hold a stage's single-occupancy 'station' lock while its producer runs.

    `station_locks` maps stage.key -> a lock (e.g. threading.Semaphore(1)); the assembly-
    line dispatcher passes it so only ONE video occupies a stage at a time (station=stage,
    spec §6.1/6.3). When None (CLI / tests / the orchestrator) this is a no-op and the
    spine behaves EXACTLY as before — the hook is opt-in."""
    lock = station_locks.get(key) if station_locks else None
    if lock is not None:
        lock.acquire()
    try:
        yield
    finally:
        if lock is not None:
            lock.release()


def _run_stage(stage: "Stage", st: dict, project: dict, pdir: pathlib.Path, topic: str,
               progress: Progress, who: str, emoji: str) -> dict | None:
    """Run one stage's producer + contract validation + auto-gate, mutating `project` in
    place. Returns a failed result dict to short-circuit produce(), or None on success.

    The failed dict carries `failure_kind`: 'transient' (a producer raised — often a
    network/runtime hiccup, so the dispatcher MAY retry) vs 'deterministic' (a contract or
    auto-gate failure — re-running yields the same result, so the dispatcher must NOT
    retry; spec §6.4). The caller holds the station lock around this call."""
    st["status"] = "running"
    _save(project, pdir)
    progress.emit(f"{emoji} {who} is {stage.label}…")
    try:
        art = stage.producer(pdir, topic)
    except Exception as exc:  # noqa: BLE001 — a stage failure halts cleanly
        st["status"] = "failed"
        st["note"] = str(exc)
        project["status"] = "failed"
        _save(project, pdir)
        progress.fail(who, str(exc))
        return _result(project, pdir, status="failed", stage=stage.key,
                       errors=[str(exc)], failure_kind="transient")

    # validate against the frozen contract
    if stage.contract is not None:
        ok, errors = contracts.validate(stage.contract, art.data)
        if not ok:
            st["status"] = "failed"
            st["validated"] = False
            st["note"] = "; ".join(errors)
            project["status"] = "failed"
            _save(project, pdir)
            progress.fail(who, f"{stage.contract} failed validation")
            return _result(project, pdir, status="failed", stage=stage.key,
                           errors=errors, failure_kind="deterministic")
        st["validated"] = True

    # composition auto-gate (lint + validate + inspect per scene)
    if stage.autogate and "auto-gate PASS" not in art.summary:
        st["status"] = "blocked"
        st["note"] = art.summary
        project["status"] = "failed"
        _save(project, pdir)
        progress.fail(who, "composition auto-gate failed")
        return _result(project, pdir, status="failed", stage=stage.key,
                       errors=[art.summary], failure_kind="deterministic")

    st["status"] = "done"
    st["artifact"] = art.rel_path
    st["updated"] = time.time()
    project["artifacts"][stage.key] = art.rel_path
    progress.done(who, art.summary)
    _save(project, pdir)
    return None


def create_project(brief: str | None = None, *, topic: str | None = None,
                   gates: dict | None = None, unattended: bool = False,
                   root: pathlib.Path | None = None) -> dict:
    """Mint a new project on disk in the 'queued' state and return its summary
    {slug, project_dir}. The assembly-line dispatcher calls this to get a slug + a belt
    card IMMEDIATELY, then runs the project with produce(slug=...). Splitting create from
    run is what lets the UI show the queued card before any stage runs. (produce(brief=)
    still creates-and-runs in one call for the CLI/orchestrator — unchanged.)"""
    root = pathlib.Path(root) if root else PROJECTS_DIR
    b = (brief or topic or "").strip()
    the_topic = (topic or b).strip()
    cfg_gates = dict(DEFAULT_GATES)
    if gates:
        cfg_gates.update(gates)
    if unattended:
        cfg_gates = {k: False for k in cfg_gates}
    slug = f"{_slug(the_topic)}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    pdir = root / slug
    pdir.mkdir(parents=True, exist_ok=True)
    project = _new_project(b, the_topic, slug, cfg_gates)
    project["status"] = "queued"  # explicit belt state: minted, not yet on a station
    _save(project, pdir)
    return {"slug": slug, "project_dir": str(pdir)}


def produce(brief: str | None = None, *, slug: str | None = None,
            approve: list[str] | None = None, gates: dict | None = None,
            unattended: bool = False, topic: str | None = None,
            root: pathlib.Path | None = None,
            progress: Progress | None = None,
            station_locks: dict | None = None,
            should_cancel: Callable[[], bool] | None = None) -> dict:
    """Run (or resume) the production pipeline for one video.

    NEW run: pass `brief`. RESUME: pass `slug` (and `approve=[gate]` to clear a gate).
    Returns a dict with `status` in {"done","blocked","failed"} plus context. On a
    human gate it persists `blocked_at_<gate>` to project.json and RETURNS — it never
    blocks mid-tool. `unattended=True` (or gates={...:False}) runs straight through.
    """
    progress = progress or Progress()
    root = pathlib.Path(root) if root else PROJECTS_DIR
    approve = set(approve or [])

    # --- (a) approve-only resume: resolve the blocked project by its gate ----
    # When the CEO signs off but Atlas no longer has the slug, a resume carries only
    # `approve`. Resolve it to the project waiting at THAT gate. Scoped to `approve`
    # (a fresh start never passes it) and to a `brief`-less call, so this can never
    # latch onto a fresh video.
    if not slug and approve and not (brief or "").strip():
        slug, err = _resolve_blocked_slug(root, approve)
        if err:
            return {"status": "failed", "stage": None, "errors": [err], "slug": None}

    # --- load (resume) or create the project --------------------------------
    if slug:
        pdir = root / slug
        project = chat_state.load_json(pdir / "project.json", None)
        if not isinstance(project, dict):
            return {"status": "failed", "stage": None,
                    "errors": [f"No resumable project at {pdir / 'project.json'}."],
                    "slug": slug, "project_dir": str(pdir)}
    else:
        b = (brief or topic or "").strip()
        the_topic = (topic or b).strip()
        cfg_gates = dict(DEFAULT_GATES)
        if gates:
            cfg_gates.update(gates)
        if unattended:
            cfg_gates = {k: False for k in cfg_gates}
        # uuid suffix guards against two fresh calls in the SAME second for the same
        # topic colliding on one slug (and silently overwriting each other's project).
        slug = f"{_slug(the_topic)}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        pdir = root / slug
        pdir.mkdir(parents=True, exist_ok=True)
        project = _new_project(b, the_topic, slug, cfg_gates)
        _save(project, pdir)

    # gate config + approvals carried in on this invocation
    cfg_gates = project.get("config", {}).get("gates", DEFAULT_GATES)
    for g in approve:
        if g in project["gates"]:
            project["gates"][g]["status"] = "approved"
            _log(project, g, "gate approved by human")
    topic = project.get("topic") or project.get("brief") or ""

    # --- (b) fact-check re-earn on resume -----------------------------------
    # A resume that clears the fact-check gate must RE-RUN Sage against the (revised)
    # script on disk — never trust the existing report (a hand-driven job can rewrite
    # it out of band). Resetting the stage to pending forces the producer to regenerate
    # factcheck_report.json; the gate below then re-evaluates the FRESH verdict. A
    # `block` STILL cannot be approved away — it re-blocks on the regenerated report.
    # This is a re-earn path, not an override.
    if GATE_FACTCHECK in approve \
            and project.get("status") == f"blocked_at_{GATE_FACTCHECK}" \
            and project["stages"][GATE_FACTCHECK].get("status") == "done":
        project["stages"][GATE_FACTCHECK] = {"status": "pending", "artifact": None,
                                             "validated": False}
        _log(project, GATE_FACTCHECK, "re-running fact-check on resume",
             "the revised script must re-earn the gate")

    project["status"] = "running"
    _save(project, pdir)

    # --- the spine ----------------------------------------------------------
    # Gates are checked as CHECKPOINTS that read project.json / artifacts on disk, so
    # they fire correctly on a resume too (a stage already 'done' still hits its gate).
    for stage in STAGES:
        # cooperative cancel: checked between stations so a cancelled video stops cleanly
        # (it holds no lock here) and is removable from the belt (spec §6.5 / E6). No-op
        # when should_cancel is None (CLI / orchestrator / tests).
        if should_cancel is not None and should_cancel():
            project["status"] = "cancelled"
            _log(project, stage.key, "cancelled by operator")
            _save(project, pdir)
            progress.emit("✖ Cancelled by operator.")
            return _result(project, pdir, status="cancelled", stage=stage.key)

        st = project["stages"][stage.key]

        # --- human gate BEFORE the final render (checkpoint) ----------------
        if stage.key == "render":
            blocked = _final_render_gate(project, pdir, cfg_gates, progress)
            if blocked is not None:
                return blocked

        # --- run the stage (skip if already produced + validated) -----------
        entry = registry.get_entry(stage.role)
        emoji = entry.emoji if entry else "•"
        who = entry.display if entry else stage.role

        if st.get("status") != "done":
            # Single-occupancy station lock held ONLY while the stage runs; a video that
            # parks at a gate / fails / is cancelled releases it on return → frees the
            # station for the next video (spec §6.1/6.3). No-op when station_locks is None.
            with _station(station_locks, stage.key):
                failed = _run_stage(stage, st, project, pdir, topic, progress, who, emoji)
            if failed is not None:
                return failed

        # --- human gate AFTER fact-check (checkpoint) -----------------------
        if stage.key == "factcheck":
            blocked = _factcheck_gate(project, pdir, cfg_gates, progress)
            if blocked is not None:
                return blocked

    # --- done ---------------------------------------------------------------
    project["status"] = "done"
    video = project["artifacts"].get("render", "video.mp4")
    _log(project, "render", "video produced", video)
    _save(project, pdir)
    progress.emit(f"🎬 Done — {pdir / video}")
    return _result(project, pdir, status="done", video=str(pdir / video))


# ----------------------------------------------------------------------
# Gate checkpoints — evaluated from disk so they fire on a resume, not just on the
# turn that produced the artifact. Each returns a `blocked` result dict to short-
# circuit produce(), or None to let the line continue.
# ----------------------------------------------------------------------
def _factcheck_gate(project: dict, pdir: pathlib.Path, cfg_gates: dict,
                    progress: Progress) -> dict | None:
    report = chat_state.load_json(pdir / "factcheck_report.json", {})
    verdict = report.get("verdict")
    summary = report.get("summary", {})
    details = {"verdict": verdict, "summary": summary,
               "flagged": _flagged_claims(report)}

    # A `block` verdict can NEVER be approved away — it routes back upstream and
    # keeps blocking (every invocation) until the script/research is fixed + re-checked.
    if verdict == "block":
        project["gates"][GATE_FACTCHECK] = {"status": "rejected", "details": details}
        project["status"] = f"blocked_at_{GATE_FACTCHECK}"
        _log(project, GATE_FACTCHECK, "BLOCKED — unverified claims",
             "route back to Scriptwriter/Researcher")
        _save(project, pdir)
        progress.emit("🛑 Fact-check BLOCKED — unverified claims. Holding; route back "
                      "to the script/research before art.")
        return _result(project, pdir, status="blocked", gate=GATE_FACTCHECK,
                       reason="Fact-check found unverified claims — cannot proceed. "
                              "Route back to Scriptwriter/Researcher.",
                       details=details)

    # Clean verdict but the gate is on and not yet signed off → pause for the human.
    if cfg_gates.get(GATE_FACTCHECK, True) and \
            project["gates"][GATE_FACTCHECK]["status"] != "approved":
        project["gates"][GATE_FACTCHECK] = {"status": "blocked", "details": details}
        project["status"] = f"blocked_at_{GATE_FACTCHECK}"
        _log(project, GATE_FACTCHECK, "paused for human sign-off")
        _save(project, pdir)
        progress.emit("⏸️  Fact-check clear, but the gate is on — awaiting your "
                      "sign-off before we spend on art.")
        return _result(project, pdir, status="blocked", gate=GATE_FACTCHECK,
                       reason="Fact-check passed; awaiting human sign-off.",
                       details=details)
    return None


def _final_render_gate(project: dict, pdir: pathlib.Path, cfg_gates: dict,
                       progress: Progress) -> dict | None:
    if not cfg_gates.get(GATE_FINAL_RENDER, True):
        return None
    if project["gates"][GATE_FINAL_RENDER]["status"] == "approved":
        return None
    details = _render_plan(pdir)
    project["gates"][GATE_FINAL_RENDER] = {"status": "blocked", "details": details}
    project["status"] = f"blocked_at_{GATE_FINAL_RENDER}"
    _log(project, GATE_FINAL_RENDER, "paused for human sign-off")
    _save(project, pdir)
    progress.emit("⏸️  Holding before the final render — awaiting your sign-off on the "
                  "draft + render plan.")
    return _result(project, pdir, status="blocked", gate=GATE_FINAL_RENDER,
                   reason="Awaiting human sign-off before the final render.",
                   details=details)


# ----------------------------------------------------------------------
# Gate detail builders (what the human sees at each pause)
# ----------------------------------------------------------------------
def _flagged_claims(report: dict | None) -> list[dict]:
    claims = (report or {}).get("claims", [])
    return [c for c in claims if c.get("status") in ("flagged", "unverifiable")]


def _render_plan(pdir: pathlib.Path) -> dict:
    script = chat_state.load_json(pdir / "script.json", {})
    mix = chat_state.load_json(pdir / "audio" / "audio_manifest.json", {})
    return {
        "working_title": script.get("working_title", ""),
        "scenes": script.get("total_scenes", 0),
        "est_runtime_sec": script.get("est_runtime_sec", 0),
        "audio_duration_sec": mix.get("total_duration_sec", 0),
        "plan": "Render each scene HTML, concat with FFmpeg, mux narration + bed.",
    }
