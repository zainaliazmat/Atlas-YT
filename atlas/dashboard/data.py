"""Read-only data-access: real system state → the six Control Room screens.

Every value here comes from a module the spine already owns — nothing is
reimplemented:
  * registry.REGISTRY / get_entry  → the fleet, agent identities, jobs
  * project.json (chat_state.load_json, tolerant) → status, stages, gates, history
  * project_view                  → gate previews + latest-blocked detection
  * contracts.validate            → artifact validity badges
  * pipeline.STAGES (lazy)        → the canonical stage→role/label/order mapping
  * eval scorecard + rubric       → the Quality screen (degrades if absent)

Pure and side-effect-free: it READS the artifacts the pipeline already wrote and
returns plain dicts. The single write path (gate approval) lives in app.py and
delegates to session.approve_gate — never here.

Every function tolerates missing/partial/corrupt inputs (chat_state.load_json
never raises) and returns a "pending"/empty shape rather than crashing.
"""
from __future__ import annotations

import functools
import json
import pathlib
import time

import contracts
import registry
import supervisor

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_PROJECTS_DIR = HERE.parent / "projects"

# Per-agent LLM switch (env var, default claude model). Mirrors each sibling
# llm.py EXACTLY so the surfaced provider is the real one, without importing the
# heavy engine. (scout + sage share SAGE_LLM by design.)
_PROVIDER_ENV: dict[str, tuple[str, str]] = {
    "scout": ("SAGE_LLM", "claude-opus-4-8"),
    "sage": ("SAGE_LLM", "claude-opus-4-8"),
    "scriptwriter": ("MARLOW_LLM", "claude-opus-4-8"),
    "art_director": ("IRIS_LLM", "claude-opus-4-8"),
    "asset_sourcer": ("MAGPIE_LLM", "claude-sonnet-4-6"),
    "audio": ("AUDIO_LLM", "claude-sonnet-4-6"),
    "composition_engineer": ("MASON_LLM", "claude-sonnet-4-6"),
    "reference_analyst": ("VERA_LLM", "claude-sonnet-4-6"),
    "editorial_coach": ("QUILL_LLM", "claude-sonnet-4-6"),
    "production_coach": ("FLUX_LLM", "claude-sonnet-4-6"),
}
_PROVIDER_MODELS = {"gemini": "gemini-2.5-flash", "deepseek": "deepseek-v4-flash"}
_PROVIDER_LABEL = {"claude": "claude", "gemini": "gemini", "deepseek": "deepseek"}


# ----------------------------------------------------------------------
# Canonical stage metadata — reused from pipeline.STAGES (lazy import to avoid
# pulling the specialist adapters at module import, mirroring session.py).
# ----------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def _stage_meta() -> list[dict]:
    """[{key, role, label, group, autogate, contract}] in spine order, from pipeline."""
    import pipeline  # lazy, like session._pipeline_produce
    return [{"key": s.key, "role": s.role, "label": s.label, "group": s.group,
             "autogate": s.autogate, "contract": s.contract} for s in pipeline.STAGES]


@functools.lru_cache(maxsize=1)
def _stage_role() -> dict[str, str]:
    return {s["key"]: s["role"] for s in _stage_meta()}


def _gate_keys() -> tuple[str, str]:
    import pipeline
    return pipeline.GATE_FACTCHECK, pipeline.GATE_FINAL_RENDER


# The upstream artifacts each stage's producer READS (domain truth, PROJECT_CONTEXT §6
# Reads→Writes). Used by the Stage Inspector to show "inputs read" honestly — every path
# is existence-checked at read time, never assumed.
STAGE_INPUTS: dict[str, list[str]] = {
    "research": [],
    "script": ["research_brief.json"],
    "factcheck": ["script.json", "research_brief.json"],
    "style": ["script.json"],
    "storyboard": ["script.json", "style_guide.json"],
    "assets": ["storyboard.json", "style_guide.json"],
    "narration": ["script.json"],
    "compose": ["storyboard.json", "style_guide.json", "asset_manifest.json",
                "narration.transcript.json"],
    "audiomix": ["narration.transcript.json", "audio/audio_manifest.json"],
    "render": ["composition_manifest.json", "audio/master.wav"],
}


# ----------------------------------------------------------------------
# Small tolerant loaders
# ----------------------------------------------------------------------
def read_json(path, default=None):
    """Read JSON, tolerating absence/corruption — and NEVER mutating disk.

    Deliberately NOT chat_state.load_json: that engine loader RENAMES a corrupt
    file aside (`<name>.corrupt-<ts>`) as a side effect of reading it, which would
    let a read-only dashboard GET mutate the real projects tree. This dashboard is
    read-mostly, so it parses in place and returns `default` on any problem — the
    file is left exactly as found.
    """
    path = pathlib.Path(path)
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(errors="replace"))
    except (json.JSONDecodeError, ValueError, OSError):
        return default


