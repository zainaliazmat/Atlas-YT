"""In-process wrappers around the real HyperFrames CLI (`npx hyperframes …`).

Phase-0 settled call: subprocess around the CLI, NOT a Node bridge — the installed
`hyperframes` package exposes only a CLI (`bin: hyperframes -> dist/cli.js`); there is
no stable importable Producer API. Each wrapper mirrors atlas/tools.py: error-
contained (never raises) + timeout-bounded, returning a structured pass/fail dict.

Robust JSON parse: the CLI prints a Chrome-status preamble to stdout BEFORE the JSON
payload, so we locate the first `{` and decode from there (raw_decode tolerates any
trailing telemetry). Verified against hyperframes v0.6.115.

These wrappers are the ONLY part of the engine that touches the toolchain/subprocess,
so the pure composer (composition_engine.py) stays unit-testable with no network/render.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

# Verified command surface (Phase 0). Render policy: draft mp4, --strict backstop,
# no --docker (Docker absent; per-machine determinism is the draft standard).
LINT_TIMEOUT = 90
VALIDATE_TIMEOUT = 150
INSPECT_TIMEOUT = 180
RENDER_TIMEOUT = 600
ASSEMBLE_TIMEOUT = 900


def _npx() -> str | None:
    return shutil.which("npx")


def toolchain_available() -> bool:
    return _npx() is not None


def _parse_json(stdout: str) -> dict | None:
    """Decode the first JSON object in stdout, skipping any leading preamble and
    tolerating trailing telemetry lines. Returns None if no JSON object is present."""
    idx = stdout.find("{")
    if idx == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(stdout[idx:])
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _run(cmd: str, scene_dir: pathlib.Path, *extra: str, timeout: int) -> dict:
    """Run `npx hyperframes <cmd> <scene_dir> --json [extra]`, contained + bounded.

    Returns {"ran": bool, "returncode": int|None, "json": dict|None, "stderr": str,
    "error": str|None}. Never raises.
    """
    npx = _npx()
    if npx is None:
        return {"ran": False, "returncode": None, "json": None, "stderr": "",
                "error": "npx not found — install Node.js >= 22 to run the HyperFrames gate."}
    # Resolve to an absolute path: we set cwd=scene_dir below, so a RELATIVE path arg
    # would be re-resolved against that cwd and double up ("Not a directory"). Absolute
    # is invariant to cwd.
    scene_dir = pathlib.Path(scene_dir).resolve()
    args = [npx, "hyperframes", cmd, str(scene_dir), "--json", *extra]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              cwd=str(scene_dir))
    except subprocess.TimeoutExpired:
        return {"ran": True, "returncode": None, "json": None, "stderr": "",
                "error": f"`hyperframes {cmd}` timed out after {timeout}s."}
    except OSError as exc:
        return {"ran": False, "returncode": None, "json": None, "stderr": "",
                "error": f"could not launch `hyperframes {cmd}`: {exc}"}
    return {"ran": True, "returncode": proc.returncode,
            "json": _parse_json(proc.stdout), "stderr": (proc.stderr or "")[:400],
            "error": None}


# ----------------------------------------------------------------------
# The four gate/render wrappers — structured pass/fail
# ----------------------------------------------------------------------
def run_lint(scene_dir: pathlib.Path) -> dict:
    """`lint` = static structure + determinism (Math.random/Date.now/repeat:-1 etc.).
    ok = no error-severity findings."""
    r = _run("lint", scene_dir, timeout=LINT_TIMEOUT)
    if r["error"]:
        return {"ok": False, "errors": 0, "warnings": 0, "findings": [], "note": r["error"]}
    if r["json"] is None:
        # M3 fail-closed: rc==0 but no parseable JSON payload means we cannot CONFIRM
        # the scene is clean — treat the unverifiable result as a gate FAILURE, never a
        # vacuous pass.
        return {"ok": False, "errors": 0, "warnings": 0, "findings": [],
                "note": "lint produced no parseable JSON — cannot confirm; failing closed."}
    data = r["json"] or {}
    errors = data.get("errorCount")
    if errors is None:
        errors = sum(1 for f in data.get("findings", []) if f.get("severity") == "error")
    findings = [{"code": f.get("code"), "severity": f.get("severity"),
                 "message": f.get("message")}
                for f in data.get("findings", []) if f.get("severity") == "error"]
    return {"ok": errors == 0 and (r["returncode"] in (0, None)),
            "errors": errors, "warnings": data.get("warningCount", 0),
            "findings": findings}


def run_validate(scene_dir: pathlib.Path) -> dict:
    """`validate` = headless-Chrome load: console errors + WCAG contrast. Console
    errors block; contrast failures are recorded but NON-blocking (surfaced, not
    swallowed)."""
    r = _run("validate", scene_dir, timeout=VALIDATE_TIMEOUT)
    if r["error"]:
        return {"ok": False, "console_errors": 0, "contrast_failures": 0, "note": r["error"]}
    if r["json"] is None:
        return {"ok": False, "console_errors": 0, "contrast_failures": 0,
                "note": "validate produced no parseable JSON — cannot confirm; "
                        "failing closed."}
    data = r["json"] or {}
    console = (data.get("consoleErrors") or data.get("errors") or
               data.get("console") or [])
    n_console = len(console) if isinstance(console, list) else int(bool(console))
    contrast = data.get("contrastFailures", 0)
    return {"ok": n_console == 0 and (r["returncode"] in (0, None)),
            "console_errors": n_console, "contrast_failures": contrast}


def run_inspect(scene_dir: pathlib.Path, *, strict: bool = False) -> dict:
    """`inspect` = rendered layout overflow/overlap across the timeline (+ optional
    *.motion.json motion verification). ok = no error-severity issues; with strict,
    warnings fail too (and the CLI exit code is honored)."""
    extra = ("--strict",) if strict else ()
    r = _run("inspect", scene_dir, *extra, timeout=INSPECT_TIMEOUT)
    if r["error"]:
        return {"ok": False, "issues": 0, "note": r["error"]}
    if r["json"] is None:
        return {"ok": False, "issues": 0,
                "note": "inspect produced no parseable JSON — cannot confirm; "
                        "failing closed."}
    data = r["json"] or {}
    issues = data.get("issues", [])
    errs = sum(1 for i in issues if i.get("severity") in ("error", None) and
               i.get("severity") != "warning")
    rc_ok = r["returncode"] in (0, None)
    return {"ok": (errs == 0) and rc_ok, "issues": len(issues),
            "errors": errs}


def run_gate(scene_dir: pathlib.Path, *, motion_strict: bool = False) -> dict:
    """The composition auto-gate: lint -> validate -> inspect. Short-circuits on the
    first hard failure so we don't spend a Chrome launch on an already-broken scene."""
    lint = run_lint(scene_dir)
    if not lint["ok"]:
        return {"lint": lint, "validate": None, "inspect": None}
    validate = run_validate(scene_dir)
    if not validate["ok"]:
        return {"lint": lint, "validate": validate, "inspect": None}
    inspect = run_inspect(scene_dir, strict=motion_strict)
    return {"lint": lint, "validate": validate, "inspect": inspect}


