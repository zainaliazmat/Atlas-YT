"""studio.gate.dimensions — the 0-5 deterministic scorers (no LLM). Each takes the
evidence pack (studio.review.evidence.collect_evidence) + the loaded thresholds and
returns a DimResult. Pure functions: unit-test with a synthetic evidence dict."""
from __future__ import annotations

from .types import DimResult, band_score

AUDIO_TARGET_LUFS = -14.0
AUDIO_TOLERANCE = 1.0   # within ±1 LUFS of target = full marks


def _floor(t, name):
    return float(t["dimensions"][name]["floor"])


def score_motion_energy(ev: dict, t: dict) -> DimResult:
    cfg = t["dimensions"]["motion_energy"]
    val = (ev.get("global") or {}).get("motion_energy")
    floor = float(cfg["floor"])
    if val is None:
        return DimResult("motion_energy", None, floor, None,
                         ["motion energy unmeasurable (no render / cv2)"], {})
    lo, hi = cfg["band"]
    score = band_score(val, lo, hi)
    diags = []
    static = [s for s in (ev.get("motion") or {}).get("scenes", [])
              if (s.get("motion_energy") or 0) < lo]
    if static:
        nos = ", ".join(str(s["scene_no"]) for s in static)
        diags.append(f"scenes {nos} are visually static (energy < {lo})")
    if score < floor:
        diags.append(f"whole-render motion {round(val,2)} below bar")
    return DimResult("motion_energy", score, floor, score >= floor, diags,
                     {"value": val})


def score_pacing(ev: dict, t: dict) -> DimResult:
    cfg = t["dimensions"]["pacing"]
    val = (ev.get("global") or {}).get("cut_rhythm")
    floor = float(cfg["floor"])
    if val is None:
        return DimResult("pacing", None, floor, None, ["cut rhythm unmeasurable"], {})
    ideal_lo, ideal_hi = cfg.get("ideal", cfg["band"])
    if ideal_lo <= val <= ideal_hi:
        score = 5.0
    else:
        lo, hi = cfg["band"]
        # distance outside the ideal band, scaled across the full band
        dist = (ideal_lo - val) if val < ideal_lo else (val - ideal_hi)
        span = max(ideal_lo - lo, hi - ideal_hi, 1e-6)
        score = round(max(0.0, 5.0 * (1 - dist / span)), 3)
    diags = [] if score >= floor else [f"median scene {round(val,2)}s outside ideal {ideal_lo}-{ideal_hi}s"]
    return DimResult("pacing", score, floor, score >= floor, diags, {"value": val})


def score_audio(ev: dict, t: dict) -> DimResult:
    cfg = t["dimensions"]["audio"]
    ld = ev.get("loudness") or {}
    lufs, clipping = ld.get("integrated_lufs"), ld.get("clipping")
    floor = float(cfg["floor"])
    if lufs is None and clipping is None:
        return DimResult("audio", None, floor, None,
                         [f"loudness unmeasurable ({ld.get('error','no audio')})"], {})
    diags = []
    # closeness to target on a 0..1 scale → mapped to 0..5
    if lufs is None:
        closeness = 0.5
    else:
        off = abs(lufs - AUDIO_TARGET_LUFS)
        closeness = max(0.0, 1.0 - max(0.0, off - AUDIO_TOLERANCE) / 8.0)
        if off > AUDIO_TOLERANCE:
            diags.append(f"{lufs} LUFS, {round(lufs-AUDIO_TARGET_LUFS,1)} from the {AUDIO_TARGET_LUFS} target")
    score = band_score(closeness, *cfg["band"])
    if clipping:
        diags.append(f"true-peak clipping ({ld.get('true_peak_dbtp')} dBTP ≥ -1.0)")
        score = min(score, floor - 0.5)   # clipping cannot pass
    return DimResult("audio", round(score, 3), floor, score >= floor, diags,
                     {"lufs": lufs, "clipping": clipping})


def score_dead_air(ev: dict, t: dict) -> DimResult:
    cfg = t["dimensions"]["dead_air"]
    floor = float(cfg["floor"])
    scenes = (ev.get("motion") or {}).get("scenes") or []
    if not scenes:
        return DimResult("dead_air", None, floor, None, ["no per-scene motion (no render)"], {})
    flagged = [s for s in scenes if s.get("flags")]
    clean_frac = 1.0 - len(flagged) / len(scenes)
    score = band_score(clean_frac, *cfg["band"])
    diags = []
    if flagged:
        nos = ", ".join(str(s["scene_no"]) for s in flagged)
        kinds = sorted({f for s in flagged for f in s["flags"]})
        diags.append(f"dead air on scene(s) {nos} ({', '.join(kinds)})")
    return DimResult("dead_air", score, floor, score >= floor, diags,
                     {"flagged": [s["scene_no"] for s in flagged], "total": len(scenes)})