def _load(pdir: pathlib.Path, rel: str, default=None):
    return read_json(pdir / rel, default)


def _rel_time(ts: float | None) -> str:
    """Compact 'updated' label: '2m', '3h', '4d', or '' when unknown."""
    if not ts:
        return ""
    delta = max(0, time.time() - ts)
    if delta < 90:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86400:
        return f"{int(delta // 3600)}h"
    return f"{int(delta // 86400)}d"


def _entry_brief(name: str) -> dict:
    e = registry.get_entry(name)
    if e is None:
        return {"name": name, "display": name, "emoji": "•", "role": "", "blurb": ""}
    return {"name": e.name, "display": e.display, "emoji": e.emoji,
            "role": e.role, "blurb": e.blurb}


def provider_for(name: str) -> dict:
    """The agent's REAL effective brain: {provider, model} read from its env switch."""
    var, claude_model = _PROVIDER_ENV.get(name, ("ATLAS_LLM", "claude-sonnet-4-6"))
    from dashboard.security import env_provider
    prov = env_provider(var)
    label = _PROVIDER_LABEL.get(prov, prov)
    model = _PROVIDER_MODELS.get(prov, claude_model)
    return {"provider": label, "model": model, "switch": var}


# ======================================================================
# Project-level reads
# ======================================================================
def _scenes_runtime(pdir: pathlib.Path) -> tuple[int, float]:
    """(scene_count, runtime_sec) from the best available artifact, tolerant."""
    script = _load(pdir, "script.json", {}) or {}
    scenes = script.get("total_scenes") or len(script.get("scenes", []) or [])
    mix = _load(pdir, "audio/audio_manifest.json", {}) or {}
    runtime = mix.get("total_duration_sec") or script.get("est_runtime_sec") or 0
    return int(scenes or 0), float(runtime or 0)


def _scorecard(pdir: pathlib.Path) -> dict | None:
    sc = _load(pdir, "eval_scorecard.json", None)
    return sc if isinstance(sc, dict) else None


def _project_summary(pdir: pathlib.Path, proj: dict) -> dict:
    status = proj.get("status") or "unknown"
    scenes, runtime = _scenes_runtime(pdir)
    sc = _scorecard(pdir)
    quality = None
    if sc is not None:
        quality = {"overall": sc.get("overall"),
                   "quality_score": sc.get("quality_score")}
    gate = None
    if status.startswith("blocked_at_"):
        gate = status[len("blocked_at_"):]
    return {
        "slug": proj.get("slug") or pdir.name,
        "title": proj.get("title") or "",
        "topic": proj.get("topic") or proj.get("brief") or "",
        "label": proj.get("title") or proj.get("topic") or proj.get("slug") or pdir.name,
        "status": status,
        "gate": gate,
        "scenes": scenes,
        "runtime_sec": round(runtime, 1) if runtime else 0,
        "quality": quality,
        "updated": proj.get("updated", 0) or 0,
        "updated_rel": _rel_time(proj.get("updated")),
        "created": proj.get("created", 0) or 0,
    }


def iter_projects(projects_dir: pathlib.Path):
    """Yield (pdir, project_dict) for every readable project, tolerant of junk."""
    if not projects_dir.exists():
        return
    for d in sorted(projects_dir.iterdir()):
        if not d.is_dir():
            continue
        proj = read_json(d / "project.json", None)
        if isinstance(proj, dict):
            yield d, proj


def list_projects(projects_dir: pathlib.Path) -> dict:
    """The Projects screen: every video + rollup counts."""
    rows = [_project_summary(d, p) for d, p in iter_projects(projects_dir)]
    rows.sort(key=lambda r: r["updated"], reverse=True)
    counts = {"total": len(rows), "needs_you": 0, "in_production": 0,
              "blocked": 0, "done": 0, "queued": 0, "failed": 0}
    qsum, qn = 0.0, 0
    for r in rows:
        st = r["status"]
        if st == "done":
            counts["done"] += 1
        elif st == "running":
            counts["in_production"] += 1
        elif st == "created":
            counts["queued"] += 1
        elif st == "failed":
            counts["failed"] += 1
        if st.startswith("blocked_at_"):
            counts["needs_you"] += 1
            # a hard fact-check block is un-approvable: it's "blocked", not "needs you"
            if _is_hard_block(projects_dir / r["slug"]):
                counts["blocked"] += 1
                counts["needs_you"] -= 1
        q = (r.get("quality") or {}).get("quality_score")
        if isinstance(q, (int, float)):
            qsum += q
            qn += 1
    counts["avg_quality"] = round(qsum / qn, 2) if qn else None
    return {"projects": rows, "counts": counts}


def _is_hard_block(pdir: pathlib.Path) -> bool:
    """True iff this project is blocked by a fact-check `block` verdict — the
    un-approvable case (routes back; the spine rejects an approve)."""
    proj = _load(pdir, "project.json", {}) or {}
    if proj.get("status") != "blocked_at_factcheck":
        return False
    report = _load(pdir, "factcheck_report.json", {}) or {}
    return report.get("verdict") == "block"


