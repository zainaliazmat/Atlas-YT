"""TEXT / STRUCTURAL analyzer (Phase 1).

Pure JSON/structural measurement of one project's text artifacts — NO LLM, NO
ffmpeg/ffprobe. Reads script / style_guide / storyboard / asset_manifest /
narration transcript and emits raw ``Measurement``s for the properties this
module owns. The only media touch is reading raster pixel dimensions with cv2
for ``assets:min_resolution`` (still deterministic, no transcode).

Every measurement pulls its band METADATA (kind / rolls_up_to / unit / owner)
from ``rubric.band(stage, prop)`` — never a threshold. A property whose band the
rubric does not declare is skipped (it is not ours to invent). Nothing raises:
a missing artifact yields ``value=None`` + an ``error`` string.
"""
from __future__ import annotations

import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

import rubric
from eval.types import EvalContext, Measurement

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[A-Za-z0-9']+")


def _words(s: Any) -> list[str]:
    return _WORD.findall(s) if isinstance(s, str) else []


def _nonempty(s: Any) -> bool:
    return isinstance(s, str) and s.strip() != ""


def _b(stage: str, prop: str) -> Optional[dict]:
    """Band metadata for (stage, prop), or None if the rubric does not score it."""
    return rubric.band(stage, prop)


def _mk(artifact: str, stage: str, prop: str, value: Optional[float],
        *, detail: Optional[dict] = None, error: Optional[str] = None) -> Optional[Measurement]:
    """Build a Measurement, sourcing kind/rolls_up_to/unit/owner from the band.

    Returns None (skip) only if the rubric has no band for this property — every
    property listed in this module IS in the rubric, so this is defensive.
    """
    band = _b(stage, prop)
    if band is None:
        return None
    return Measurement(
        artifact=artifact,
        stage=stage,
        owner=band.get("owner", ""),
        prop=prop,
        value=value,
        kind=band.get("kind", "objective"),
        rolls_up_to=tuple(band.get("rolls_up_to", ())),
        unit=band.get("unit", ""),
        detail=detail or {},
        error=error,
    )


def _miss(artifact: str, stage: str, prop: str, err: str) -> Optional[Measurement]:
    return _mk(artifact, stage, prop, None, error=err)


def _add(out: list, m: Optional[Measurement]) -> None:
    if m is not None:
        out.append(m)


# ---------------------------------------------------------------------------
# SCRIPT
# ---------------------------------------------------------------------------

def _analyze_script(ctx: EvalContext, out: list) -> None:
    art = "script.json"
    s = ctx.script
    props = ["scene_count", "runtime_fit", "words_per_scene", "one_point_adherence",
             "claim_support_ratio", "info_density", "narrative_arc", "cta_quality",
             "on_screen_text_density"]
    if not isinstance(s, dict):
        for p in props:
            _add(out, _miss(art, "script", p, "script.json missing or unparseable"))
        return

    scenes = s.get("scenes") or []
    if not isinstance(scenes, list):
        scenes = []

    # scene_count
    n = len(scenes) if scenes else s.get("total_scenes")
    _add(out, _mk(art, "script", "scene_count",
                  float(n) if n is not None else None,
                  detail={"from_scenes": len(scenes),
                          "total_scenes": s.get("total_scenes")},
                  error=None if n is not None else "no scenes and no total_scenes"))

    # runtime_fit
    rt = s.get("est_runtime_sec")
    _add(out, _mk(art, "script", "runtime_fit",
                  float(rt) if isinstance(rt, (int, float)) else None,
                  detail={"est_runtime_sec": rt},
                  error=None if isinstance(rt, (int, float)) else "no est_runtime_sec"))

    # words_per_scene
    if scenes:
        counts = [len(_words(sc.get("narration"))) for sc in scenes]
        mean_wc = sum(counts) / len(counts)
        var = sum((c - mean_wc) ** 2 for c in counts) / len(counts)
        flags = [sc.get("scene_no", i + 1)
                 for i, (sc, c) in enumerate(zip(scenes, counts)) if c > 45 or c < 8]
        _add(out, _mk(art, "script", "words_per_scene", float(mean_wc),
                      detail={"variance": var, "flags": flags, "per_scene": counts}))
    else:
        _add(out, _miss(art, "script", "words_per_scene", "no scenes"))

    # one_point_adherence: fraction of scenes with exactly one non-empty `point`
    if scenes:
        ok = sum(1 for sc in scenes if _nonempty(sc.get("point")))
        _add(out, _mk(art, "script", "one_point_adherence", ok / len(scenes),
                      detail={"scenes_with_point": ok, "total": len(scenes)}))
    else:
        _add(out, _miss(art, "script", "one_point_adherence", "no scenes"))

    # claim_support_ratio
    claims = [c for sc in scenes for c in (sc.get("claims") or [])]
    total_claims = len(claims)
    if total_claims == 0:
        _add(out, _mk(art, "script", "claim_support_ratio", 1.0,
                      detail={"total_claims": 0,
                              "note": "no claims -> vacuously supported (1.0)"}))
    else:
        def _has_ref(c: dict) -> bool:
            ref = c.get("source_ref")
            # source_ref may legitimately be 0 (an index into sources[]); 0 is a
            # valid, non-empty reference. Only None / "" / [] count as unsupported.
            if ref is None:
                return False
            if isinstance(ref, str):
                return ref.strip() != ""
            if isinstance(ref, (list, tuple, dict)):
                return len(ref) > 0
            return True  # any non-empty scalar (incl. 0) is a reference
        supported = sum(1 for c in claims if _has_ref(c))
        _add(out, _mk(art, "script", "claim_support_ratio", supported / total_claims,
                      detail={"total_claims": total_claims, "supported": supported}))

    # info_density: claims per minute
    if isinstance(rt, (int, float)) and rt > 0:
        cpm = total_claims / (rt / 60.0)
        _add(out, _mk(art, "script", "info_density", float(cpm),
                      detail={"total_claims": total_claims, "est_runtime_sec": rt}))
    else:
        _add(out, _miss(art, "script", "info_density",
                        "no positive est_runtime_sec for claims-per-minute"))

    # narrative_arc: hook AND cta present
    hook_ok = _nonempty(s.get("hook"))
    cta_ok = _nonempty(s.get("cta"))
    _add(out, _mk(art, "script", "narrative_arc", 1.0 if (hook_ok and cta_ok) else 0.0,
                  detail={"hook": hook_ok, "cta": cta_ok}))

    # cta_quality: cta present
    _add(out, _mk(art, "script", "cta_quality", 1.0 if cta_ok else 0.0,
                  detail={"cta_present": cta_ok}))

    # on_screen_text_density: MAX chars across on_screen_text
    osts = [sc.get("on_screen_text") for sc in scenes if _nonempty(sc.get("on_screen_text"))]
    if scenes:
        lengths = [len(t) for t in osts]
        mx = max(lengths) if lengths else 0
        over = sum(1 for L in lengths if L > 45)
        _add(out, _mk(art, "script", "on_screen_text_density", float(mx),
                      detail={"over_45": over, "count": len(lengths)}))
    else:
        _add(out, _miss(art, "script", "on_screen_text_density", "no scenes"))


# ---------------------------------------------------------------------------
# STYLE
# ---------------------------------------------------------------------------

def _analyze_style(ctx: EvalContext, out: list) -> None:
    art = "style_guide.json"
    sg = ctx.style_guide
    props = ["signature_present", "type_in_system", "motion_budget_sane", "palette_distance"]
    if not isinstance(sg, dict):
        for p in props:
            _add(out, _miss(art, "style", p, "style_guide.json missing or unparseable"))
        return

    palette = sg.get("palette") or {}
    sig = palette.get("signature_highlight")
    sig_ok = isinstance(sig, str) and sig.strip().upper() == "#FFD000"
    _add(out, _mk(art, "style", "signature_present", 1.0 if sig_ok else 0.0,
                  detail={"signature_highlight": sig}))

    # type_in_system: rule (phase-1) = 1.0 iff typography declares >=1 font family
    # and no declared entry has an empty/blank family; else 0.0.
    typo = sg.get("typography") or {}
    families = []
    empty = False
    for k, v in typo.items():
        if isinstance(v, dict) and "family" in v:
            fam = v.get("family")
            if _nonempty(fam):
                families.append(fam)
            else:
                empty = True
    type_ok = (len(families) >= 1 and not empty)
    _add(out, _mk(art, "style", "type_in_system", 1.0 if type_ok else 0.0,
                  detail={"families": families, "any_empty": empty,
                          "rule": ">=1 declared font family and no empty family entry"}))

    # motion_budget_sane: rule = a motion section exists AND declares a numeric
    # per-scene budget within sane bounds [1, 8]; else 0.0.
    motion = sg.get("motion")
    if isinstance(motion, dict):
        budget = motion.get("max_per_scene")
        sane = isinstance(budget, (int, float)) and 1 <= budget <= 8
        _add(out, _mk(art, "style", "motion_budget_sane", 1.0 if sane else 0.0,
                      detail={"max_per_scene": budget,
                              "rule": "motion.max_per_scene numeric in [1,8]"}))
    else:
        _add(out, _mk(art, "style", "motion_budget_sane", 0.0,
                      detail={"rule": "no motion section declared"}))

    # palette_distance: info-only in phase 1 (no reference centroid). value=None.
    _add(out, _mk(art, "style", "palette_distance", None,
                  detail={"note": "recorded-only; needs reference palette (phase 1)"},
                  error="recorded-only; no reference palette in phase 1"))


# ---------------------------------------------------------------------------
# STORYBOARD
# ---------------------------------------------------------------------------

def _effect_names(effects: Any) -> list[str]:
    names = []
    for e in (effects or []):
        if isinstance(e, str):
            names.append(e)
        elif isinstance(e, dict):
            nm = e.get("name")
            if isinstance(nm, str):
                names.append(nm)
    return names


def _analyze_storyboard(ctx: EvalContext, out: list) -> None:
    art = "storyboard.json"
    sb = ctx.storyboard
    props = ["layout_variety", "effect_discipline", "transition_character",
             "shot_specificity", "signature_beat_placement"]
    if not isinstance(sb, dict):
        for p in props:
            _add(out, _miss(art, "storyboard", p, "storyboard.json missing or unparseable"))
        return

    scenes = sb.get("scenes") or []
    if not isinstance(scenes, list):
        scenes = []
    if not scenes:
        for p in props:
            _add(out, _miss(art, "storyboard", p, "no storyboard scenes"))
        return

    # layout_variety: normalized Shannon entropy H / log(k)
    layouts = [sc.get("layout") for sc in scenes if _nonempty(sc.get("layout"))]
    dist: dict[str, int] = {}
    for L in layouts:
        dist[L] = dist.get(L, 0) + 1
    k = len(dist)
    total = sum(dist.values())
    if k <= 1 or total == 0:
        variety = 0.0
    else:
        H = -sum((c / total) * math.log(c / total) for c in dist.values())
        variety = H / math.log(k)
    max_share = (max(dist.values()) / total) if total else 0.0
    _add(out, _mk(art, "storyboard", "layout_variety", float(variety),
                  detail={"distribution": dist, "max_share": max_share,
                          "distinct_layouts": k}))

    # effect_discipline: COUNT of highlighter-FFD000 effects across all scenes (rule: exactly 1)
    hl_scenes = []
    hl_count = 0
    for sc in scenes:
        for nm in _effect_names(sc.get("effects")):
            if nm == "highlighter-FFD000":
                hl_count += 1
                hl_scenes.append(sc.get("scene_no"))
    _add(out, _mk(art, "storyboard", "effect_discipline", float(hl_count),
                  detail={"scenes": hl_scenes, "rule": "exactly one highlighter-FFD000"}))

    # transition_character: fraction of transitions that are hard "cut"
    transitions = [sc.get("transition") for sc in scenes]
    tdist: dict[str, int] = {}
    for t in transitions:
        key = t if _nonempty(t) else "(none)"
        tdist[key] = tdist.get(key, 0) + 1
    cuts = tdist.get("cut", 0)
    match_cuts = tdist.get("match-cut", 0) + tdist.get("match_cut", 0)
    _add(out, _mk(art, "storyboard", "transition_character",
                  cuts / len(transitions) if transitions else None,
                  detail={"distribution": tdist, "match_cut_count": match_cuts,
                          "cut_count": cuts, "total": len(transitions)}))

    # shot_specificity: fraction of shots with a concrete asset_ref OR non-empty content
    shots = [sh for sc in scenes for sh in (sc.get("shots") or [])]
    if shots:
        spec = sum(1 for sh in shots
                   if _nonempty(sh.get("asset_ref")) or _nonempty(sh.get("content")))
        _add(out, _mk(art, "storyboard", "shot_specificity", spec / len(shots),
                      detail={"specific": spec, "total_shots": len(shots)}))
    else:
        _add(out, _miss(art, "storyboard", "shot_specificity", "no shots"))

    # signature_beat_placement: exactly one signature_beat=true scene, not first/last
    beat_scenes = [sc.get("scene_no") for sc in scenes if sc.get("signature_beat") is True]
    scene_nos = [sc.get("scene_no") for sc in scenes]
    first, last = (scene_nos[0], scene_nos[-1]) if scene_nos else (None, None)
    placement_ok = (len(beat_scenes) == 1 and beat_scenes[0] not in (first, last))
    _add(out, _mk(art, "storyboard", "signature_beat_placement",
                  1.0 if placement_ok else 0.0,
                  detail={"beat_scene": beat_scenes, "first": first, "last": last}))


# ---------------------------------------------------------------------------
# ASSETS
# ---------------------------------------------------------------------------

_CLEARED_OK = {"cleared", "sourced"}


def _analyze_assets(ctx: EvalContext, out: list) -> None:
    art = "asset_manifest.json"
    am = ctx.asset_manifest
    props = ["placeholder_rate", "clearance_rate", "relevance_score", "min_resolution"]
    if not isinstance(am, dict):
        for p in props:
            _add(out, _miss(art, "assets", p, "asset_manifest.json missing or unparseable"))
        return
    assets = am.get("assets") or []
    if not isinstance(assets, list) or not assets:
        for p in props:
            _add(out, _miss(art, "assets", p, "no assets in manifest"))
        return

    n = len(assets)

    # placeholder_rate
    ph = sum(1 for a in assets if a.get("status") == "placeholder")
    _add(out, _mk(art, "assets", "placeholder_rate", ph / n,
                  detail={"placeholders": ph, "total": n}))

    # clearance_rate: not-unclearable / total. Rule: cleared-ok = status in
    # {cleared, sourced}. unclearable = status == placeholder (license/clearance
    # forced it to placeholder). sourced = flagged for human review but usable.
    not_unclearable = sum(1 for a in assets if a.get("status") in _CLEARED_OK)
    _add(out, _mk(art, "assets", "clearance_rate", not_unclearable / n,
                  detail={"cleared_ok": not_unclearable, "total": n,
                          "rule": "cleared-ok = status in {cleared, sourced}; "
                                  "unclearable = placeholder"}))

    _relevance(ctx, assets, out)
    _min_resolution(ctx, assets, out)


def _relevance(ctx: EvalContext, assets: list, out: list) -> None:
    """assets:relevance_score — reuse the asset-sourcer relevance machinery.

    Build a ``Query`` from the storyboard shot's subject text (via
    se.derive_query) and a ``Candidate`` whose searchable text is the asset's
    own identity (asset_id + uri filename tokens). se.relevance then scores how
    much of the shot's SUBJECT survives in the asset's identity — a deterministic
    proxy for "is this asset about what the shot needs?". value = MEAN over
    non-placeholder assets. Falls back to a token-overlap proxy if the engine
    can't be imported.
    """
    art = "asset_manifest.json"
    # map scene_no -> first shot content (subject text), from storyboard
    shot_by_scene: dict[Any, str] = {}
    sb = ctx.storyboard
    if isinstance(sb, dict):
        for sc in (sb.get("scenes") or []):
            shots = sc.get("shots") or []
            content = ""
            for sh in shots:
                if _nonempty(sh.get("content")):
                    content = sh["content"]
                    break
            shot_by_scene[sc.get("scene_no")] = content

    non_ph = [a for a in assets if a.get("status") != "placeholder"]
    if not non_ph:
        _add(out, _miss(art, "assets", "relevance_score",
                        "no non-placeholder assets to score"))
        return

    def _filename_tokens(a: dict) -> str:
        uri = a.get("uri") or ""
        stem = os.path.splitext(os.path.basename(uri))[0]
        aid = a.get("asset_id") or ""
        return f"{aid} {stem}".replace("_", " ").replace("-", " ")

    method = ""
    error = None
    se = None
    try:
        repo_root = ctx.dir.resolve().parents[1]  # .../atlas/projects/<proj> -> repo root
        # robustly find repo root containing asset-sourcer
        cand_root = ctx.dir.resolve()
        for _ in range(6):
            if (cand_root / "asset-sourcer").is_dir():
                repo_root = cand_root
                break
            cand_root = cand_root.parent
        as_path = str(repo_root / "asset-sourcer")
        if as_path not in sys.path:
            sys.path.insert(0, as_path)
        import source_engine as _se  # noqa
        se = _se
    except Exception as e:  # asset-sourcer unimportable
        se = None
        error = f"asset-sourcer unimportable: {type(e).__name__}: {e}"

    scores: dict[str, float] = {}
    if se is not None:
        method = "source_engine.relevance(derive_query(shot.content) x Candidate(asset identity))"
        try:
            from sources import Candidate  # type: ignore
            for a in non_ph:
                content = shot_by_scene.get(a.get("scene_no"), "") or (a.get("asset_id") or "")
                q = se.derive_query(content, ctx.style_guide if isinstance(ctx.style_guide, dict) else {})
                identity = _filename_tokens(a)
                cand = Candidate(source="openverse", title=identity, author="",
                                 source_url="", license_raw="", download_url="",
                                 extra={"description": identity})
                scores[a.get("asset_id")] = float(se.relevance(q, cand))
        except Exception as e:
            se = None
            error = f"source_engine relevance failed: {type(e).__name__}: {e}"
            scores = {}

    if se is None:
        # documented token-overlap fallback proxy
        method = "token-overlap fallback: shot.content tokens vs asset_id/uri-filename tokens"
        for a in non_ph:
            content = shot_by_scene.get(a.get("scene_no"), "") or ""
            q_tokens = {w.lower() for w in _words(content)}
            id_tokens = {w.lower() for w in _words(_filename_tokens(a))}
            if not q_tokens or not id_tokens:
                scores[a.get("asset_id")] = 0.0
            else:
                scores[a.get("asset_id")] = len(q_tokens & id_tokens) / len(id_tokens)

    if not scores:
        _add(out, _miss(art, "assets", "relevance_score",
                        error or "could not score relevance"))
        return
    mean = sum(scores.values()) / len(scores)
    _add(out, _mk(art, "assets", "relevance_score", float(mean),
                  detail={"method": method, "per_asset": scores,
                          "scored": len(scores)},
                  error=None))


def _min_resolution(ctx: EvalContext, assets: list, out: list) -> None:
    art = "asset_manifest.json"
    try:
        import cv2  # noqa
    except Exception as e:
        _add(out, _miss(art, "assets", "min_resolution",
                        f"cv2 unavailable: {type(e).__name__}: {e}"))
        return

    raster_exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
    dims: dict[str, list] = {}
    unreadable: list[str] = []
    short_sides: list[int] = []
    for a in assets:
        uri = a.get("uri") or ""
        ext = os.path.splitext(uri)[1].lower()
        if ext not in raster_exts:
            continue
        p = ctx.dir / uri
        if not p.is_file():
            unreadable.append(a.get("asset_id"))
            continue
        try:
            img = cv2.imread(str(p))
        except Exception:
            img = None
        if img is None:
            unreadable.append(a.get("asset_id"))
            continue
        h, w = img.shape[:2]
        dims[a.get("asset_id")] = [w, h]
        short_sides.append(min(w, h))

    if not short_sides:
        _add(out, _miss(art, "assets", "min_resolution",
                        "no readable raster assets under project dir"))
        return
    _add(out, _mk(art, "assets", "min_resolution", float(min(short_sides)),
                  detail={"dims": dims, "unreadable": unreadable,
                          "readable": len(short_sides)}))


# ---------------------------------------------------------------------------
# NARRATION
# ---------------------------------------------------------------------------

def _analyze_narration(ctx: EvalContext, out: list) -> None:
    art = "audio/narration.transcript.json"
    tr = ctx.transcript
    props = ["speech_cadence", "pause_structure", "scene_timing_fit", "total_duration_fit"]
    if not isinstance(tr, dict):
        for p in props:
            _add(out, _miss(art, "narration", p, "narration transcript missing or unparseable"))
        return

    segs = tr.get("segments") or []
    if not isinstance(segs, list):
        segs = []
    total_dur = tr.get("total_duration_sec")

    # total_duration_fit
    _add(out, _mk(art, "narration", "total_duration_fit",
                  float(total_dur) if isinstance(total_dur, (int, float)) else None,
                  detail={"total_duration_sec": total_dur},
                  error=None if isinstance(total_dur, (int, float)) else "no total_duration_sec"))

    # speech_cadence: overall wpm
    if segs and isinstance(total_dur, (int, float)) and total_dur > 0:
        total_words = sum(len(_words(s.get("text"))) for s in segs)
        wpm = total_words / (total_dur / 60.0)
        flags = []
        for s in segs:
            dur = None
            try:
                dur = float(s.get("end_sec")) - float(s.get("start_sec"))
            except (TypeError, ValueError):
                dur = None
            if dur and dur > 0:
                w = len(_words(s.get("text")))
                pw = w / (dur / 60.0)
                if pw > 185 or pw < 110:
                    flags.append(s.get("scene_no"))
        _add(out, _mk(art, "narration", "speech_cadence", float(wpm),
                      detail={"total_words": total_words, "per_scene_flags": flags}))
    else:
        _add(out, _miss(art, "narration", "speech_cadence",
                        "no segments or no positive total_duration_sec"))

    # pause_structure: mean gap between consecutive segments, clamped >=0
    if len(segs) >= 2:
        gaps = []
        ordered = sorted(segs, key=lambda s: (s.get("start_sec") if isinstance(s.get("start_sec"), (int, float)) else 0))
        for prev, nxt in zip(ordered, ordered[1:]):
            try:
                g = float(nxt.get("start_sec")) - float(prev.get("end_sec"))
            except (TypeError, ValueError):
                continue
            gaps.append(max(0.0, g))
        if gaps:
            _add(out, _mk(art, "narration", "pause_structure", sum(gaps) / len(gaps),
                          detail={"gaps": gaps, "n_gaps": len(gaps)}))
        else:
            _add(out, _miss(art, "narration", "pause_structure", "no measurable gaps"))
    else:
        _add(out, _miss(art, "narration", "pause_structure", "fewer than 2 segments"))

    # scene_timing_fit: MAX abs(narrated dur - script duration_est_sec) by scene_no
    script = ctx.script
    if not isinstance(script, dict):
        _add(out, _miss(art, "narration", "scene_timing_fit",
                        "script.json missing; cannot match scene durations"))
    else:
        est_by_scene: dict[Any, float] = {}
        for sc in (script.get("scenes") or []):
            d = sc.get("duration_est_sec")
            if isinstance(d, (int, float)):
                est_by_scene[sc.get("scene_no")] = float(d)
        diffs = {}
        for s in segs:
            sn = s.get("scene_no")
            if sn in est_by_scene:
                try:
                    nd = float(s.get("end_sec")) - float(s.get("start_sec"))
                except (TypeError, ValueError):
                    continue
                diffs[sn] = abs(nd - est_by_scene[sn])
        if diffs:
            worst = max(diffs, key=diffs.get)
            _add(out, _mk(art, "narration", "scene_timing_fit", float(diffs[worst]),
                          detail={"worst_scene": worst, "per_scene": diffs}))
        else:
            _add(out, _miss(art, "narration", "scene_timing_fit",
                            "no scene_no matches between transcript and script"))


# ---------------------------------------------------------------------------
# RENDER (caption_sync only — text-derived, graceful skip)
# ---------------------------------------------------------------------------

def _analyze_render(ctx: EvalContext, out: list) -> None:
    art = "audio/narration.transcript.json"
    tr = ctx.transcript
    # caption_sync needs word-level timing (a "words" array with start/end on segments).
    has_word_timing = False
    if isinstance(tr, dict):
        for s in (tr.get("segments") or []):
            words = s.get("words")
            if isinstance(words, list) and words and isinstance(words[0], dict) \
                    and ("start" in words[0] or "start_sec" in words[0]):
                has_word_timing = True
                break
    if has_word_timing:
        # phase-1 text analyzer does not compute the alignment itself; record as
        # measurable-but-deferred. (Real sync lives in the av analyzer.)
        _add(out, _mk(art, "render", "caption_sync", None,
                      detail={"note": "word timing present; caption_sync computed by AV analyzer"},
                      error="word timing present but sync not computed in text analyzer"))
    else:
        _add(out, _mk(art, "render", "caption_sync", None,
                      detail={"note": "no whisper word-timing present; skipped"},
                      error="no whisper word-timing present; skipped"))


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def analyze(ctx: EvalContext) -> list[Measurement]:
    """Measure every text/structural property this module owns. Never raises."""
    out: list[Measurement] = []
    for fn in (_analyze_script, _analyze_style, _analyze_storyboard,
               _analyze_assets, _analyze_narration, _analyze_render):
        try:
            fn(ctx, out)
        except Exception as e:  # graceful: a single section failing must not crash all
            # We don't know which props the section owns here, so record nothing more
            # than is already added; the section-level guards above should prevent this.
            # As a last resort, surface the failure on the section's artifact.
            out.append(Measurement(
                artifact="(analyzer)", stage="text", owner="", prop=fn.__name__,
                value=None, kind="objective", rolls_up_to=(),
                error=f"section crashed: {type(e).__name__}: {e}"))
    return out
