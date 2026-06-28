"""studio.review.critics — the SEVEN lenses of the in-loop multi-critic review.

This is the automated port of the PRODUCTION_BIBLE's *Prompt 5.0 — multi-agent critique
(find what we missed)*. Where the bible runs seven critics by hand at the end, this runs
them IN-LOOP on every draft render, each an INDEPENDENT Claude vision call (the CEO's
"1A" choice) so a specialist lens rarely misses something in its lane and no two critics
nod along. Every critic argues from the SAME shared, measured ``evidence`` pack
(``studio.review.evidence``) — findings must be grounded in the numbers/frames, never
vibes.

The seven lenses (verbatim intent from Prompt 5.0 + this task's brief):
  1. motion      — pacing vs VO/beats, easing, residual dead air/trailing static,
                   transition strength, scenes that drag or rush.
  2. narrative   — hook in first 3s, clarity, the dark-truth→empowerment arc,
                   redundancy, whether the CTA lands, weak lines.
  3. brand       — fidelity to the chosen pack's DESIGN.md (color/type/texture/motion).
  4. legibility  — text on long enough to read, contrast vs texture, caption overlap,
                   safe margins.
  5. engagement  — drop-off risk, first-frame stopping power, where a viewer bails.
  6. technical   — determinism (Math.random/Date.now/fetch), window.__timelines
                   registration, seekability, + the audio mix (loudness/clipping).
  7. fact        — quotes verbatim + attributed, fairness footnotes present, no
                   fabricated quotes, no real-person likeness.

Each critic returns a JSON list of findings; we normalise severity, tag the lens, and
hand the union to ``studio.review.synthesize``. The vision seam is injectable so the
whole pass is offline-testable with a fake. Critics run in parallel threads (each call
is a blocking subscription request); one lens raising degrades to ``[]`` for that lens,
never crashing the review.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

SEVERITIES = ("Blocker", "Major", "Minor", "Nit")
EFFORTS = ("S", "M", "L")


# ======================================================================
# deterministic technical scan — facts the technical critic reasons over
# ======================================================================
def technical_scan(html: str) -> dict:
    """Grep the composition source for the determinism + seekability invariants the
    engine depends on. Pure + testable — the technical critic is handed these FACTS so
    its verdict is grounded, not guessed.

      - nondeterminism: Math.random / Date.now / new Date() / fetch( / XMLHttpRequest
        (any of these make a render non-byte-stable or network-dependent);
      - timeline registration: a ``window.__timelines[...] = `` assignment (HyperFrames
        needs the master timeline registered to drive + seek the render);
      - gsap presence (motion runtime) as a sanity signal.
    """
    patterns = {
        "math_random": r"Math\.random\s*\(",
        "date_now": r"Date\.now\s*\(",
        "new_date": r"new\s+Date\s*\(",
        "fetch": r"\bfetch\s*\(",
        "xhr": r"XMLHttpRequest",
    }
    nondeterminism = {}
    for name, pat in patterns.items():
        hits = len(re.findall(pat, html))
        if hits:
            nondeterminism[name] = hits
    return {
        "nondeterminism": nondeterminism,
        "registers_timeline": bool(re.search(r"window\.__timelines\s*\[[^\]]+\]\s*=", html)),
        "uses_gsap": "gsap" in html.lower(),
        "html_chars": len(html),
    }


# ======================================================================
# lens definitions
# ======================================================================
def _scene_digest(evidence: dict) -> str:
    """A compact per-scene table the critics read: window, duration, motion verdict,
    on-screen text, narration — the measured spine every finding must cite."""
    lines = []
    for s in evidence.get("scenes", []):
        m = s.get("motion") or {}
        flags = ",".join(m.get("flags", [])) or "-"
        lines.append(
            f"  scene {s['scene_no']}: {s['start']:.1f}-{s['cut']:.1f}s "
            f"({s['duration_sec']:.1f}s) motion={m.get('motion_energy', '?')} "
            f"tail_static={m.get('trailing_static_sec', '?')}s @cut={'hot' if m.get('animating_at_cut') else '-'} "
            f"flags=[{flags}]\n"
            f"      on-screen: {s.get('on_screen_text', '')!r}\n"
            f"      narration: {s.get('narration', '')!r}")
    return "\n".join(lines)


def _loudness_digest(evidence: dict) -> str:
    ld = evidence.get("loudness") or {}
    return (f"integrated={ld.get('integrated_lufs')} LUFS, "
            f"true_peak={ld.get('true_peak_dbtp')} dBTP, "
            f"clipping={ld.get('clipping')} (target ≈ −14 LUFS, ceiling −1 dBTP)")


_JSON_INSTRUCTION = (
    "Reply with ONLY a JSON array (no prose, no fences). Each element is an object:\n"
    '  {"severity": "Blocker|Major|Minor|Nit", "scene": <int or null>, '
    '"issue": "<what is wrong>", "evidence": "<cite the measured number or what the '
    'frame shows>", "fix": "<concrete, scene-scoped change>", "effort": "S|M|L"}\n'
    "Severity: Blocker = ships broken/unwatchable; Major = clearly hurts the video; "
    "Minor = polish; Nit = optional. Return [] if the lens is clean. Ground EVERY "
    "finding in the evidence or the frames — no speculation.")


def _lens_motion(evidence: dict) -> tuple[str, str, list[str]]:
    system = ("You are the MOTION & TIMING critic for a premium motion-graphics studio. "
              "Judge pacing against the narration beats, easing quality, residual dead "
              "air / trailing static holds, transition strength, and scenes that drag or "
              "rush. " + _JSON_INSTRUCTION)
    user = ("Per-scene measured motion (frame-diff energy; tail_static = frozen seconds "
            "before the cut; @cut hot = a move chopped mid-flight):\n"
            f"{_scene_digest(evidence)}\n\n"
            f"whole-render: {evidence.get('global')}\n"
            "The attached frames are scene midpoints. Flag any scene that looks frozen, "
            "drags, rushes, or whose motion stops before its cut.")
    return system, user, _mid_frames(evidence)


def _lens_narrative(evidence: dict) -> tuple[str, str, list[str]]:
    sc = evidence.get("script") or {}
    system = ("You are the NARRATIVE & SCRIPT critic. Judge hook strength in the first 3 "
              "seconds, clarity, the dark-truth→empowerment arc, redundancy, weak lines, "
              "and whether the CTA lands. " + _JSON_INSTRUCTION)
    user = (f"HOOK: {sc.get('hook')!r}\nCTA: {sc.get('cta')!r}\n\n"
            f"Scene narration + on-screen text in order:\n{_scene_digest(evidence)}\n\n"
            "Frames attached are the opening scenes. Is the first 3s a real hook? Does "
            "the arc build and the CTA pay off?")
    return system, user, _mid_frames(evidence, limit=3)


def _lens_brand(evidence: dict) -> tuple[str, str, list[str]]:
    system = ("You are the BRAND FIDELITY critic. Judge the frames against the pack's "
              "DESIGN.md: color tokens (warm cream paper, near-black ink, forest "
              "spray-green accent), type system (grunge display / slab / mono), grunge "
              "speckle texture, halftone imagery, and the motion principles. Flag any "
              "off-palette color, wrong/again-default font, missing texture, or generic "
              '"AI slop" look. ' + _JSON_INSTRUCTION)
    user = (f"Pack: {evidence.get('reference')}. "
            "Attached frames span the video. Where does it drift from the design "
            "system or look templated/generic rather than the grunge editorial bar?")
    return system, user, _mid_frames(evidence)


def _lens_legibility(evidence: dict) -> tuple[str, str, list[str]]:
    system = ("You are the LEGIBILITY critic. Judge whether text is on screen long enough "
              "to read at its length, contrast of text against the grunge texture, caption "
              "overlap/collision with other elements, and title-safe margins. "
              + _JSON_INSTRUCTION)
    user = ("Per-scene on-screen text + how long it is up:\n"
            f"{_scene_digest(evidence)}\n\n"
            "Attached frames are scene midpoints + transition frames. Flag any text that "
            "is too small, low-contrast, clipped at the edge, collides, or flips away "
            "before it can be read.")
    return system, user, _all_frames(evidence)


def _lens_engagement(evidence: dict) -> tuple[str, str, list[str]]:
    system = ("You are the ENGAGEMENT / RETENTION critic. Judge first-frame stopping "
              "power and where a viewer is most likely to drop off (a flat stretch, a "
              "slow open, a saggy middle). " + _JSON_INSTRUCTION)
    user = (f"Render duration: {evidence.get('render_duration_sec')}s. "
            f"polish-vs-reference rate: {(evidence.get('polish_vs_reference') or {}).get('rate')}.\n"
            f"{_scene_digest(evidence)}\n\n"
            "The FIRST attached frame is the opening — does it stop the scroll? Then flag "
            "the highest drop-off risk moments.")
    return system, user, _mid_frames(evidence)


def _lens_technical(evidence: dict) -> tuple[str, str, list[str]]:
    scan = technical_scan(evidence.get("index_html", ""))
    system = ("You are the TECHNICAL DETERMINISM + AUDIO-MIX critic. A render must be "
              "byte-stable and seek-safe: NO Math.random / Date.now / new Date / fetch / "
              "XMLHttpRequest in the composition, and the master timeline MUST be "
              "registered on window.__timelines. Also judge the audio mix. "
              + _JSON_INSTRUCTION)
    user = (f"Static scan of index.html: {scan}\n"
            f"Audio mix: {_loudness_digest(evidence)}\n\n"
            "Any nondeterminism hit is at least a Major (Blocker if it breaks "
            "seek-stability). A missing timeline registration is a Blocker. Clipping or "
            "loudness far from −14 LUFS is a Major.")
    return system, user, []


def _lens_fact(evidence: dict) -> tuple[str, str, list[str]]:
    sc = evidence.get("script") or {}
    scenes = sc.get("scenes") or []
    claims = [{"scene": s.get("scene_no"), "narration": s.get("narration"),
               "on_screen_text": s.get("on_screen_text"), "claims": s.get("claims", [])}
              for s in scenes]
    system = ("You are the FACT & COMPLIANCE critic. Verify: any quote shown is verbatim "
              "AND attributed to a real source; fairness/footnote context is present where "
              "a claim needs it; NO fabricated quotes; NO real-person likeness used "
              "unfairly or without basis. " + _JSON_INSTRUCTION)
    user = ("Script claims + on-screen text per scene:\n"
            f"{claims}\n\n"
            "Attached frames let you check what text is actually shown. Flag any quote "
            "without attribution, any unsupported statistic stated as fact, any fabricated "
            "quote, or any real person depicted without fair basis.")
    return system, user, _all_frames(evidence, limit=8)


LENSES = [
    {"key": "motion", "title": "Motion & Timing", "build": _lens_motion},
    {"key": "narrative", "title": "Narrative & Script", "build": _lens_narrative},
    {"key": "brand", "title": "Brand Fidelity", "build": _lens_brand},
    {"key": "legibility", "title": "Legibility", "build": _lens_legibility},
    {"key": "engagement", "title": "Engagement / Retention", "build": _lens_engagement},
    {"key": "technical", "title": "Technical Determinism + Audio", "build": _lens_technical},
    {"key": "fact", "title": "Fact & Compliance", "build": _lens_fact},
]


# ======================================================================
# frame selectors
# ======================================================================
def _mid_frames(evidence: dict, limit: int | None = None) -> list[str]:
    paths = [f["path"] for f in evidence.get("frames", [])
             if f.get("kind") == "mid" and f.get("path")]
    return paths[:limit] if limit else paths


def _all_frames(evidence: dict, limit: int | None = None) -> list[str]:
    paths = [f["path"] for f in evidence.get("frames", []) if f.get("path")]
    return paths[:limit] if limit else paths


# ======================================================================
# run
# ======================================================================
def _normalize(raw, lens_key: str) -> list[dict]:
    """Coerce one critic's parsed reply into a clean list of findings tagged with the
    lens. Tolerates a bare object, a {"findings": [...]} wrapper, or junk → []."""
    if isinstance(raw, dict):
        raw = raw.get("findings") or raw.get("issues") or [raw]
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sev = str(item.get("severity", "Minor")).title()
        if sev not in SEVERITIES:
            sev = "Minor"
        eff = str(item.get("effort", "M")).upper()
        if eff not in EFFORTS:
            eff = "M"
        scene = item.get("scene")
        try:
            scene = int(scene) if scene is not None else None
        except (TypeError, ValueError):
            scene = None
        issue = str(item.get("issue", "")).strip()
        if not issue:
            continue
        out.append({"lens": lens_key, "severity": sev, "scene": scene,
                    "issue": issue, "evidence": str(item.get("evidence", "")).strip(),
                    "fix": str(item.get("fix", "")).strip(), "effort": eff})
    return out


def run_one_critic(lens: dict, evidence: dict, vision_fn) -> list[dict]:
    """Run a single lens end-to-end: build its grounded prompt, call the vision seam,
    parse + normalise. Never raises — a failure yields [] for that lens."""
    from .vision import extract_json
    try:
        system, user, images = lens["build"](evidence)
        reply = vision_fn(system, user, images)
        return _normalize(extract_json(reply), lens["key"])
    except Exception:  # noqa: BLE001
        return []


def run_critics(evidence: dict, *, vision_fn=None, lenses=None, max_workers: int = 7) -> list[dict]:
    """Run all seven lenses in parallel and return the UNION of their findings (still
    un-deduped — that's synthesize's job). ``vision_fn`` defaults to the subscription
    vision seam; inject a fake for offline tests."""
    if vision_fn is None:
        from .vision import vision_chat as vision_fn
    lenses = lenses or LENSES
    findings: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(run_one_critic, lens, evidence, vision_fn): lens
                for lens in lenses}
        for fut in as_completed(futs):
            findings.extend(fut.result())
    return findings