# ----------------------------------------------------------------------
# The live belt (assembly line) — rebuilt from disk every call (spec §6.2)
# ----------------------------------------------------------------------
def _belt_state(proj: dict) -> str:
    """Normalise project.json `status` to the ONE belt vocabulary (spec §6.1/§5):
    queued | running | blocked | failed | cancelled | done."""
    s = proj.get("status") or "queued"
    if s.startswith("blocked_at_"):
        return "blocked"
    if s == "created":
        return "queued"
    if s in ("queued", "running", "failed", "cancelled", "done", "interrupted"):
        return s
    return s


def _current_station(proj: dict, metas: list[dict]) -> str | None:
    """The station this video occupies / is about to enter: the running stage if any,
    else the first not-yet-done stage, else the last (all done)."""
    stages = proj.get("stages", {}) or {}
    for m in metas:
        if (stages.get(m["key"], {}) or {}).get("status") == "running":
            return m["key"]
    for m in metas:
        if (stages.get(m["key"], {}) or {}).get("status", "pending") != "done":
            return m["key"]
    return metas[-1]["key"] if metas else None


def belt(projects_dir: pathlib.Path) -> dict:
    """The live assembly line: every video, its belt-state, its current station, and a
    compact per-stage status map (for the spine row), plus the 10 stations (for the
    occupancy strip) and which station each running video holds. All from disk."""
    metas = _stage_meta()
    stations = [{"key": m["key"], "label": m["key"], "group": m["group"],
                 "autogate": m["autogate"], "agent": _entry_brief(m["role"])}
                for m in metas]
    videos = []
    for d, proj in iter_projects(projects_dir):
        summ = _project_summary(d, proj)
        pstages = proj.get("stages", {}) or {}
        sup_log = (proj.get("supervisor", {}) or {}).get("log") or []
        atlas_activity = None
        if sup_log:
            last = sup_log[-1]
            atlas_activity = {"text": supervisor.humanize_atlas_activity(last),
                              "ts": last.get("ts", 0)}
        videos.append({
            "slug": summ["slug"], "label": summ["label"], "topic": summ["topic"],
            "belt_state": _belt_state(proj), "status": summ["status"],
            "gate": summ["gate"], "station": _current_station(proj, metas),
            "stages": {m["key"]: (pstages.get(m["key"], {}) or {}).get("status", "pending")
                       for m in metas},
            "updated": summ["updated"], "updated_rel": summ["updated_rel"],
            "hard_block": _is_hard_block(d) if summ["status"] == "blocked_at_factcheck"
            else False,
            "atlas_activity": atlas_activity,
        })
    videos.sort(key=lambda v: v["updated"], reverse=True)
    occupancy = {v["station"]: {"slug": v["slug"], "label": v["label"]}
                 for v in videos if v["belt_state"] == "running" and v["station"]}
    counts = {st: sum(1 for v in videos if v["belt_state"] == st) for st in
              ("queued", "running", "blocked", "failed", "cancelled", "done",
               "interrupted")}
    return {"stations": stations, "videos": videos, "occupancy": occupancy,
            "counts": counts}


# ----------------------------------------------------------------------
# Pipeline detail (one project)
# ----------------------------------------------------------------------
def project_detail(projects_dir: pathlib.Path, slug: str) -> dict | None:
    pdir = projects_dir / slug
    proj = read_json(pdir / "project.json", None)
    if not isinstance(proj, dict):
        return None
    summary = _project_summary(pdir, proj)
    stages_meta = _stage_meta()
    g_fact, g_final = _gate_keys()
    pstages = proj.get("stages", {}) or {}
    pgates = proj.get("gates", {}) or {}

    # one ladder row per stage, enriched with role identity + artifact summary line
    ladder = []
    for sm in stages_meta:
        key = sm["key"]
        st = pstages.get(key, {}) or {}
        ent = _entry_brief(sm["role"])
        ladder.append({
            "key": key, "label": sm["label"], "group": sm["group"],
            "autogate": sm["autogate"], "contract": sm["contract"],
            "agent": ent,
            "status": st.get("status", "pending"),
            "validated": bool(st.get("validated")),
            "artifact": st.get("artifact"),
            "note": st.get("note"),
            "updated_rel": _rel_time(st.get("updated")),
            "detail": _stage_detail_line(pdir, key, st),
        })

    gates = {
        g_fact: _gate_state(pgates.get(g_fact, {}), pdir, g_fact),
        g_final: _gate_state(pgates.get(g_final, {}), pdir, g_final),
    }

    # contract validity badges (validate the artifacts that exist on disk)
    contracts_status = _contracts_status(pdir, stages_meta, pstages)

    return {
        "summary": summary,
        "config": proj.get("config", {}),
        "stages": ladder,
        "gates": gates,
        "contracts": contracts_status,
        "artifacts": _artifact_files(pdir),
        "quality": _scorecard(pdir),
        "history": list(reversed(proj.get("history", []) or []))[:40],
        "has_video": (pdir / "video.mp4").exists(),
    }


