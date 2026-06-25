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

import base64
import json
import pathlib
import shutil
import subprocess
import time

import shader_transition  # pure builder of the deterministic WebGL transition page

# Verified command surface (Phase 0). Render policy: draft mp4, --strict backstop,
# no --docker (Docker absent; per-machine determinism is the draft standard).
LINT_TIMEOUT = 90
VALIDATE_TIMEOUT = 150
INSPECT_TIMEOUT = 180
RENDER_TIMEOUT = 600
ASSEMBLE_TIMEOUT = 900
CHROME_FRAME_TIMEOUT = 60        # per-frame headless-Chrome screenshot budget

# Canonical assembly encode — every segment normalised to these params so the concat
# demuxer can stream-copy them together (and so trims/transitions interleave cleanly).
_CANON = ("-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "medium",
          "-crf", "18", "-an")


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


# Transient-failure retry (reliability): a saturated compose runs many headless-Chrome
# instances back-to-back; under load a validate/inspect Chrome can exit NON-ZERO though
# the scene is fine (parseable JSON, zero real findings). Those crashes hit only the
# LATER scenes and flake the whole video's gate. The checks are read-only/idempotent, so
# re-running a transient failure (with a brief pause for load to subside) is safe. A
# result with REAL findings — a console error, an inspect issue, a lint error, or a
# fail-closed 'note' (unparseable/timeout) — is deterministic and never retried.
GATE_TRANSIENT_RETRIES = 2        # extra attempts beyond the first
GATE_RETRY_SLEEP = 3.0            # seconds between attempts (patched to 0 in tests)


def _is_transient(result: dict) -> bool:
    """True when a check FAILED only because its Chrome exited non-zero under load —
    parseable JSON (no 'note'), zero substantive findings. Such a result is safe to
    retry; anything with real findings or a fail-closed note is not."""
    return (not result.get("ok")
            and "note" not in result
            and result.get("console_errors", 0) == 0
            and result.get("errors", 0) == 0
            and result.get("issues", 0) == 0)


def _retrying(check) -> dict:
    """Run a read-only gate check, retrying TRANSIENT (resource-contention) failures."""
    res = check()
    attempts = 0
    while _is_transient(res) and attempts < GATE_TRANSIENT_RETRIES:
        time.sleep(GATE_RETRY_SLEEP)
        res = check()
        attempts += 1
    return res


def run_gate(scene_dir: pathlib.Path, *, motion_strict: bool = False) -> dict:
    """The composition auto-gate: lint -> validate -> inspect. Short-circuits on the
    first hard failure so we don't spend a Chrome launch on an already-broken scene.
    Each step retries a transient Chrome crash (see _is_transient) so a saturated
    compose doesn't flake a perfectly good scene."""
    lint = _retrying(lambda: run_lint(scene_dir))
    if not lint["ok"]:
        return {"lint": lint, "validate": None, "inspect": None}
    validate = _retrying(lambda: run_validate(scene_dir))
    if not validate["ok"]:
        return {"lint": lint, "validate": validate, "inspect": None}
    inspect = _retrying(lambda: run_inspect(scene_dir, strict=motion_strict))
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
# ---- toolchain micro-helpers (all error-contained, never raise) -------
def _chrome_bin() -> str | None:
    for b in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        p = shutil.which(b)
        if p:
            return p
    return None