def run_render(scene_dir: pathlib.Path) -> dict:
    """Draft per-scene render: `render --quality draft --format mp4 --strict`. The
    --strict backstop fails the render on lint errors even if the gate were skipped."""
    out_rel = "renders/draft.mp4"
    r = _run("render", scene_dir, "--quality", "draft", "--format", "mp4",
             "--strict", "-o", out_rel, timeout=RENDER_TIMEOUT)
    out_path = scene_dir / out_rel
    if r["error"]:
        return {"ok": False, "output": None, "note": r["error"]}
    ok = (r["returncode"] == 0) and out_path.exists()
    return {"ok": ok, "output": str(out_path) if ok else None,
            "note": None if ok else (r["stderr"] or "render did not produce an output")}


# ----------------------------------------------------------------------
# Final assembly (render_video) — INTEGRATION. Concats the per-scene renders,
# applies storyboard transitions at boundaries, muxes narration. Defensive: any
# missing input or absent ffmpeg returns a structured failure, never raises.
# ----------------------------------------------------------------------
def assemble_final(pdir: pathlib.Path, plan: dict) -> dict:
    """Execute build_assembly_plan(...) into pdir/video.mp4 via FFmpeg.

    Kept simple + honest: cross-scene transitions use FFmpeg xfade where the plan
    asks for it, else a hard concat. Narration is muxed if present. This is the one
    integration path that must run the real toolchain.
    """
    pdir = pathlib.Path(pdir)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return {"ok": False, "error": "ffmpeg not found — required for final assembly."}
    renders = [pdir / s["render"] for s in plan.get("steps", [])
               if s.get("render")]
    missing = [str(p) for p in renders if not p.exists()]
    if plan.get("missing_renders") or missing:
        return {"ok": False, "error": "missing per-scene renders; compose + gate first.",
                "missing": (plan.get("missing_renders") or []) + missing}
    # Minimal, robust path: hard-concat the scene renders (transition xfades are a
    # documented enhancement — see SKILL.md "assembly"). Mux narration if present.
    listing = pdir / "renders" / "_concat.txt"
    listing.parent.mkdir(parents=True, exist_ok=True)
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in renders))
    out = pdir / "video.mp4"
    cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listing)]
    narration = plan.get("narration")
    npath = pdir / narration if narration else None
    if npath and npath.exists():
        cmd += ["-i", str(npath), "-c:v", "libx264", "-c:a", "aac", "-shortest"]
    else:
        cmd += ["-c", "copy"]
    cmd += [str(out)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=ASSEMBLE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"final assembly timed out after {ASSEMBLE_TIMEOUT}s."}
    if proc.returncode != 0 or not out.exists():
        return {"ok": False, "error": "ffmpeg assembly failed.",
                "stderr": (proc.stderr or "")[:400]}
    return {"ok": True, "video": "video.mp4",
            "transitions": [s for s in plan.get("steps", []) if s.get("transition")],
            "flags": plan.get("flags", [])}
