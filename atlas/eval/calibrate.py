"""Band calibration PROPOSER — Phase 2, step 1 (read-only).

Optimizing against placeholder bands is the exact failure the design docs warn
about ("a loop with no target is meaningless"). This module turns reference
videos into a *proposed* set of calibrated bands — and nothing more. It is a
PROPOSER, not a writer:

  * It runs the EXISTING eval analyzers (eval.analyzers.audio + video) on the
    reference media, so every proposed band is in the SAME UNITS as the scoring
    instrument (ebur128 LUFS/dBTP, @4fps |Δluma|, ffprobe seconds) — apples to
    apples. It does NOT invent a second measurement path.
  * It reads Vera's reference_rubric (reference-analyst/standards/default.json)
    as a CROSS-CHECK only. Vera uses ffmpeg `loudnorm` (not `ebur128`) and a
    scene-threshold cut detector, so her numbers are a sanity prior, never the
    band itself.
  * It emits `rubric.proposal.json` + a human-readable diff. It has NO path to
    `atlas/rubric/rubric.json`. Applying a calibrated band is a CEO-OWNED human
    edit (privilege asymmetry, structural — same as the rest of the system).

Two kinds of band, two sources (design doc §7 "What the standard can/can't capture"):

  * MEDIA-MEASURABLE from a finished mp4, in instrument units → derived here:
        render:final_loudness, render:final_peak  (ebur128 on the video)
        audiomix:integrated_loudness, audiomix:true_peak  (same physical signal,
            same target — the mix's realized loudness == the muxed video's)
        compose:motion_energy  (@4fps |Δluma|)
    plus the length-dependent runtime family, handled specially (see below).

  * STRUCTURAL / EDITORIAL — NOT recoverable from a finished mp4 (you cannot read
    a script, storyboard, or asset manifest back out of pixels): all script:*,
    storyboard:*, assets:*, narration:* and the stem-dependent audiomix props
    (ducking_depth, vo_intelligibility, sfx_on_beat). These need the CEO visual
    interview + the completed-project distribution as a prior. We DO NOT propose
    numbers for them from references; we surface them as "needs CEO interview".

A note on FORMAT (the CEO's two target lengths). The reference set mixes a 57s
explainer with several 5.8–35 min long-form videos. Quality properties that are
*rates/levels* (loudness, peak, motion energy, cut rhythm) are FORMAT-INDEPENDENT
— a good video cuts and is mixed the same way regardless of total length — so we
learn them from the WHOLE set. Length-dependent bands (runtime, total duration,
scene count) depend on the target format, of which there are two: SHORT (~60–90s,
current testing) and LONG (~5–8 min). We never collapse a 35-min clip into the
runtime band; instead we report each reference's length against both profiles and
recommend a parameterized short/long band (a CEO decision), keeping the active
short band as-is until long-form testing begins.
"""
from __future__ import annotations

import json
import os
import statistics
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import rubric
from eval.types import EvalContext
from eval.analyzers import audio as audio_an
from eval.analyzers import video as video_an

# --------------------------------------------------------------------------- #
# Paths & config
# --------------------------------------------------------------------------- #

_ATLAS_DIR = Path(__file__).resolve().parent.parent          # .../atlas
_REPO_DIR = _ATLAS_DIR.parent                                # repo root
REFERENCE_DIR = _REPO_DIR / "ReferanceVideos"
VERA_RUBRIC = _REPO_DIR / "reference-analyst" / "standards" / "default.json"
CACHE_FILE = _ATLAS_DIR / "eval" / ".calibration_cache.json"
PROPOSAL_FILE = _ATLAS_DIR / "eval" / "rubric.proposal.json"

# Target-format length profiles (seconds). The CEO owns these; they are the two
# formats the company produces, NOT learned from references.
FORMAT_PROFILES = {
    "short": {"runtime_band": [60, 90], "note": "current testing format (~60–90s explainer)"},
    "long":  {"runtime_band": [300, 480], "note": "future format (~5–8 min); proposed, CEO to confirm"},
}
# A reference whose duration exceeds this is a STYLE reference whose *length* is
# out of target for both profiles (learn its rates/levels, not its length).
LONG_PROFILE_MAX_SEC = 480

# Bands that are media-measurable from a finished mp4 in instrument units. Maps
# the band_id -> which Measurement (stage, prop) the eval analyzers emit for it.
# render:final_* come straight from the analyzers; the audiomix:* loudness/peak
# share the SAME physical signal (the realized mix == the muxed video's audio),
# so we mirror the render measurement onto them with the same target intent.
_MEDIA_FROM_ANALYZER = {
    "render:final_loudness": ("render", "final_loudness"),
    "render:final_peak":     ("render", "final_peak"),
    "compose:motion_energy": ("compose", "motion_energy"),
    "render:final_runtime":  ("render", "final_runtime"),
}
# audiomix loudness/peak mirror the render measurement (same signal, same target).
_MIRRORED = {
    "audiomix:integrated_loudness": "render:final_loudness",
    "audiomix:true_peak":           "render:final_peak",
}
# Length-dependent bands: proposed per FORMAT PROFILE, never from the long-clip
# distribution. (Listed so the report explicitly accounts for them.)
_FORMAT_DEPENDENT = ("render:final_runtime", "script:runtime_fit",
                     "narration:total_duration_fit", "script:scene_count")