def _run_ff(args: list[str], *, timeout: int = ASSEMBLE_TIMEOUT) -> bool:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def _probe(src: pathlib.Path, entries: str, *, stream: bool) -> list[str] | None:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    cmd = [ffprobe, "-v", "error"]
    if stream:
        cmd += ["-select_streams", "v:0"]
    cmd += ["-show_entries", entries, "-of", "default=noprint_wrappers=1:nokey=1", str(src)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return out.stdout.strip().splitlines() if out.returncode == 0 else None


def _duration(src: pathlib.Path) -> float | None:
    r = _probe(src, "format=duration", stream=False)
    try:
        return float(r[0]) if r else None
    except (ValueError, IndexError):
        return None


def _dims(src: pathlib.Path) -> tuple[int, int]:
    r = _probe(src, "width,height", stream=True)
    try:
        return int(r[0]), int(r[1])
    except (ValueError, IndexError, TypeError):
        return 1920, 1080


def _encode_segment(ffmpeg: str, src: pathlib.Path, dst: pathlib.Path, fps: int,
                    w: int, h: int, *, trim_tail_frames: int = 0) -> bool:
    """Re-encode `src` to canonical params; optionally drop the last N frames (used to
    make room for a transition without changing total duration)."""
    args = [ffmpeg, "-y", "-i", str(src)]
    if trim_tail_frames > 0:
        dur = _duration(src)
        if dur is None:
            return False
        keep = max(1.0 / fps, dur - trim_tail_frames / float(fps))
        args += ["-t", f"{keep:.4f}"]
    args += ["-vf", f"scale={w}:{h}", "-r", str(fps), "-vsync", "cfr", *_CANON, str(dst)]
    return _run_ff(args)


def _extract_frame(ffmpeg: str, src: pathlib.Path, dst: pathlib.Path, *,
                   last: bool = False) -> bool:
    if last:
        args = [ffmpeg, "-y", "-sseof", "-0.07", "-i", str(src),
                "-update", "1", "-frames:v", "1", str(dst)]
    else:
        args = [ffmpeg, "-y", "-i", str(src), "-update", "1", "-frames:v", "1", str(dst)]
    return _run_ff(args, timeout=60)


def _render_transition_segment(ffmpeg: str, chrome: str, work: pathlib.Path,
                               from_png: pathlib.Path, to_png: pathlib.Path,
                               shader: str, frames: int, fps: int, w: int, h: int,
                               dst: pathlib.Path) -> bool:
    """Render the WebGL transition frame-by-frame (deterministic SwiftShader) into a
    canonical clip. Each frame is a one-shot headless screenshot at its progress."""
    def b64(p):
        return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
    try:
        html = shader_transition.build_transition_html(b64(from_png), b64(to_png),
                                                       shader, w, h)
    except (ValueError, OSError):
        return False
    page = work / "transition.html"
    page.write_text(html)
    page_url = page.resolve().as_uri()   # absolute file:// URI (a relative path is ERR_INVALID_URL)
    flags = shader_transition.chrome_flags() + [f"--window-size={w},{h}",
                                                "--virtual-time-budget=8000"]
    for i in range(frames):
        p = shader_transition.progress_for_frame(i, frames)
        shot = (work / f"f{i:03d}.png").resolve()
        cmd = [chrome, *flags, f"--screenshot={shot}", f"{page_url}#p={p}"]
        try:
            subprocess.run(cmd, capture_output=True, timeout=CHROME_FRAME_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError):
            return False
        if not shot.exists() or shot.stat().st_size == 0:
            return False
    # Sanity: a real transition varies with progress. If the first and middle frames are
    # byte-identical the page never rendered the shader (error page / blank GL) — bail so
    # the caller degrades to a clean concat instead of splicing in garbage.
    if frames >= 3:
        f0 = (work / "f000.png").read_bytes()
        fmid = (work / f"f{frames // 2:03d}.png").read_bytes()
        if f0 == fmid:
            return False
    return _run_ff([ffmpeg, "-y", "-framerate", str(fps), "-i", str(work / "f%03d.png"),
                    "-vf", f"scale={w}:{h}", "-r", str(fps), *_CANON, str(dst)])


def _assemble_with_shaders(ffmpeg: str, chrome: str, pdir: pathlib.Path, plan: dict,
                           npath: pathlib.Path | None) -> pathlib.Path | None:
    """Segment-based assembly that splices deterministic shader transitions at signature
    boundaries. Each shader replaces the outgoing scene's last `frames` (net-zero
    duration → narration stays in sync). Returns the output path, or None on any failure
    so the caller can fall back to a plain concat — the video never depends on WebGL."""
    steps = plan.get("steps", [])
    fps = int(plan.get("fps") or 30)
    first = next((pdir / s["render"] for s in steps if s.get("render")), None)
    if first is None:
        return None
    w, h = _dims(first)
    work = pdir / "renders" / "_shader"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    segments: list[pathlib.Path] = []
    for idx, s in enumerate(steps):
        if s.get("render"):
            nxt = steps[idx + 1] if idx + 1 < len(steps) else {}
            trim = int(nxt.get("frames", 0)) if nxt.get("mode") == "shader" else 0
            seg = work / f"seg_{idx:02d}_scene.mp4"
            if not _encode_segment(ffmpeg, pdir / s["render"], seg, fps, w, h,
                                   trim_tail_frames=trim):
                return None
            segments.append(seg)
        elif s.get("mode") == "shader":
            if not segments:
                return None
            from_png = work / f"from_{idx:02d}.png"
            to_png = work / f"to_{idx:02d}.png"
            # FROM = last frame of the (already-trimmed) outgoing segment; TO = first
            # frame of the incoming scene (which then plays full) — so it reads continuous.
            if not _extract_frame(ffmpeg, segments[-1], from_png, last=True):
                return None
            if not _extract_frame(ffmpeg, pdir / s["to_render"], to_png):
                return None
            tw = work / f"tw_{idx:02d}"
            tw.mkdir(exist_ok=True)
            seg = work / f"seg_{idx:02d}_trans.mp4"
            if not _render_transition_segment(ffmpeg, chrome, tw, from_png, to_png,
                                              s["shader"], int(s["frames"]), fps, w, h, seg):
                return None
            segments.append(seg)
        # xfade/concat boundary steps carry no segment — a hard cut (unchanged behavior).
    listing = work / "_segs.txt"
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in segments))
    silent = work / "silent.mp4"
    if not _run_ff([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c", "copy", str(silent)]):
        return None
    out = pdir / "video.mp4"
    if npath and npath.exists():
        ok = _run_ff([ffmpeg, "-y", "-i", str(silent), "-i", str(npath),
                      "-c:v", "copy", "-c:a", "aac", "-shortest", str(out)])
    else:
        ok = _run_ff([ffmpeg, "-y", "-i", str(silent), "-c", "copy", str(out)])
    if ok and out.exists():
        shutil.rmtree(work, ignore_errors=True)   # drop the large intermediate segments
        return out
    return None


def _assemble_concat(ffmpeg: str, pdir: pathlib.Path, renders: list[pathlib.Path],
                     plan: dict, npath: pathlib.Path | None) -> dict:
    """The proven, minimal path: hard-concat the scene renders, mux narration if present.
    Used when there are no shader transitions, and as the graceful fallback if one fails."""
    listing = pdir / "renders" / "_concat.txt"
    listing.parent.mkdir(parents=True, exist_ok=True)
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in renders))
    out = pdir / "video.mp4"
    cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listing)]
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