def _gate_state(g: dict, pdir: pathlib.Path, gate: str) -> dict:
    return {"gate": gate, "status": (g or {}).get("status", "pending"),
            "details": (g or {}).get("details"),
            "hard_block": gate == "factcheck" and _is_hard_block(pdir)}


def _stage_detail_line(pdir: pathlib.Path, key: str, st: dict) -> str:
    """A short, real one-liner about a done stage's artifact (for the ladder sub-row)."""
    if st.get("status") != "done":
        return ""
    try:
        if key == "research":
            b = _load(pdir, "research_brief.json", {}) or {}
            facts = b.get("facts") or b.get("verified_facts") or []
            srcs = b.get("sources") or []
            return f"{len(facts)} facts · {len(srcs)} sources"
        if key == "script":
            s = _load(pdir, "script.json", {}) or {}
            return f"{s.get('total_scenes', len(s.get('scenes', [])))} scenes"
        if key == "factcheck":
            r = _load(pdir, "factcheck_report.json", {}) or {}
            sm = r.get("summary", {})
            return (f"verdict {r.get('verdict','?')} · {sm.get('verified',0)} verified · "
                    f"{sm.get('flagged',0)+sm.get('unverifiable',0)} flagged")
        if key == "assets":
            a = _load(pdir, "asset_manifest.json", {}) or {}
            assets = a.get("assets", [])
            cleared = sum(1 for x in assets if x.get("status") == "cleared")
            return f"{len(assets)} assets · {cleared} cleared"
        if key in ("narration", "audiomix"):
            m = _load(pdir, "audio/audio_manifest.json", {}) or {}
            lufs = m.get("integrated_lufs") or m.get("target_lufs")
            dur = m.get("total_duration_sec")
            bits = []
            if dur:
                bits.append(f"{round(dur)}s")
            if lufs:
                bits.append(f"{lufs} LUFS")
            return " · ".join(bits)
        if key == "compose":
            c = _load(pdir, "composition_manifest.json", {}) or {}
            scenes = c.get("scenes") or c.get("scene_builds") or []
            return f"{len(scenes)} scene HTML"
    except Exception:  # noqa: BLE001 — detail line is best-effort, never fatal
        return ""
    return ""


def _contracts_status(pdir, stages_meta, pstages) -> list[dict]:
    """Validate each artifact that exists against its frozen contract (real badge)."""
    seen = {}
    for sm in stages_meta:
        cname = sm["contract"]
        if not cname:
            continue
        st = pstages.get(sm["key"], {}) or {}
        rel = st.get("artifact")
        if not rel:
            continue
        obj = read_json(pdir / rel, None)
        if obj is None:
            seen[cname] = {"contract": cname, "status": "missing"}
            continue
        ok, errors = contracts.validate(cname, obj)
        seen[cname] = {"contract": cname, "status": "valid" if ok else "invalid",
                       "errors": errors[:3] if not ok else []}
    return list(seen.values())


def _artifact_files(pdir: pathlib.Path) -> list[dict]:
    """Listable artifacts that actually exist on disk (name + size only)."""
    from dashboard.security import ARTIFACT_FILES
    out = []
    for rel in ARTIFACT_FILES:
        p = pdir / rel
        if p.exists() and p.is_file():
            out.append({"name": rel, "size": p.stat().st_size})
    for extra in ("video.mp4", "master.wav", "audio/master.wav"):
        p = pdir / extra
        if p.exists() and p.is_file():
            out.append({"name": extra, "size": p.stat().st_size})
    return out


# ----------------------------------------------------------------------
# Stage / Agent Inspector (depth 2) — one stage of one project, in full
# ----------------------------------------------------------------------
def _classify_failure(st: dict, contract: str | None) -> dict | None:
    """{kind, reason} for a failed/blocked stage, or None. Mirrors the spine's own split
    (pipeline._run_stage): a contract-validation or composition auto-gate failure is
    DETERMINISTIC (re-running repeats it → the UI must NOT offer RETRY); a producer that
    raised is TRANSIENT (a hiccup → RETRY is honest). Spec §6.4."""
    status = (st or {}).get("status")
    if status not in ("failed", "blocked"):
        return None
    note = (st or {}).get("note") or ""
    low = note.lower()
    validated = bool((st or {}).get("validated"))
    deterministic = (
        status == "blocked"                                  # composition auto-gate block
        or (contract is not None and not validated and any(
            kw in low for kw in ("valid", "required", "contract", "auto-gate",
                                 "schema", "property")))
    )
    return {"kind": "deterministic" if deterministic else "transient",
            "reason": note or "stage failed"}


