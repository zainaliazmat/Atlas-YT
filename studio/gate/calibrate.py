"""studio.gate.calibrate — prove the gate discriminates: the flat draft (dark-truth-v2)
must BLOCK and the hand-crafted reference (dark-truth-social) must PASS. Run with the real
artifacts:  python -m studio.gate.calibrate"""
from __future__ import annotations

from pathlib import Path

from .. import config
from . import scorecard

LOW_SLUG = "dark-truth-v2"
HIGH_REF = "dark-truth-social"


def calibrate(*, vision_fn=None) -> dict:
    low = scorecard.score(slug=LOW_SLUG, vision_fn=vision_fn)
    ref_dir = config.REPO_ROOT / "reference" / HIGH_REF
    high = scorecard.score(
        index_html=ref_dir / "index.html",
        video=ref_dir / "renders" / f"{HIGH_REF}.mp4",
        pdir=ref_dir, thresholds=None, vision_fn=vision_fn, polish=bool(vision_fn))
    discriminates = (low["verdict"] == "BLOCKED" and high["verdict"] == "PASS")
    summary = (f"LOW {LOW_SLUG}: {low['verdict']} (overall {low['overall']})\n"
               f"HIGH {HIGH_REF}: {high['verdict']} (overall {high['overall']})\n"
               f"DISCRIMINATES: {discriminates}")
    return {"low": low, "high": high, "discriminates": discriminates, "summary": summary}


def main() -> int:
    res = calibrate()
    print(res["summary"])
    print("\nLOW block reasons:")
    for r in res["low"]["reasons"]:
        print(f"  - {r}")
    return 0 if res["discriminates"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