def assemble_final(pdir: pathlib.Path, plan: dict) -> dict:
    """Execute build_assembly_plan(...) into pdir/video.mp4 via FFmpeg.

    Signature beats get a deterministic WebGL shader transition (segment-spliced, net-
    zero duration); everything else is a hard concat. The shader path is attempted only
    when the plan asks for it AND headless Chrome is present, and ANY failure degrades
    gracefully to the plain concat — the final video never depends on WebGL. Narration is
    muxed if present. This is the one integration path that must run the real toolchain.
    """
    pdir = pathlib.Path(pdir)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return {"ok": False, "error": "ffmpeg not found — required for final assembly."}
    renders = [pdir / s["render"] for s in plan.get("steps", []) if s.get("render")]
    missing = [str(p) for p in renders if not p.exists()]
    if plan.get("missing_renders") or missing:
        return {"ok": False, "error": "missing per-scene renders; compose + gate first.",
                "missing": (plan.get("missing_renders") or []) + missing}
    narration = plan.get("narration")
    npath = pdir / narration if narration else None
    shader_steps = [s for s in plan.get("steps", []) if s.get("mode") == "shader"]
    if shader_steps:
        chrome = _chrome_bin()
        if chrome:
            out = _assemble_with_shaders(ffmpeg, chrome, pdir, plan, npath)
            if out is not None:
                return {"ok": True, "video": "video.mp4",
                        "shader_transitions": [s["shader"] for s in shader_steps],
                        "transitions": [s for s in plan.get("steps", []) if s.get("transition")],
                        "flags": plan.get("flags", [])}
        # Chrome missing or a shader step failed → degrade to a clean concat.
    return _assemble_concat(ffmpeg, pdir, renders, plan, npath)