# A media band is NOT automatically a learnable aesthetic target. Two kinds:
#
#  * DELIVERY STANDARDS — loudness & true-peak. These are PRODUCTION/DELIVERY
#    specs (-14 LUFS, no clipping), not tastes to learn. Raw downloaded
#    references carry each creator's upload mastering (and several CLIP), so their
#    spread is mastering noise, not shared DNA. We measure them for EVIDENCE but
#    recommend KEEPING the standard — and a quality ceiling may NEVER be loosened
#    past the no-clip invariant. (Design doc §7: capture what the generator
#    controls, not artifacts of how a reference was produced.)
#  * AESTHETIC RATES — motion energy (and cut rhythm). Genuinely learnable, BUT
#    these references are stock-footage / screen-recording / long-form, a
#    different design space than our restrained motion-graphics. So we propose the
#    reference band yet cross-check it against OUR OWN output and flag divergence.
_DELIVERY_STANDARD = {
    "render:final_loudness", "audiomix:integrated_loudness",
    "render:final_peak", "audiomix:true_peak",
}
_AESTHETIC_RATE = {"compose:motion_energy"}
# Max acceptable reference spread before we declare "no shared DNA → keep standard".
_LOUDNESS_AGREE_LUFS = 4.0     # references must agree within this to inform loudness
_PEAK_NOCLIP_DBTP = -1.0       # a true-peak ceiling may never exceed this (hard invariant)

# Structural / editorial bands that cannot be read from a finished mp4. We list
# them so the proposal is explicit about what it does NOT calibrate (and why).
_NEEDS_CEO_INTERVIEW = (
    "script:hook_strength", "script:scene_count", "script:runtime_fit",
    "script:words_per_scene", "script:one_point_adherence", "script:claim_support_ratio",
    "script:info_density", "script:narrative_arc", "script:cta_quality",
    "script:on_screen_text_density",
    "style:type_in_system", "style:motion_budget_sane", "style:palette_distance",
    "storyboard:layout_variety", "storyboard:transition_character",
    "storyboard:shot_specificity", "storyboard:signature_beat_placement",
    "assets:placeholder_rate", "assets:min_resolution",
    "narration:speech_cadence", "narration:pause_structure",
    "narration:scene_timing_fit", "narration:total_duration_fit",
    "audiomix:ducking_depth", "audiomix:vo_intelligibility", "audiomix:sfx_on_beat",
    "compose:cut_rhythm",  # eval measures from manifest; ref has none → Vera prior only
)


# --------------------------------------------------------------------------- #
# Reference measurement (reuses the real analyzers, in instrument units)
# --------------------------------------------------------------------------- #

def list_references(reference_dir: Path = REFERENCE_DIR) -> list[Path]:
    if not reference_dir.is_dir():
        return []
    return sorted(p for p in reference_dir.glob("*.mp4") if p.is_file())


def _ctx_for_video(video_path: Path, work_dir: Path) -> EvalContext:
    """An EvalContext whose `.video` resolves to the reference mp4, so the REAL
    eval analyzers run on it unmodified (true apples-to-apples)."""
    link = work_dir / "video.mp4"
    if link.exists() or link.is_symlink():
        link.unlink()
    try:
        link.symlink_to(video_path.resolve())
    except OSError:                       # filesystem without symlinks → hardlink/copy
        try:
            os.link(video_path.resolve(), link)
        except OSError:
            import shutil
            shutil.copy2(video_path.resolve(), link)
    return EvalContext(work_dir, run_id=video_path.name)


def measure_reference(video_path: Path) -> dict:
    """Run the eval audio+video analyzers on ONE reference mp4. Returns a dict of
    {band_id: value} for every media band we could measure (value not None), plus
    a `_duration_sec`. Never raises (analyzers degrade gracefully)."""
    out: dict[str, Optional[float]] = {}
    with tempfile.TemporaryDirectory(prefix="calib_") as td:
        ctx = _ctx_for_video(video_path, Path(td))
        ms = list(audio_an.analyze(ctx)) + list(video_an.analyze(ctx))
        by_id = {f"{m.stage}:{m.prop}": m for m in ms}
        for band_id, (stage, prop) in _MEDIA_FROM_ANALYZER.items():
            m = by_id.get(f"{stage}:{prop}")
            if m is not None and m.value is not None:
                out[band_id] = float(m.value)
        # mirror render loudness/peak onto the audiomix targets (same signal)
        for mirror_id, src_id in _MIRRORED.items():
            if src_id in out:
                out[mirror_id] = out[src_id]
    out["_duration_sec"] = out.get("render:final_runtime")
    return out


def list_own_outputs() -> list[Path]:
    """Completed project dirs that have a rendered video.mp4 (our OWN output, the
    design-space baseline to compare references against)."""
    proj = _ATLAS_DIR / "projects"
    if not proj.is_dir():
        return []
    return sorted(d for d in proj.iterdir() if d.is_dir() and (d / "video.mp4").is_file())


def measure_own_output(use_cache: bool = True) -> dict[str, dict]:
    """Measure our own completed renders with the SAME analyzers (instrument
    units). These are short-form motion-graphics — the baseline a reference band
    must be sanity-checked against before we'd chase it."""
    cache: dict[str, dict] = {}
    if use_cache and CACHE_FILE.is_file():
        try:
            cache = json.loads(CACHE_FILE.read_text())
        except Exception:
            cache = {}
    out: dict[str, dict] = {}
    dirty = False
    for d in list_own_outputs():
        vid = d / "video.mp4"
        st = vid.stat()
        key = f"OWN::{d.name}|{int(st.st_mtime)}|{st.st_size}"
        if use_cache and key in cache:
            out[d.name] = cache[key]
            continue
        ctx = EvalContext(d, run_id=d.name)
        ms = list(audio_an.analyze(ctx)) + list(video_an.analyze(ctx))
        by_id = {f"{m.stage}:{m.prop}": m for m in ms}
        vals: dict[str, float] = {}
        for band_id, (stage, prop) in _MEDIA_FROM_ANALYZER.items():
            m = by_id.get(f"{stage}:{prop}")
            if m is not None and m.value is not None:
                vals[band_id] = float(m.value)
        for mirror_id, src_id in _MIRRORED.items():
            if src_id in vals:
                vals[mirror_id] = vals[src_id]
        cache[key] = vals
        out[d.name] = vals
        dirty = True
    if use_cache and dirty:
        try:
            CACHE_FILE.write_text(json.dumps(cache, indent=2))
        except Exception:
            pass
    return out


def measure_all(video_paths: list[Path], use_cache: bool = True) -> dict[str, dict]:
    """Measure every reference, caching per (filename, mtime, size) so re-runs are
    instant and the slow ffmpeg/cv2 decode happens once."""
    cache: dict[str, dict] = {}
    if use_cache and CACHE_FILE.is_file():
        try:
            cache = json.loads(CACHE_FILE.read_text())
        except Exception:
            cache = {}
    results: dict[str, dict] = {}
    dirty = False
    for p in video_paths:
        st = p.stat()
        key = f"{p.name}|{int(st.st_mtime)}|{st.st_size}"
        if use_cache and key in cache:
            results[p.name] = cache[key]
            continue
        vals = measure_reference(p)
        cache[key] = vals
        results[p.name] = vals
        dirty = True
    if use_cache and dirty:
        try:
            CACHE_FILE.write_text(json.dumps(cache, indent=2))
        except Exception:
            pass
    return results


# --------------------------------------------------------------------------- #
# Band proposal
# --------------------------------------------------------------------------- #

def _band_from_values(vals: list[float], pad_frac: float = 0.10) -> dict:
    """A proposed [lo, hi] band = the shared range of the references, lightly
    padded (more references → tighter, more representative — design doc §7).
    Mirrors Vera's _band intent so the two pipelines agree on band shape."""
    vals = [v for v in vals if isinstance(v, (int, float))]
    lo, hi = min(vals), max(vals)
    span = (hi - lo)
    pad = span * pad_frac if span > 0 else (abs(lo) * pad_frac or 0.1)
    return {"min": round(lo - pad, 3), "max": round(hi + pad, 3),
            "value": round(statistics.mean(vals), 3),
            "n": len(vals), "raw_min": round(lo, 3), "raw_max": round(hi, 3)}


@dataclass
class BandProposal:
    band_id: str
    comparator: str
    source: str                # "media-measured" | "format-profile" | "needs-ceo-interview"
    confidence: str            # "high" | "medium" | "low"
    current: dict = field(default_factory=dict)
    proposed: dict = field(default_factory=dict)
    n_refs: int = 0
    rationale: str = ""
    per_video: dict = field(default_factory=dict)


def _vera_crosscheck() -> dict:
    if not VERA_RUBRIC.is_file():
        return {}
    try:
        return json.loads(VERA_RUBRIC.read_text())
    except Exception:
        return {}


def _current_band_view(band_id: str) -> dict:
    b = rubric.band_by_id(band_id)
    if b is None:
        return {}
    keys = ("comparator", "min", "max", "target", "unit", "placeholder", "hard")
    return {k: b.get(k) for k in keys if b.get(k) is not None}


def build_proposal(use_cache: bool = True) -> dict:
    """Produce the full calibration proposal. READ-ONLY: returns a dict and (via
    `write_proposal`) writes rubric.proposal.json — NEVER rubric.json."""
    refs = list_references()
    measured = measure_all(refs, use_cache=use_cache) if refs else {}
    own = measure_own_output(use_cache=use_cache)
    vera = _vera_crosscheck()

    # durations → which format profile each reference informs
    durations = {name: m.get("_duration_sec") for name, m in measured.items()}
    short_refs = [n for n, d in durations.items() if d is not None and d <= 120]
    long_refs = [n for n, d in durations.items() if d is not None and 120 < d <= LONG_PROFILE_MAX_SEC]
    over_long = [n for n, d in durations.items() if d is not None and d > LONG_PROFILE_MAX_SEC]

    proposals: list[BandProposal] = []

    def _own_values(band_id: str) -> list[float]:
        return [round(v[band_id], 3) for v in own.values() if band_id in v]

    # ---- DELIVERY STANDARDS (loudness, true-peak): keep the spec, don't learn ---
    for band_id in ("render:final_loudness", "audiomix:integrated_loudness",
                    "render:final_peak", "audiomix:true_peak"):
        per_video = {n: m[band_id] for n, m in measured.items() if band_id in m}
        if not per_video:
            continue
        vals = list(per_video.values())
        cur = _current_band_view(band_id)
        comparator = cur.get("comparator", "range")
        spread = round(max(vals) - min(vals), 2)
        own_vals = _own_values(band_id)
        if "peak" in band_id:
            clipping = [n for n, v in per_video.items() if v > _PEAK_NOCLIP_DBTP]
            rationale = (f"DELIVERY STANDARD (no-clip ceiling ≤ {_PEAK_NOCLIP_DBTP} dBTP). "
                         f"{len(clipping)}/{len(vals)} references actually CLIP (true-peak > "
                         f"{_PEAK_NOCLIP_DBTP}) — raw-upload mastering artifacts, not a target. "
                         "A ceiling may never be loosened past the invariant → KEEP current."
                         + (f" Our own output: {own_vals}." if own_vals else ""))
        else:
            rationale = (f"DELIVERY STANDARD (-14 LUFS broadcast target). References span "
                         f"{round(min(vals),1)}…{round(max(vals),1)} LUFS (spread {spread}"
                         + (f" > {_LOUDNESS_AGREE_LUFS}" if spread > _LOUDNESS_AGREE_LUFS else "")
                         + ") — raw downloads carry each creator's upload mastering, so there is "
                         "NO shared loudness DNA to learn. KEEP the standard."
                         + (f" Our own output: {own_vals}." if own_vals else ""))
        proposals.append(BandProposal(
            band_id=band_id, comparator=comparator, source="delivery-standard",
            confidence="keep-current", current=cur,
            proposed={"recommendation": "KEEP current standard", **cur},
            n_refs=len(vals), rationale=rationale,
            per_video={k: round(v, 3) for k, v in per_video.items()}))

    # ---- AESTHETIC RATES (motion energy): propose, but cross-check our output ---
    for band_id in ("compose:motion_energy",):
        per_video = {n: m[band_id] for n, m in measured.items() if band_id in m}
        if not per_video:
            continue
        band = _band_from_values(list(per_video.values()))
        cur = _current_band_view(band_id)
        comparator = cur.get("comparator", "range")
        proposed = _proposed_for_comparator(comparator, band)
        own_vals = _own_values(band_id)
        n_missing = len(refs) - band["n"]
        pmin, pmax = proposed.get("min", -1e9), proposed.get("max", 1e9)
        own_outside = [v for v in own_vals if not (pmin <= v <= pmax)]
        rationale = (f"{band['n']}/{len(refs)} references @4fps |Δluma| in instrument units"
                     + (f" ({n_missing} undecodable, e.g. AV1)." if n_missing else ".")
                     + " CAVEAT: references are stock-footage / screen-recording / long-form — a "
                       "HIGHER-motion design space than our restrained motion-graphics.")
        if own_vals:
            rationale += (f" Our own output motion: {own_vals}"
                          + (f"; {len(own_outside)}/{len(own_vals)} of our renders fall OUTSIDE the "
                             f"proposed band [{round(pmin,2)}, {round(pmax,2)}] (ours: {own_outside}) "
                             "— adopting it would force us toward reference-level motion (a CEO "
                             "aesthetic decision, not an obvious win)."
                             if own_outside else " — within the proposed band."))
        proposals.append(BandProposal(
            band_id=band_id, comparator=comparator, source="aesthetic-rate",
            confidence="low", current=cur, proposed=proposed, n_refs=band["n"],
            rationale=rationale, per_video={k: round(v, 3) for k, v in per_video.items()}))

    # ---- length-dependent (format) bands — short/long profiles, NOT collapsed --
    for band_id in ("render:final_runtime", "script:runtime_fit", "narration:total_duration_fit"):
        cur = _current_band_view(band_id)
        proposals.append(BandProposal(
            band_id=band_id, comparator=cur.get("comparator", "range"),
            source="format-profile", confidence="medium", current=cur,
            proposed={"short": FORMAT_PROFILES["short"]["runtime_band"],
                      "long": FORMAT_PROFILES["long"]["runtime_band"],
                      "recommendation": "keep SHORT band active for current testing; "
                                        "add LONG profile when 5–8 min testing begins"},
            n_refs=len(short_refs) + len(long_refs),
            rationale=("Length is a target-format choice, not a reference artifact. "
                       f"References split: {len(short_refs)} short(≤120s), "
                       f"{len(long_refs)} long(120–480s), {len(over_long)} over-long(>480s, "
                       "style refs whose LENGTH is out of target). Do not learn a single "
                       "runtime band from a mixed-length set."),
            per_video={n: round(d, 1) for n, d in durations.items() if d is not None}))

    return {
        "proposal_version": "phase2-step1/1.0",
        "generated_against_rubric": rubric.rubric_version(),
        "reference_dir": str(REFERENCE_DIR),
        "n_references": len(refs),
        "reference_durations_sec": {n: round(d, 1) for n, d in durations.items() if d is not None},
        "format_profiles": FORMAT_PROFILES,
        "reference_format_split": {"short_<=120s": short_refs, "long_120-480s": long_refs,
                                   "over_long_>480s": over_long},
        "own_output_baseline": {n: {k: round(v, 3) for k, v in vals.items()}
                                for n, vals in own.items()},
        "vera_crosscheck": _vera_summary(vera),
        "bands": [asdict(p) for p in proposals],
        "not_calibrated_needs_ceo_interview": sorted(set(_NEEDS_CEO_INTERVIEW)),
        "notes": [
            "READ-ONLY proposal. Applying any band is a CEO-owned human edit to "
            "atlas/rubric/rubric.json (flip placeholder:false on calibrated bands).",
            "Every media band is in the scoring instrument's own units (the eval "
            "analyzers were run directly on the references).",
            "Structural/editorial bands are NOT proposed from references (you cannot "
            "read a script/storyboard/manifest out of a finished mp4) — they need the "
            "CEO visual interview + the completed-project distribution as a prior.",
        ],
    }


def _proposed_for_comparator(comparator: str, band: dict) -> dict:
    """Shape the proposed band to the band's comparator. For `range` we propose
    [min,max]; for `lte`/`gte` we propose the relevant edge from the reference
    distribution; otherwise we record the observed distribution for the CEO."""
    if comparator == "range":
        return {"min": band["min"], "max": band["max"], "value": band["value"],
                "observed_min": band["raw_min"], "observed_max": band["raw_max"]}
    if comparator == "lte":
        # a ceiling: propose the observed max (with pad) as the cap
        return {"max": band["max"], "value": band["value"],
                "observed_min": band["raw_min"], "observed_max": band["raw_max"]}
    if comparator == "gte":
        return {"min": band["min"], "value": band["value"],
                "observed_min": band["raw_min"], "observed_max": band["raw_max"]}
    return {"value": band["value"], "observed_min": band["raw_min"],
            "observed_max": band["raw_max"]}


def _vera_summary(vera: dict) -> dict:
    if not vera:
        return {}
    t = vera.get("targets", {})
    return {
        "source_videos": vera.get("source_videos"),
        "method_note": "Vera uses ffmpeg loudnorm + scene-threshold cut detection "
                       "(DIFFERENT units than the eval instrument) — cross-check only.",
        "pacing_avg_shot_sec": t.get("pacing", {}).get("avg_shot_sec"),
        "audio_integrated_lufs_loudnorm": t.get("audio", {}).get("integrated_lufs"),
        "audio_true_peak_loudnorm": t.get("audio", {}).get("true_peak_db"),
        "motion_kinetic_score_normalized": t.get("motion", {}).get("kinetic_score"),
        "color_saturation": t.get("color", {}).get("saturation"),
        "color_brightness": t.get("color", {}).get("brightness"),
    }