def stage_detail(projects_dir: pathlib.Path, slug: str, key: str) -> dict | None:
    """One stage of one project, in full: the owning agent + its effective brain, the
    upstream artifacts it reads, its output artifact with a field-level contract verdict,
    and — when parked — the transient/deterministic failure with the honest action set
    (RETRY only for transient; CANCEL while in-flight/parked). All read-only, all tolerant."""
    pdir = projects_dir / slug
    proj = read_json(pdir / "project.json", None)
    if not isinstance(proj, dict):
        return None
    sm = next((m for m in _stage_meta() if m["key"] == key), None)
    if sm is None:
        return None
    st = (proj.get("stages", {}) or {}).get(key, {}) or {}
    ent = _entry_brief(sm["role"])
    prov = provider_for(sm["role"])
    label = proj.get("title") or proj.get("topic") or proj.get("slug") or slug
    belt_state = _belt_state(proj)

    inputs = [{"name": rel, "exists": (pdir / rel).is_file()}
              for rel in STAGE_INPUTS.get(key, [])]

    output = None
    artifact = st.get("artifact")
    if artifact:
        present = (pdir / artifact).is_file()
        out = {"artifact": artifact, "exists": present, "contract": sm["contract"],
               "valid": None, "errors": []}
        if sm["contract"] and present:
            obj = read_json(pdir / artifact, None)
            if obj is None:
                out["valid"], out["errors"] = False, ["present but not parseable JSON"]
            else:
                ok, errors = contracts.validate(sm["contract"], obj)
                out["valid"] = ok
                out["errors"] = errors[:6] if not ok else []
        output = out

    failure = _classify_failure(st, sm["contract"])
    can_retry = bool(failure and failure["kind"] == "transient")
    can_cancel = belt_state in ("running", "queued", "blocked", "failed")

    return {
        "slug": proj.get("slug") or slug, "label": label, "key": key,
        "stage_label": sm["label"], "group": sm["group"], "autogate": sm["autogate"],
        "agent": ent,
        "provider": {"provider": prov["provider"], "model": prov["model"],
                     "switch": prov["switch"]},
        "status": st.get("status", "pending"), "validated": bool(st.get("validated")),
        "updated_rel": _rel_time(st.get("updated")),
        "contract": sm["contract"], "inputs": inputs, "output": output,
        "failure": failure, "note": st.get("note"),
        "detail": _stage_detail_line(pdir, key, st),
        "belt_state": belt_state,
        "actions": {"can_retry": can_retry, "can_cancel": can_cancel},
    }


# ======================================================================
# Fleet + agent detail (generalized to ALL registry entries)
# ======================================================================
def _agent_jobs_across_projects(projects_dir: pathlib.Path) -> dict[str, list[dict]]:
    """Real recent jobs per agent, derived from the pipeline stages each ran on disk.

    For every project, each stage whose role==agent and which actually ran becomes
    one job row {project label, stage, status, updated}. This is fully read-only and
    engine-free — the truth of 'what has this agent done' is the artifacts on disk.
    """
    role_of = _stage_role()
    jobs: dict[str, list[dict]] = {}
    for d, proj in iter_projects(projects_dir):
        label = proj.get("title") or proj.get("topic") or proj.get("slug") or d.name
        for key, st in (proj.get("stages", {}) or {}).items():
            role = role_of.get(key)
            if not role or st.get("status") in (None, "pending"):
                continue
            jobs.setdefault(role, []).append({
                "project": label[:80], "slug": proj.get("slug") or d.name,
                "stage": key, "status": st.get("status"),
                "updated": st.get("updated", 0) or 0,
                "updated_rel": _rel_time(st.get("updated")),
            })
    for role in jobs:
        jobs[role].sort(key=lambda r: r["updated"], reverse=True)
    return jobs


def _live_stage_roles(projects_dir: pathlib.Path) -> dict[str, dict]:
    """role -> {slug, label, stage}: who is on what RIGHT NOW. An agent appears iff an
    in-flight (`running`) project has that agent's stage `running` — so the fleet can name
    the exact video + station, not just "busy"."""
    role_of = _stage_role()
    live: dict[str, dict] = {}
    for d, proj in iter_projects(projects_dir):
        if proj.get("status") != "running":
            continue
        label = proj.get("title") or proj.get("topic") or proj.get("slug") or d.name
        for key, st in (proj.get("stages", {}) or {}).items():
            if (st or {}).get("status") == "running":
                role = role_of.get(key)
                if role:
                    live[role] = {"slug": proj.get("slug") or d.name,
                                  "label": label[:80], "stage": key}
    return live


