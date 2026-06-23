"""Disposable projects-dir builder for the dashboard test suite.

Builds a tmp projects/ directory populated with real-shaped projects by copying a
real gold project and mutating copies. NOTHING here touches the real
`atlas/projects/` tree (we only READ the gold dir to copy it out).

The slug names below double as the dir names AND the API slugs the tests address.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import time

# Real source projects we COPY OUT OF (read-only). Never written to.
_ATLAS = pathlib.Path(__file__).resolve().parents[2]
_REAL_PROJECTS = _ATLAS / "projects"
GOLD_SRC = _REAL_PROJECTS / "gpt-4o-vs-claude-vs-gemini-vs-deepseek-comparison--20260621-013345-67a3"
HARD_BLOCK_SRC = _REAL_PROJECTS / "gpt-4o-vs-claude-vs-gemini-vs-deepseek-head-to-hea-20260621-034108-f28f"

SLUGS = {
    "done": "done",
    "blocked_clean": "blocked_clean",
    "hard_block": "hard_block",
    "blocked_final": "blocked_final",
    "queued": "queued",
    "corrupt": "corrupt",
}


def _write_json(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1))


def _read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text())


def build_projects(tmp_root: pathlib.Path) -> tuple[pathlib.Path, dict]:
    """Create a disposable projects dir under `tmp_root`; return (dir, SLUGS)."""
    pdir = tmp_root / "projects"
    pdir.mkdir(parents=True, exist_ok=True)
    now = time.time()

    # --- done: full copy of the real gold project (has video.mp4 ~9MB) ---
    done = pdir / SLUGS["done"]
    shutil.copytree(GOLD_SRC, done)
    proj = _read_json(done / "project.json")
    proj["slug"] = SLUGS["done"]
    proj["updated"] = now - 60
    _write_json(done / "project.json", proj)

    # --- blocked_clean: blocked_at_factcheck, status 'blocked', verdict PASS ---
    # APPROVABLE (no hard block). Based on gold, flipped to a clean block.
    bclean = pdir / SLUGS["blocked_clean"]
    shutil.copytree(GOLD_SRC, bclean)
    # drop the heavy video + downstream artifacts so it reads as paused at factcheck
    for junk in ("video.mp4", "renders", "scenes", "composition_manifest.json"):
        p = bclean / junk
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    proj = _read_json(bclean / "project.json")
    proj["slug"] = SLUGS["blocked_clean"]
    proj["status"] = "blocked_at_factcheck"
    proj["updated"] = now - 30
    proj.setdefault("gates", {})
    proj["gates"]["factcheck"] = {
        "status": "blocked",
        "details": {"verdict": "pass",
                    "summary": {"verified": 8, "flagged": 0, "unverifiable": 0},
                    "flagged": []},
    }
    proj["gates"]["final_render"] = {"status": "pending", "details": None}
    _write_json(bclean / "project.json", proj)
    # factcheck_report with a PASS verdict -> approvable
    _write_json(bclean / "factcheck_report.json", {
        "schema_version": "1.0",
        "verdict": "pass",
        "summary": {"verified": 8, "flagged": 0, "unverifiable": 0},
        "claims": [
            {"claim_id": "s1c1", "scene_no": 1, "claim_text": "A verified claim.",
             "status": "verified", "sources": [{"url": "https://example.test/a"}]},
        ],
    })

    # --- hard_block: blocked_at_factcheck, gate rejected, verdict BLOCK ---
    # UN-approvable: copy a real one (has a real block report w/ unverifiable claims).
    hblock = pdir / SLUGS["hard_block"]
    shutil.copytree(HARD_BLOCK_SRC, hblock)
    proj = _read_json(hblock / "project.json")
    proj["slug"] = SLUGS["hard_block"]
    proj["updated"] = now - 20
    _write_json(hblock / "project.json", proj)

    # --- blocked_final: blocked_at_final_render, gate blocked ---
    bfinal = pdir / SLUGS["blocked_final"]
    bfinal.mkdir()
    _write_json(bfinal / "project.json", {
        "schema_version": "1.0",
        "project_id": "bf000001",
        "slug": SLUGS["blocked_final"],
        "created": now - 500,
        "updated": now - 10,
        "title": "Blocked at final render",
        "niche": "",
        "topic": "A project paused at the final render gate.",
        "brief": "A project paused at the final render gate.",
        "status": "blocked_at_final_render",
        "config": {"gates": {"factcheck": True, "final_render": True}},
        "stages": {
            "research": {"status": "done", "artifact": "research_brief.json",
                         "validated": True, "updated": now - 400},
            "script": {"status": "done", "artifact": "script.json",
                       "validated": True, "updated": now - 380},
            "factcheck": {"status": "done", "artifact": "factcheck_report.json",
                          "validated": True, "updated": now - 360},
        },
        "gates": {
            "factcheck": {"status": "approved",
                          "details": {"verdict": "pass",
                                      "summary": {"verified": 3}}},
            "final_render": {"status": "blocked",
                             "details": {"working_title": "Render me",
                                         "scenes": 3}},
        },
        "artifacts": {},
        "history": [
            {"ts": now - 360, "stage": "factcheck", "decision": "pass",
             "why": "all claims sourced"},
        ],
    })
    _write_json(bfinal / "script.json", {
        "schema_version": "1.0",
        "working_title": "Render me",
        "hook": "hook line",
        "cta": "subscribe",
        "total_scenes": 3,
        "est_runtime_sec": 45.0,
        "scenes": [
            {"scene_no": 1, "narration": "one"},
            {"scene_no": 2, "narration": "two"},
            {"scene_no": 3, "narration": "three"},
        ],
    })
    _write_json(bfinal / "audio" / "audio_manifest.json", {
        "schema_version": "1.0",
        "total_duration_sec": 46.2,
        "integrated_lufs": -14.0,
        "target_lufs": -14.0,
    })

    # --- queued: a fresh created project, no artifacts ---
    queued = pdir / SLUGS["queued"]
    queued.mkdir()
    _write_json(queued / "project.json", {
        "schema_version": "1.0",
        "project_id": "q0000001",
        "slug": SLUGS["queued"],
        "created": now - 5,
        "updated": now - 5,
        "title": "Fresh in queue",
        "niche": "",
        "topic": "A just-created project waiting to run.",
        "brief": "A just-created project waiting to run.",
        "status": "created",
        "config": {"gates": {"factcheck": True, "final_render": True}},
        "stages": {},
        "gates": {},
        "artifacts": {},
        "history": [],
    })

    # --- corrupt: valid project.json, GARBAGE script.json ---
    corrupt = pdir / SLUGS["corrupt"]
    corrupt.mkdir()
    _write_json(corrupt / "project.json", {
        "schema_version": "1.0",
        "project_id": "c0000001",
        "slug": SLUGS["corrupt"],
        "created": now - 200,
        "updated": now - 40,
        "title": "Corrupt artifact project",
        "niche": "",
        "topic": "Has a corrupt script.json.",
        "brief": "Has a corrupt script.json.",
        "status": "running",
        "config": {"gates": {"factcheck": True, "final_render": True}},
        "stages": {
            "research": {"status": "done", "artifact": "research_brief.json",
                         "validated": True, "updated": now - 150},
            "script": {"status": "running", "artifact": "script.json",
                       "validated": False, "updated": now - 40},
        },
        "gates": {},
        "artifacts": {},
        "history": [],
    })
    # truncated / garbage JSON — proves artifact endpoint returns valid:false not 500.
    # NOTE: chat_state.load_json renames a corrupt file aside on read; that's fine in
    # this disposable copy. Write it as raw bytes that are NOT valid JSON.
    (corrupt / "script.json").write_text("{ this is not json")

    return pdir, dict(SLUGS)


def build_empty(tmp_root: pathlib.Path) -> pathlib.Path:
    """An EMPTY projects dir (exists, no children) for the empty-system case."""
    pdir = tmp_root / "projects_empty"
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir
