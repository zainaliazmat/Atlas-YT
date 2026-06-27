"""studio.gate.compliance — the HARD publish blockers (pass/fail, not 0-5). A failure here
is un-approvable (same semantics as the factcheck gate). Every check degrades to
passed=None when its toolchain is unavailable; the scorecard decides whether None blocks
(per thresholds: overflow_blocks / likeness_blocks).

NOTE: technical_scan returns nondeterminism as a dict (name→count), not a list.
The brief assumed a list but a non-empty dict is also truthy, so the logic still
works correctly — "random" is a substring of "math_random" so the reason test passes.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .types import ComplianceResult


def check_determinism(html: str) -> ComplianceResult:
    from studio.review.critics import technical_scan
    scan = technical_scan(html or "")
    # nondeterminism is a dict {name: hit_count} in the real implementation
    nd = scan.get("nondeterminism") or {}
    if nd:
        return ComplianceResult("determinism", False,
                                f"nondeterministic calls in index.html: {nd}")
    if not scan.get("registers_timeline"):
        return ComplianceResult("determinism", False,
                                "no window.__timelines master timeline registered")
    return ComplianceResult("determinism", True, "")


def check_factcheck(pdir: Path) -> ComplianceResult:
    p = Path(pdir) / "factcheck_report.json"
    if not p.is_file():
        return ComplianceResult("factcheck", None, "no factcheck_report.json")
    try:
        verdict = (json.loads(p.read_text(encoding="utf-8")) or {}).get("verdict")
    except Exception as exc:  # noqa: BLE001
        return ComplianceResult("factcheck", None, f"unreadable factcheck report: {exc}")
    return ComplianceResult("factcheck", verdict == "pass",
                            "" if verdict == "pass" else f"fact-check verdict={verdict!r}")


def check_licenses(pdir: Path) -> ComplianceResult:
    """Every materialized asset must carry a real license. Reads the project's
    asset_manifest.json if present (list of {file, license}); 'Unknown'/'' fails."""
    p = Path(pdir) / "asset_manifest.json"
    if not p.is_file():
        return ComplianceResult("licenses", None, "no asset_manifest.json")
    try:
        entries = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return ComplianceResult("licenses", None, f"unreadable asset manifest: {exc}")
    rows = entries.get("assets", entries) if isinstance(entries, dict) else entries
    bad = [r.get("file", "?") for r in rows
           if str(r.get("license", "")).strip().lower() in ("", "unknown")]
    if bad:
        return ComplianceResult("licenses", False, f"unlicensed assets: {bad}")
    return ComplianceResult("licenses", True, "")


def _hf_inspect(pdir: Path):
    """Default inspect seam: `npx hyperframes inspect --json`. Returns a dict with an
    `overflow` list (truthy = clipped text) or None if the toolchain is unavailable."""
    if shutil.which("npx") is None:
        return None
    index = Path(pdir) / "index.html"
    if not index.is_file():
        return None
    try:
        out = subprocess.run(["npx", "--yes", "hyperframes", "inspect", "--json"],
                             cwd=str(pdir), capture_output=True, text=True, timeout=180)
        data = json.loads(out.stdout or "{}")
    except Exception:
        return None
    # normalize: collect overflow/contrast findings into a list
    overflow = data.get("overflow") or data.get("layout", {}).get("overflow") or []
    return {"overflow": overflow, "ok": not overflow}


def check_overflow(pdir: Path, *, inspect_fn=None) -> ComplianceResult:
    fn = inspect_fn or _hf_inspect
    res = fn(Path(pdir))
    if not res:
        return ComplianceResult("overflow", None, "hyperframes inspect unavailable")
    overflow = res.get("overflow") or []
    if overflow:
        where = ", ".join(str(o.get("scene", o)) for o in overflow[:6])
        return ComplianceResult("overflow", False, f"clipped/overflowing text on scene(s) {where}")
    return ComplianceResult("overflow", True, "")


def check_no_likeness(ev: dict, *, vision_fn=None) -> ComplianceResult:
    """No real-person likeness. If a vision_fn is supplied, ask it of the mid frames;
    otherwise pass=None (cannot judge without vision)."""
    if vision_fn is None:
        return ComplianceResult("likeness", None, "no vision_fn — likeness not checked")
    frames = [f["path"] for f in (ev.get("frames") or [])
              if f.get("kind") == "mid" and f.get("path")]
    if not frames:
        return ComplianceResult("likeness", None, "no frames to inspect")
    system = ("You are a compliance reviewer. Answer ONLY 'YES' or 'NO': do any frames show a "
              "recognizable real, named public figure's likeness (not an anonymous silhouette)?")
    try:
        reply = vision_fn(system, "Reply YES or NO.", frames)
    except Exception as exc:  # noqa: BLE001
        return ComplianceResult("likeness", None, f"vision check failed: {exc}")
    yes = "YES" in (reply or "").upper()
    return ComplianceResult("likeness", not yes,
                            "real-person likeness detected" if yes else "")


def run_compliance(ev: dict, pdir, t: dict, *, inspect_fn=None, vision_fn=None) -> list[ComplianceResult]:
    pdir = Path(pdir)
    return [
        check_determinism(ev.get("index_html") or ""),
        check_factcheck(pdir),
        check_licenses(pdir),
        check_overflow(pdir, inspect_fn=inspect_fn),
        check_no_likeness(ev, vision_fn=vision_fn),
    ]