def fleet(projects_dir: pathlib.Path) -> dict:
    jobs = _agent_jobs_across_projects(projects_dir)
    live = _live_stage_roles(projects_dir)
    blocked = _find_latest_blocked(projects_dir)
    agents = []
    for e in registry.REGISTRY:
        recent = jobs.get(e.name, [])
        prov = provider_for(e.name)
        status = "idle"
        detail = ""
        cur = live.get(e.name)              # {slug, label, stage} or None
        if cur:
            status = "running"
            detail = f"on {cur['stage']} · {cur['label'][:38]}"
        elif recent:
            detail = f"last: {recent[0]['stage']} · {recent[0]['updated_rel']}"
        # the agent who owns the gate the studio is paused at "holds for you"
        if blocked and _gate_owner(blocked.get("gate")) == e.name:
            status, detail = "holding", f"awaiting you · {blocked.get('gate')} gate"
        agents.append({
            **_entry_brief(e.name),
            "provider": prov["provider"], "model": prov["model"],
            "jobs_run": len(recent), "status": status, "detail": detail,
            "current": cur,
            "last_rel": recent[0]["updated_rel"] if recent else "",
        })
    summary = {
        "total": len(agents),
        "working": sum(1 for a in agents if a["status"] == "running"),
        "idle": sum(1 for a in agents if a["status"] == "idle"),
        "holding": sum(1 for a in agents if a["status"] == "holding"),
        "non_claude": sum(1 for a in agents if a["provider"] != "claude"),
    }
    return {"agents": agents, "summary": summary}


def _gate_owner(gate: str | None) -> str | None:
    """Which agent the studio is effectively waiting on at a gate (for the 'holding'
    badge). Fact-check → Sage; final render → Mason."""
    return {"factcheck": "sage", "final_render": "composition_engineer"}.get(gate or "")


def _read_soul(project_dir: str) -> dict:
    """The agent's soul bundle: SOUL.md voice line + the file inventory. Read-only,
    bounded; never serialize an absolute path (just the file names)."""
    base = pathlib.Path(project_dir) / "soul"
    out = {"files": [], "voice": "", "identity": ""}
    if not base.exists():
        return out
    for f in sorted(base.rglob("*.md")):
        out["files"].append(f.relative_to(base).as_posix())
    soul_md = base / "SOUL.md"
    if soul_md.exists():
        text = soul_md.read_text(errors="replace")
        # first non-heading, non-empty paragraph = a representative identity line
        for line in text.splitlines():
            s = line.strip().lstrip("#").strip()
            if s and not s.startswith(("-", "*", ">")) and len(s) > 24:
                out["identity"] = s[:400]
                break
    style_md = base / "STYLE.md"
    if style_md.exists():
        for line in style_md.read_text(errors="replace").splitlines():
            s = line.strip().lstrip("#").strip()
            if s and len(s) > 16:
                out["voice"] = s[:240]
                break
    return out


def agent_detail(projects_dir: pathlib.Path, name: str) -> dict | None:
    e = registry.get_entry(name)
    if e is None:
        return None
    jobs = _agent_jobs_across_projects(projects_dir).get(e.name, [])
    live = _live_stage_roles(projects_dir)
    prov = provider_for(e.name)
    return {
        **_entry_brief(e.name),
        "provider": prov["provider"], "model": prov["model"], "switch": prov["switch"],
        "persona": e.persona,
        "is_stage": e.name in set(_stage_role().values()),
        "status": "running" if live.get(e.name) else "idle",
        "current": live.get(e.name),
        "jobs": [{"name": j.name, "tool": j.tool, "description": j.description,
                  "params": {k: getattr(v, "__name__", str(v)) for k, v in j.params.items()},
                  "timeout": j.timeout} for j in e.jobs],
        "soul": _read_soul(e.project_dir),
        "recent_jobs": jobs[:12],
        "jobs_run": len(jobs),
        "owned_bands": _owned_bands(e.name),
    }


# ======================================================================
# Quality / scorecards (consumes the eval layer if present; degrades if not)
# ======================================================================
@functools.lru_cache(maxsize=1)
def _rubric_safe() -> dict | None:
    try:
        import rubric
        r = rubric.load_rubric()
        # deep-copy out of the frozen MappingProxy into plain JSON-able dicts
        import json
        return json.loads(json.dumps(_unfreeze(r)))
    except Exception:  # noqa: BLE001 — rubric is optional context for the screen
        return None