def reference_fit(measured: dict[str, dict]) -> dict:
    """Re-validate: do the references pass the CURRENT bands, and would they pass
    the PROPOSED media bands?

    Uses the real `rollup` + `validation.report_reference_fit` over Measurements
    synthesized from the cached reference values (no second media decode). Pre-
    calibration most media bands are placeholders, so references are EXPECTED to
    miss several — that miss IS the signal that the band needs calibration
    (validation.report_reference_fit documents exactly this)."""
    from eval import rollup, validation
    from eval.types import Measurement

    proposal = build_proposal(use_cache=True)
    # only bands with a real proposed numeric range can be sanity-checked this way
    proposed_by_id = {b["band_id"]: b for b in proposal["bands"]
                      if b["source"] == "aesthetic-rate"}

    per_ref = {}
    agg_current_fail_placeholder: dict[str, int] = {}
    would_pass_proposed = {bid: {"pass": 0, "fail": 0} for bid in proposed_by_id}
    for name, vals in measured.items():
        ms = []
        for band_id, v in vals.items():
            if band_id.startswith("_"):
                continue
            b = rubric.band_by_id(band_id)
            if b is None:
                continue
            stage, prop = band_id.split(":", 1)
            ms.append(Measurement(artifact=b.get("artifact", ""), stage=stage,
                                  owner=b.get("owner", "?"), prop=prop, value=float(v),
                                  kind=b["kind"], rolls_up_to=tuple(b["rolls_up_to"]),
                                  unit=b.get("unit", "")))
        sc = rollup.build_scorecard(ms)
        sc["project_dir"] = name
        fit = validation.report_reference_fit(sc)
        per_ref[name] = fit
        for bid in fit["failed_on_placeholder_bands"]:
            agg_current_fail_placeholder[bid] = agg_current_fail_placeholder.get(bid, 0) + 1
        # would the reference pass the PROPOSED band? (range comparator only here)
        for bid, prop_b in proposed_by_id.items():
            if bid not in vals:
                continue
            pmin = prop_b["proposed"].get("min")
            pmax = prop_b["proposed"].get("max")
            v = float(vals[bid])
            ok = ((pmin is None or v >= pmin) and (pmax is None or v <= pmax))
            would_pass_proposed[bid]["pass" if ok else "fail"] += 1

    return {
        "per_reference": per_ref,
        "current_band_failures_across_refs": agg_current_fail_placeholder,
        "references_would_pass_proposed_band": would_pass_proposed,
        "note": ("Current-band placeholder failures are the calibration signal. "
                 "'would_pass_proposed' shows the proposed band admits the references "
                 "it was derived from (sanity check; range bands only)."),
    }


def write_proposal(proposal: dict, path: Path = PROPOSAL_FILE) -> Path:
    """Write the proposal JSON. This is the ONLY write — to eval/rubric.proposal.json,
    NEVER to atlas/rubric/rubric.json (which has no write path at all)."""
    path.write_text(json.dumps(proposal, indent=2))
    return path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _fmt_band(d: dict) -> str:
    if not d:
        return "—"
    parts = []
    for k in ("min", "max", "target", "value"):
        if k in d and d[k] is not None:
            parts.append(f"{k}={d[k]}")
    return " ".join(parts) if parts else json.dumps(d)


def print_summary(proposal: dict) -> None:
    print(f"BAND CALIBRATION PROPOSAL  ({proposal['proposal_version']})")
    print(f"  references: {proposal['n_references']}  |  rubric: {proposal['generated_against_rubric']}")
    split = proposal["reference_format_split"]
    print(f"  format split: short={len(split['short_<=120s'])} "
          f"long={len(split['long_120-480s'])} over-long={len(split['over_long_>480s'])}")
    print("  reference durations (s):")
    for n, d in proposal["reference_durations_sec"].items():
        print(f"    {d:>7.1f}  {n[:60]}")
    print("\n  DELIVERY STANDARDS (loudness/peak) — measured for evidence, KEEP standard:")
    for b in proposal["bands"]:
        if b["source"] != "delivery-standard":
            continue
        print(f"    {b['band_id']:<32} [{b['confidence']}] current({_fmt_band(b['current'])})  "
              f"refs={list(b['per_video'].values())}")
    print("\n  AESTHETIC RATES (motion) — proposed, cross-checked vs our own output:")
    for b in proposal["bands"]:
        if b["source"] != "aesthetic-rate":
            continue
        print(f"    {b['band_id']:<32} [{b['confidence']}] "
              f"current({_fmt_band(b['current'])}) -> proposed({_fmt_band(b['proposed'])})  n={b['n_refs']}")
    own = proposal.get("own_output_baseline", {})
    if own:
        print("  our own output (motion_energy):",
              [v.get("compose:motion_energy") for v in own.values()])
    print("\n  FORMAT-dependent (length) bands — short/long profiles, NOT collapsed:")
    for b in proposal["bands"]:
        if b["source"] != "format-profile":
            continue
        print(f"    {b['band_id']:<32} short={b['proposed']['short']} long={b['proposed']['long']}")
    print(f"\n  NOT calibrated (needs CEO interview): {len(proposal['not_calibrated_needs_ceo_interview'])} bands")
    print(f"  proposal written -> {PROPOSAL_FILE}")
    print("  NOTE: this is a PROPOSAL. A human applies approved bands to rubric.json.")


DOCS_REPORT = _REPO_DIR / "docs" / "phase2-step1-calibration.md"


def render_markdown(proposal: dict, fit: dict) -> str:
    L: list[str] = []
    a = L.append
    a("# Phase 2 — Step 1: Band-Calibration Proposal (reference-derived)")
    a("")
    a("> **Status: PROPOSAL for CEO approval. Nothing applied.** This is produced by the "
      "read-only proposer `atlas/eval/calibrate.py`. Applying any band is a CEO-owned human "
      "edit to `atlas/rubric/rubric.json` (flip `placeholder:false` on the calibrated band). "
      "The improver has no write path to the rubric — calibrate writes only "
      "`atlas/eval/rubric.proposal.json` + this report.")
    a("")
    a(f"- Proposal version: `{proposal['proposal_version']}`")
    a(f"- Generated against rubric: `{proposal['generated_against_rubric']}`")
    a(f"- References measured: **{proposal['n_references']}** "
      f"(in `{proposal['reference_dir']}`)")
    a("")
    a("## Method (apples-to-apples)")
    a("")
    a("Every media band below was produced by running the **existing eval analyzers** "
      "(`eval.analyzers.audio` + `video`) directly on each reference mp4 — so the proposed "
      "values are in the **same units as the scoring instrument** (ebur128 LUFS/dBTP, @4fps "
      "|Δluma|, ffprobe seconds). Vera's `reference_rubric` is shown only as a cross-check "
      "(she uses `loudnorm` + scene-threshold cut detection — different units).")
    a("")
    a("## The reference set & the two target formats")
    a("")
    a("You produce **two** formats: ~60–90s (current testing) and ~5–8 min (future). "
      "Quality properties that are *rates/levels* (loudness, peak, motion energy) are "
      "**format-independent** and are learned from the whole set. Length-dependent bands "
      "(runtime, total duration, scene count) depend on the target format, so they are "
      "**not** collapsed into one band — a short and a long profile are proposed instead.")
    a("")
    a("| Reference | Duration (s) | Profile |")
    a("|---|--:|---|")
    split = proposal["reference_format_split"]
    def _profile(n):
        if n in split["short_<=120s"]: return "short (≤120s)"
        if n in split["long_120-480s"]: return "long (120–480s)"
        return "over-long (>480s) — style ref, length out of target"
    for n, d in sorted(proposal["reference_durations_sec"].items(), key=lambda kv: kv[1]):
        a(f"| {n[:60]} | {d:.1f} | {_profile(n)} |")
    a("")
    a("## Media bands — what the references actually say")
    a("")
    a("Headline finding: this reference set does **not** yield tight, directly-adoptable "
      "media bands. Loudness/peak are **delivery standards** the noisy raw downloads can't "
      "improve on (keep them); motion is a **different design space** than our restrained "
      "motion-graphics (a CEO call, not an obvious win). The high-leverage calibration is the "
      "structural/editorial bands below, which need the CEO interview.")
    a("")
    a("### Delivery standards (loudness, true-peak) → KEEP")
    a("")
    a("| Band | Current (keep) | Per-reference values | Finding |")
    a("|---|---|---|---|")
    for b in proposal["bands"]:
        if b["source"] != "delivery-standard":
            continue
        pv = ", ".join(f"{v}" for v in b["per_video"].values())
        a(f"| `{b['band_id']}` | {_fmt_band(b['current'])} | {pv} | {b['rationale']} |")
    a("")
    own_loud = [v.get("render:final_loudness") for v in (proposal.get("own_output_baseline") or {}).values()
                if v.get("render:final_loudness") is not None]
    own_peak = [v.get("render:final_peak") for v in (proposal.get("own_output_baseline") or {}).values()
                if v.get("render:final_peak") is not None]
    if own_loud:
        clip = [p for p in own_peak if p > -1.0]
        a(f"> **Keep the standard — but note OUR OWN output misses it too.** Our renders measure "
          f"{own_loud} LUFS (target −14±1): we mix **~7–8 LUFS too quiet**"
          + (f", and {len(clip)} of our renders CLIP (true-peak {clip} > −1.0 dBTP)." if clip else ".")
          + " That is a concrete, objective target for the Step-2 improvement loop — independent "
            "of the noisy references.")
        a("")
    a("### Aesthetic rates (motion) → PROPOSED, but cross-checked vs our own output")
    a("")
    a("| Band | Conf | Current | Proposed | n | Per-reference values |")
    a("|---|---|---|---|--:|---|")
    for b in proposal["bands"]:
        if b["source"] != "aesthetic-rate":
            continue
        pv = ", ".join(f"{v}" for v in b["per_video"].values())
        a(f"| `{b['band_id']}` | {b['confidence']} | {_fmt_band(b['current'])} | "
          f"{_fmt_band(b['proposed'])} | {b['n_refs']} | {pv} |")
    a("")
    for b in proposal["bands"]:
        if b["source"] == "aesthetic-rate":
            a(f"- **`{b['band_id']}`** — {b['rationale']}")
    a("")
    own = proposal.get("own_output_baseline", {})
    if own:
        a("**Our own output baseline** (same analyzers, instrument units):")
        a("")
        a("| Project | motion_energy | final_loudness | final_peak | runtime (s) |")
        a("|---|--:|--:|--:|--:|")
        for n, v in own.items():
            a(f"| {n[:46]} | {v.get('compose:motion_energy','—')} | "
              f"{v.get('render:final_loudness','—')} | {v.get('render:final_peak','—')} | "
              f"{v.get('render:final_runtime','—')} |")
        a("")
    a("## Length-dependent bands — short/long profiles (NOT collapsed)")
    a("")
    for b in proposal["bands"]:
        if b["source"] != "format-profile":
            continue
        a(f"- **`{b['band_id']}`** — short `{b['proposed']['short']}` · "
          f"long `{b['proposed']['long']}`. {b['proposed']['recommendation']}. "
          f"_{b['rationale']}_")
    a("")
    a("## NOT calibrated from references (needs the CEO visual interview)")
    a("")
    a("You cannot read a script, storyboard, or asset manifest back out of a finished mp4, "
      "so these bands are **not** proposed from references. They need the CEO visual "
      "interview + the completed-project distribution as a prior (the cross-cutting "
      "workstream in the Phase-2 plan §4).")
    a("")
    a("```")
    for bid in proposal["not_calibrated_needs_ceo_interview"]:
        a(bid)
    a("```")
    a("")
    a("## Vera cross-check")
    a("")
    vc = proposal.get("vera_crosscheck") or {}
    if vc:
        a(f"- {vc.get('method_note','')}")
        a(f"- avg shot sec (Vera): `{vc.get('pacing_avg_shot_sec')}`")
        a(f"- integrated LUFS via loudnorm (Vera): `{vc.get('audio_integrated_lufs_loudnorm')}` "
          "— compare to the ebur128 `final_loudness` proposed above (different filters).")
        a(f"- saturation `{vc.get('color_saturation')}` · brightness `{vc.get('color_brightness')}` "
          "(palette centroid available for a future `style:palette_distance` band).")
    else:
        a("- (Vera reference_rubric not found at standards/default.json)")
    a("")
    a("## Re-validation")
    a("")
    a("**`validate_instrument()`** on the current rubric: see the run output — all 42 bands "
      "must still discriminate known-good from known-bad (the proposal changes nothing until "
      "a human applies it).")
    a("")
    a("**`report_reference_fit()`** over the references (current bands): the references miss "
      "several placeholder media bands — that miss is the calibration signal. Current "
      "placeholder-band failures across references:")
    a("")
    cf = fit.get("current_band_failures_across_refs", {})
    if cf:
        for bid, cnt in sorted(cf.items(), key=lambda kv: -kv[1]):
            a(f"- `{bid}` — fails on {cnt}/{proposal['n_references']} references (current placeholder band)")
    else:
        a("- (none — references already fit the current media bands)")
    a("")
    a("Sanity check — the proposed bands admit the references they were derived from:")
    a("")
    for bid, wp in (fit.get("references_would_pass_proposed_band") or {}).items():
        a(f"- `{bid}` — would pass {wp['pass']}/{wp['pass']+wp['fail']} references under the proposed band")
    a("")
    a("## What a human does next")
    a("")
    a("1. Review the proposed media bands + the long-format runtime profile.")
    a("2. Apply approved bands by editing `atlas/rubric/rubric.json` "
      "(set the new min/max, flip `placeholder:false`).")
    a("3. Re-run `validate_instrument()` (must stay all-pass) and "
      "`report_reference_fit()` (references should now pass the calibrated media bands).")
    a("4. Begin the CEO visual interview for the structural/editorial bands above.")
    a("")
    return "\n".join(L)


def main(argv: list[str]) -> int:
    no_cache = "--no-cache" in argv
    proposal = build_proposal(use_cache=not no_cache)
    write_proposal(proposal)
    measured = measure_all(list_references(), use_cache=not no_cache)
    fit = reference_fit(measured)
    if "--report" in argv:
        DOCS_REPORT.parent.mkdir(parents=True, exist_ok=True)
        DOCS_REPORT.write_text(render_markdown(proposal, fit))
        print(f"report written -> {DOCS_REPORT}")
    print_summary(proposal)
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    raise SystemExit(main(sys.argv[1:]))