def _unfreeze(obj):
    from types import MappingProxyType
    if isinstance(obj, MappingProxyType):
        return {k: _unfreeze(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_unfreeze(v) for v in obj]
    return obj


def _owned_bands(agent_name: str) -> list[str]:
    """Rubric band ids whose owning stage is run by this agent (best-effort, read-only)."""
    rb = _rubric_safe()
    if not rb:
        return []
    role_of = _stage_role()
    mine = {k for k, v in role_of.items() if v == agent_name}
    out = []
    for band_id in (rb.get("bands", {}) or {}):
        stage = band_id.split(":", 1)[0]
        if stage in mine:
            out.append(band_id)
    return out


def quality(projects_dir: pathlib.Path, slug: str | None = None) -> dict:
    """Quality screen. Picks a scorecard (named slug, else most-recent project that
    has one). Always returns the rubric standard + a degraded flag when no eval data
    exists, so the screen renders a clear 'no scorecard yet' state."""
    rb = _rubric_safe()
    # gather every project that has a scorecard, for trend + latest
    scored = []
    for d, proj in iter_projects(projects_dir):
        sc = _scorecard(d)
        if sc is not None:
            scored.append((proj.get("updated", 0) or 0,
                           proj.get("slug") or d.name,
                           proj.get("title") or proj.get("topic") or d.name, sc))
    scored.sort(key=lambda r: r[0])

    latest = None
    if slug:
        pdir = projects_dir / slug
        sc = _scorecard(pdir)
        if sc is not None:
            latest = {"slug": slug, "scorecard": sc}
    if latest is None and scored:
        _, sl, _, sc = scored[-1]
        latest = {"slug": sl, "scorecard": sc}

    trend = [{"slug": sl, "label": (lbl or sl)[:18],
              "quality_score": sc.get("quality_score"),
              "overall": sc.get("overall")} for _, sl, lbl, sc in scored]

    return {
        "available": latest is not None,
        "rubric": rb,
        "rubric_version": (rb or {}).get("rubric_version"),
        "latest": latest,
        "trend": trend,
        "scored_count": len(scored),
        "loop_ledger": _loop_ledger(),
    }


def _loop_ledger() -> dict:
    """The self-improvement change ledger, if the eval tracking store exists.
    Degrades to empty when the (separate-track) eval loop hasn't run."""
    runs_path = HERE.parent / "eval" / "runs" / "eval_runs.jsonl"
    if not runs_path.exists():
        return {"available": False, "rows": []}
    rows = []
    try:
        seen_changes = {}
        for line in runs_path.read_text(errors="replace").splitlines()[-400:]:
            line = line.strip()
            if not line:
                continue
            import json
            r = json.loads(line)
            cid = r.get("change_id")
            if cid:
                seen_changes.setdefault(cid, {"change_id": cid, "rows": 0,
                                              "ts": r.get("ts", 0)})
                seen_changes[cid]["rows"] += 1
                seen_changes[cid]["ts"] = max(seen_changes[cid]["ts"], r.get("ts", 0))
        rows = sorted(seen_changes.values(), key=lambda x: x["ts"], reverse=True)[:8]
    except Exception:  # noqa: BLE001
        return {"available": False, "rows": []}
    return {"available": bool(rows), "rows": rows}


# ======================================================================
# Overview (mission control) + gate detail
# ======================================================================
def overview(projects_dir: pathlib.Path) -> dict:
    proj_list = list_projects(projects_dir)
    rows = proj_list["projects"]
    counts = proj_list["counts"]
    fleet_data = fleet(projects_dir)
    blocked = _find_latest_blocked(projects_dir)

    # the most relevant active project: a blocked one, else a running one, else newest
    active = None
    if blocked:
        active = next((r for r in rows if r["slug"] == blocked["slug"]), None)
    if active is None:
        active = next((r for r in rows if r["status"] == "running"), None)
    if active is None and rows:
        active = rows[0]

    active_pipeline = None
    if active:
        active_pipeline = _mini_pipeline(projects_dir, active["slug"])

    q = quality(projects_dir)
    activity = _activity_log(projects_dir)

    gate_card = None
    if blocked:
        gate_card = gate_detail(projects_dir, blocked["slug"])

    return {
        "kpis": {
            "in_production": counts["in_production"],
            "awaiting_you": counts["needs_you"] + counts["blocked"],
            "fleet_total": fleet_data["summary"]["total"],
            "fleet_idle": fleet_data["summary"]["idle"],
            "latest_quality": (q["latest"]["scorecard"].get("quality_score")
                               if q.get("available") else None),
        },
        "active": active,
        "active_pipeline": active_pipeline,
        "fleet": fleet_data["agents"],
        "gate": gate_card,
        "quality": q["latest"],
        "activity": activity,
        "counts": counts,
    }


def _mini_pipeline(projects_dir, slug) -> dict | None:
    det = project_detail(projects_dir, slug)
    if det is None:
        return None
    nodes = [{"key": s["key"], "role": s["role"] if "role" in s else s["agent"]["name"],
              "emoji": s["agent"]["emoji"], "status": s["status"], "group": s["group"]}
              for s in det["stages"]]
    return {"slug": slug, "title": det["summary"]["label"],
            "status": det["summary"]["status"], "nodes": nodes, "gates": det["gates"]}


def _activity_log(projects_dir: pathlib.Path, limit: int = 12) -> list[dict]:
    """Merged, newest-first history across all projects (real ts + decision lines)."""
    events = []
    for _, proj in iter_projects(projects_dir):
        label = proj.get("title") or proj.get("topic") or proj.get("slug") or "?"
        for h in proj.get("history", []) or []:
            events.append({"ts": h.get("ts", 0) or 0, "stage": h.get("stage"),
                           "decision": h.get("decision"), "why": h.get("why", ""),
                           "project": label[:60], "rel": _rel_time(h.get("ts"))})
    events.sort(key=lambda e: e["ts"], reverse=True)
    return events[:limit]


# ----------------------------------------------------------------------
# Local, NON-MUTATING mirrors of project_view's read-only previews. We mirror its
# logic (not its code) because project_view reads through chat_state.load_json,
# which renames a corrupt file aside — a disk mutation a read-only dashboard must
# never cause. Same shapes, but every read goes through read_json (parse-in-place).
# ----------------------------------------------------------------------
def _find_latest_blocked(projects_dir: pathlib.Path) -> dict | None:
    best = None
    for d, proj in iter_projects(projects_dir):
        status = proj.get("status") or ""
        if not status.startswith("blocked_at_"):
            continue
        updated = proj.get("updated", 0) or 0
        if best is None or updated > best[0]:
            best = (updated, d, proj, status)
    if best is None:
        return None
    _, d, proj, status = best
    gate = status[len("blocked_at_"):]
    details = (proj.get("gates", {}).get(gate, {}) or {}).get("details")
    label = proj.get("title") or proj.get("topic") or proj.get("slug") or d.name
    return {"slug": proj.get("slug") or d.name, "gate": gate, "status": status,
            "details": details, "label": label, "updated": best[0]}


def _gate1_preview(pdir: pathlib.Path) -> dict:
    report = _load(pdir, "factcheck_report.json", {}) or {}
    script = _load(pdir, "script.json", {}) or {}
    flagged = [c for c in report.get("claims", [])
               if c.get("status") in ("flagged", "unverifiable")]
    return {"verdict": report.get("verdict"), "summary": report.get("summary", {}),
            "flagged": flagged,
            "script": {"working_title": script.get("working_title", ""),
                       "total_scenes": script.get("total_scenes", 0),
                       "scenes": script.get("scenes", [])}}


def _gate2_preview(pdir: pathlib.Path) -> dict:
    script = _load(pdir, "script.json", {}) or {}
    mix = _load(pdir, "audio/audio_manifest.json", {}) or {}
    style = _load(pdir, "style_guide.json", {}) or {}
    drafts = sorted((pdir / "scenes").glob("scene-*/renders/draft.mp4"),
                    key=str) if (pdir / "scenes").exists() else []
    return {"plan": {"working_title": script.get("working_title", ""),
                     "scenes": script.get("total_scenes", 0),
                     "est_runtime_sec": script.get("est_runtime_sec", 0),
                     "audio_duration_sec": mix.get("total_duration_sec", 0),
                     "plan": "Render each scene HTML, concat with FFmpeg, "
                             "mux narration + bed."},
            "draft_renders": drafts,
            "palette": style.get("palette", {}) if isinstance(style, dict) else {}}


def gate_detail(projects_dir: pathlib.Path, slug: str) -> dict | None:
    """The gate screen. Returns the verdict/flags (gate 1) or render plan + drafts
    (gate 2), the project label, and crucially `approvable` — False for a hard
    fact-check `block` (which the spine refuses to approve and routes back)."""
    pdir = projects_dir / slug
    proj = read_json(pdir / "project.json", None)
    if not isinstance(proj, dict):
        return None
    status = proj.get("status") or ""
    gate = status[len("blocked_at_"):] if status.startswith("blocked_at_") else None
    label = proj.get("title") or proj.get("topic") or proj.get("slug") or slug

    base = {"slug": proj.get("slug") or slug, "label": label, "status": status,
            "gate": gate, "blocked": gate is not None}

    if gate == "factcheck":
        prev = _gate1_preview(pdir)
        hard = prev.get("verdict") == "block"
        sup = (proj.get("supervisor", {}) or {})
        base.update({
            "kind": "factcheck", "preview": prev,
            "verdict": prev.get("verdict"),
            "summary": prev.get("summary", {}),
            "flagged": prev.get("flagged", []),
            "approvable": (not hard) and gate is not None,
            "hard_block": hard,
            "verified_claims": _verified_claims(pdir),
            "fix_history": (sup.get("fix_history", {}) or {}).get("factcheck", []),
        })
        return base
    if gate == "final_render":
        prev = _gate2_preview(pdir)
        # serialize draft render paths as project-relative names only (no abs path)
        drafts = [p.relative_to(pdir).as_posix() for p in prev.get("draft_renders", [])]
        base.update({
            "kind": "final_render", "plan": prev.get("plan", {}),
            "palette": prev.get("palette", {}),
            "draft_renders": drafts, "approvable": True, "hard_block": False,
            "has_video": (pdir / "video.mp4").exists(),
        })
        return base
    # not at a gate (done / running / failed / queued) — render an informational card
    base.update({"kind": "none", "approvable": False, "hard_block": False})
    return base


def _verified_claims(pdir: pathlib.Path) -> list[dict]:
    report = _load(pdir, "factcheck_report.json", {}) or {}
    return [{"claim_text": c.get("claim_text"), "scene_no": c.get("scene_no"),
             "sources": len(c.get("sources", []) or [])}
            for c in report.get("claims", []) if c.get("status") == "verified"][:20]
