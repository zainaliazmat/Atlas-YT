# Quality Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `studio/gate/` — a scorecard that scores a draft render 0–5 per dimension with actionable diagnostics, runs a hard determinism+compliance self-check, BLOCKS publish below bar, and is calibrated to score our flat draft LOW and the hand-crafted reference HIGH.

**Architecture:** A new `studio/gate/` package layered on the existing `studio/review/evidence.py` evidence pack (no forking). Each scored dimension is a **pure function over the evidence dict** → a `DimResult`, so dimensions unit-test with a synthetic evidence dict (no rendering). `compliance.py` returns hard pass/fail checks. `scorecard.py` combines dims + compliance into a verdict + specific reasons. The public `gate.score()` accepts a project `slug` (via `collect_evidence`) OR explicit `(video, index_html, script)` paths (for the twin-less reference). Wired into `studio/pipeline.py`'s final gate as the publish blocker.

**Tech Stack:** Python 3 (stdlib + the existing studio/eval toolchain: cv2/ffmpeg via `studio.review`, the atlas eval analyzers). pytest. No new dependencies.

## Global Constraints

- Python; match the existing studio module style (module docstring, `from __future__ import annotations`, lazy heavy imports, graceful degradation — never raise on toolchain gaps, surface in an `errors`/reason field).
- Reuse, never fork: `studio.review.evidence`, `studio.review.motion_check`, `studio.review.critics.technical_scan`, `eval/analyzers/video.py`. Scoring/blocking lives in `studio/gate/`; evidence-gathering stays in `studio/review/`.
- Determinism contract: nothing in the gate may itself use wall-clock/random in a way that makes scores non-reproducible for a fixed render+seed.
- All thresholds/floors are CEO-owned in `studio/gate/thresholds.json` (read-only to any improvement loop) — never hard-code a floor in logic; read it from the loaded config.
- Tests live in `studio/tests/` and run with `python -m pytest studio/tests/<file> -v` from the repo root (`/home/zain-ali/Documents/YT-AGENTS`).
- A scored dimension that cannot be measured returns `score=None` (skipped, non-blocking, noted) — it never defaults to 0 (which would falsely block) nor to pass.
- Verdict semantics mirror the factcheck gate: a **compliance** failure is un-approvable; a **scored-dimension** failure is the block reason surfaced to the operator.

---

### Task 1: Scaffold the `studio/gate/` package + thresholds + result types

**Files:**
- Create: `studio/gate/__init__.py` (package marker + public re-exports, filled in Task 8)
- Create: `studio/gate/types.py`
- Create: `studio/gate/thresholds.json`
- Test: `studio/tests/test_gate_types.py`

**Interfaces:**
- Produces: `DimResult` dataclass `{name:str, score:float|None, floor:float, passed:bool|None, diagnostics:list[str], detail:dict}`; `ComplianceResult` dataclass `{name:str, passed:bool|None, reason:str}`; `load_thresholds() -> dict`; `band_score(value, low, high) -> float` (maps a raw value in [low,high] linearly to [0,5], clamped).

- [ ] **Step 1: Write the failing test**

```python
# studio/tests/test_gate_types.py
from studio.gate.types import DimResult, ComplianceResult, load_thresholds, band_score


def test_band_score_maps_and_clamps():
    assert band_score(0, 0, 10) == 0.0
    assert band_score(10, 0, 10) == 5.0
    assert band_score(5, 0, 10) == 2.5
    assert band_score(-3, 0, 10) == 0.0      # clamp low
    assert band_score(99, 0, 10) == 5.0      # clamp high


def test_dimresult_passed_is_caller_set():
    d = DimResult(name="motion_energy", score=2.0, floor=3.0, passed=False,
                  diagnostics=["too static"], detail={})
    assert d.name == "motion_energy" and d.passed is False


def test_thresholds_load_has_every_dimension_floor():
    t = load_thresholds()
    for dim in ("motion_energy", "motion_variety", "content_fidelity",
                "dead_air", "pacing", "audio", "polish_vs_reference"):
        assert dim in t["dimensions"], f"missing threshold block for {dim}"
        assert "floor" in t["dimensions"][dim]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_gate_types.py -v`
Expected: FAIL — `ModuleNotFoundError: studio.gate`.

- [ ] **Step 3: Write minimal implementation**

```python
# studio/gate/__init__.py
"""studio.gate — the quality scorecard + publish blocker (see
docs/superpowers/specs/2026-06-28-reference-quality-compose-and-quality-gate-design.md).
Public seam (gate.score) is filled in Task 8."""
from __future__ import annotations
```

```python
# studio/gate/types.py
"""studio.gate.types — shared result shapes + threshold loading for the gate."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_THRESHOLDS_PATH = Path(__file__).with_name("thresholds.json")


@dataclass
class DimResult:
    name: str
    score: float | None          # 0-5, or None when unmeasurable (non-blocking)
    floor: float
    passed: bool | None          # score >= floor; None when unmeasurable
    diagnostics: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


@dataclass
class ComplianceResult:
    name: str
    passed: bool | None          # None = could not run (toolchain gap)
    reason: str = ""


def load_thresholds() -> dict:
    return json.loads(_THRESHOLDS_PATH.read_text(encoding="utf-8"))


def band_score(value: float, low: float, high: float) -> float:
    """Map ``value`` in [low,high] linearly to [0,5], clamped to the ends."""
    if high == low:
        return 5.0 if value >= high else 0.0
    frac = (float(value) - low) / (high - low)
    frac = max(0.0, min(1.0, frac))
    return round(frac * 5.0, 3)
```

```json
{
  "default_floor": 3.0,
  "dimensions": {
    "motion_energy":       {"floor": 3.0, "weight": 0.15, "band": [1.5, 6.0]},
    "motion_variety":      {"floor": 3.0, "weight": 0.25, "band": [0.4, 1.0]},
    "content_fidelity":    {"floor": 3.5, "weight": 0.20, "band": [0.6, 1.0]},
    "dead_air":            {"floor": 3.0, "weight": 0.10, "band": [0.0, 1.0]},
    "pacing":              {"floor": 2.5, "weight": 0.05, "band": [1.5, 9.0], "ideal": [2.0, 7.0]},
    "audio":               {"floor": 3.0, "weight": 0.10, "band": [0.0, 1.0]},
    "polish_vs_reference": {"floor": 2.5, "weight": 0.15, "band": [0.0, 1.0],
                            "min_votes": 3, "margin": 0.0}
  },
  "compliance": {
    "overflow_blocks": true,
    "likeness_blocks": true,
    "max_unknown_license": 0
  }
}
```

Note: `thresholds.json` is the file content for the `Create` above (the JSON block).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_gate_types.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add studio/gate/__init__.py studio/gate/types.py studio/gate/thresholds.json studio/tests/test_gate_types.py
git commit -m "feat(gate): scaffold studio/gate package, result types + CEO thresholds"
```

---

### Task 2: Number dimensions — `motion_energy`, `pacing`, `audio`

**Files:**
- Create: `studio/gate/dimensions.py`
- Test: `studio/tests/test_gate_dimensions_numbers.py`

**Interfaces:**
- Consumes: `DimResult`, `band_score`, `load_thresholds` (Task 1); the evidence pack shape from `studio/review/evidence.collect_evidence` (`global.motion_energy:float|None`, `global.cut_rhythm:float|None`, `loudness:{integrated_lufs,true_peak_dbtp,clipping}`).
- Produces: `score_motion_energy(ev, t) -> DimResult`, `score_pacing(ev, t) -> DimResult`, `score_audio(ev, t) -> DimResult` (each `t` is the loaded thresholds dict).

- [ ] **Step 1: Write the failing test**

```python
# studio/tests/test_gate_dimensions_numbers.py
from studio.gate.types import load_thresholds
from studio.gate import dimensions as D

T = load_thresholds()


def test_motion_energy_low_fails_with_diagnostic():
    ev = {"global": {"motion_energy": 0.9}, "motion": {"scenes": [
        {"scene_no": 1, "motion_energy": 0.5}, {"scene_no": 2, "motion_energy": 7.0}]}}
    r = D.score_motion_energy(ev, T)
    assert r.passed is False and r.score < T["dimensions"]["motion_energy"]["floor"]
    assert any("static" in d.lower() or "scene 1" in d.lower() for d in r.diagnostics)


def test_motion_energy_healthy_passes():
    ev = {"global": {"motion_energy": 6.0}, "motion": {"scenes": []}}
    r = D.score_motion_energy(ev, T)
    assert r.passed is True and r.score >= T["dimensions"]["motion_energy"]["floor"]


def test_motion_energy_unmeasurable_is_none():
    r = D.score_motion_energy({"global": {"motion_energy": None}, "motion": {}}, T)
    assert r.score is None and r.passed is None


def test_audio_off_target_fails():
    ev = {"loudness": {"integrated_lufs": -22.0, "true_peak_dbtp": -3.0, "clipping": False}}
    r = D.score_audio(ev, T)
    assert r.passed is False
    assert any("LUFS" in d or "lufs" in d.lower() for d in r.diagnostics)


def test_audio_clipping_forces_fail():
    ev = {"loudness": {"integrated_lufs": -14.0, "true_peak_dbtp": -0.2, "clipping": True}}
    r = D.score_audio(ev, T)
    assert r.passed is False and any("clip" in d.lower() for d in r.diagnostics)


def test_pacing_in_ideal_band_passes():
    r = D.score_pacing({"global": {"cut_rhythm": 4.0}}, T)
    assert r.passed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_gate_dimensions_numbers.py -v`
Expected: FAIL — `ModuleNotFoundError: studio.gate.dimensions`.

- [ ] **Step 3: Write minimal implementation**

```python
# studio/gate/dimensions.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_gate_dimensions_numbers.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add studio/gate/dimensions.py studio/tests/test_gate_dimensions_numbers.py
git commit -m "feat(gate): motion_energy, pacing, audio dimension scorers"
```

---

### Task 3: `dead_air` dimension

**Files:**
- Modify: `studio/gate/dimensions.py` (append `score_dead_air`)
- Test: `studio/tests/test_gate_dead_air.py`

**Interfaces:**
- Consumes: evidence `motion: {scenes: [{scene_no, flags:[...], status}], any_flag}` (from `motion_check.evaluate_scene_motion`, surfaced in the evidence pack).
- Produces: `score_dead_air(ev, t) -> DimResult`.

- [ ] **Step 1: Write the failing test**

```python
# studio/tests/test_gate_dead_air.py
from studio.gate.types import load_thresholds
from studio.gate import dimensions as D

T = load_thresholds()


def test_dead_air_flags_named_scenes():
    ev = {"motion": {"any_flag": True, "scenes": [
        {"scene_no": 1, "flags": [], "status": "PASS"},
        {"scene_no": 3, "flags": ["trailing_static"], "status": "FLAG"},
        {"scene_no": 6, "flags": ["trailing_static"], "status": "FLAG"},
        {"scene_no": 8, "flags": ["silent_gap"], "status": "FLAG"}]}}
    r = D.score_dead_air(ev, T)
    assert r.passed is False
    joined = " ".join(r.diagnostics)
    assert "3" in joined and "6" in joined and "8" in joined


def test_dead_air_clean_passes_full():
    ev = {"motion": {"any_flag": False, "scenes": [
        {"scene_no": i, "flags": [], "status": "PASS"} for i in range(1, 6)]}}
    r = D.score_dead_air(ev, T)
    assert r.passed is True and r.score == 5.0


def test_dead_air_no_scenes_is_none():
    r = D.score_dead_air({"motion": {"scenes": []}}, T)
    assert r.score is None and r.passed is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_gate_dead_air.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'score_dead_air'`.

- [ ] **Step 3: Write minimal implementation** (append to `studio/gate/dimensions.py`)

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_gate_dead_air.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add studio/gate/dimensions.py studio/tests/test_gate_dead_air.py
git commit -m "feat(gate): dead_air dimension from per-scene motion flags"
```

---

### Task 4: `motion_variety` — the anti-spam metric (scene-signature parser)

**Files:**
- Create: `studio/gate/parse.py` (static `index.html` parsing, shared by Tasks 4 & 5)
- Modify: `studio/gate/dimensions.py` (append `score_motion_variety`)
- Test: `studio/tests/test_gate_motion_variety.py`

**Interfaces:**
- Produces: `parse.scene_blocks(html) -> list[dict]` (each `{scene_no:int, id:str, html:str}` — the inner HTML of every `<section ... class="scene clip">`); `parse.scene_signature(block_html, choreo_js) -> str` (a stable token summarizing the scene's layout + beat — e.g. `"count-up|stat"`, `"orbit"`, `"underline"`); `dimensions.score_motion_variety(ev, t) -> DimResult`.
- Consumes: evidence `index_html:str`, `scenes` (for scene count).

- [ ] **Step 1: Write the failing test**

```python
# studio/tests/test_gate_motion_variety.py
from studio.gate.types import load_thresholds
from studio.gate import parse, dimensions as D

T = load_thresholds()

# 3 scenes that all share ONE beat (the current-engine failure) vs 3 distinct.
SAMEY = """
<section id="s1" class="scene clip"><div class="lead">A</div><div class="fx"></div></section>
<section id="s2" class="scene clip"><div class="lead">B</div><div class="fx"></div></section>
<section id="s3" class="scene clip"><div class="lead">C</div><div class="fx"></div></section>
<script>
makeOutlineDraw({ mount: "#s1 .fx" }); makeOutlineDraw({ mount: "#s2 .fx" });
makeOutlineDraw({ mount: "#s3 .fx" });
</script>"""

VARIED = """
<section id="s1" class="scene clip"><div class="lead">A</div><span class="count-host"></span></section>
<section id="s2" class="scene clip"><div class="lead">B</div><div class="fx"></div></section>
<section id="s3" class="scene clip"><div class="lead">C</div><div class="cards"></div></section>
<script>
countUp({ mount: "#s1 .count-host" });
makeOrbitCluster({ mount: "#s2 .fx" });
quoteCards({ mount: "#s3 .cards" });
</script>"""


def test_scene_blocks_finds_all():
    blocks = parse.scene_blocks(SAMEY)
    assert [b["scene_no"] for b in blocks] == [1, 2, 3]


def test_samey_scores_low_with_dominant_signature_diag():
    ev = {"index_html": SAMEY, "scenes": [{"scene_no": i} for i in (1, 2, 3)]}
    r = D.score_motion_variety(ev, T)
    assert r.passed is False
    assert any("share" in d.lower() or "templated" in d.lower() for d in r.diagnostics)


def test_varied_scores_high():
    ev = {"index_html": VARIED, "scenes": [{"scene_no": i} for i in (1, 2, 3)]}
    r = D.score_motion_variety(ev, T)
    assert r.passed is True and r.score >= T["dimensions"]["motion_variety"]["floor"]


def test_no_html_is_none():
    r = D.score_motion_variety({"index_html": "", "scenes": []}, T)
    assert r.score is None and r.passed is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_gate_motion_variety.py -v`
Expected: FAIL — `ModuleNotFoundError: studio.gate.parse`.

- [ ] **Step 3: Write minimal implementation**

```python
# studio/gate/parse.py
"""studio.gate.parse — static structural parsing of a composed index.html, shared by the
motion_variety and content_fidelity dimensions. Regex-based (the compositions are
machine-authored with a stable shape), tolerant of missing pieces."""
from __future__ import annotations

import re

_SECTION_RE = re.compile(
    r'<section\b[^>]*\bid="(?P<id>s\d+)"[^>]*\bclass="[^"]*\bscene\b[^"]*"[^>]*>'
    r'(?P<body>.*?)</section>', re.DOTALL | re.IGNORECASE)

# Beat/layout tokens we recognize in the choreography + markup. Order = priority.
_BEAT_TOKENS = [
    ("count-up", r'count-host|countUp|count-up'),
    ("orbit", r'makeOrbitCluster|class="[^"]*orbit'),
    ("bell", r'\bbell\b|notif'),
    ("quote-cards", r'quoteCards|class="[^"]*cards'),
    ("shatter", r'shatter|crumble'),
    ("drain", r'grayscale|drain'),
    ("checklist", r'checklist|checkmark'),
    ("strike", r'strike|strikethrough'),
    ("signature", r'signature|writeOn'),
    ("underline", r'makeOutlineDraw|underline'),
]


def scene_blocks(html: str) -> list[dict]:
    out = []
    for m in _SECTION_RE.finditer(html or ""):
        sid = m.group("id")
        out.append({"scene_no": int(sid[1:]), "id": sid, "html": m.group("body")})
    return out


def scene_signature(block_html: str, choreo_js: str) -> str:
    """A stable token for the scene's beat. Looks in BOTH the scene markup and the
    composition's choreography script (beats are wired by `#sid` selector there)."""
    sid_m = re.search(r'id="(s\d+)"', block_html)
    sid = sid_m.group(1) if sid_m else ""
    # choreography lines that mention this scene id
    scoped = "\n".join(l for l in (choreo_js or "").splitlines() if f"#{sid} " in l or f'#{sid}"' in l)
    hay = block_html + "\n" + scoped
    for name, pat in _BEAT_TOKENS:
        if re.search(pat, hay, re.IGNORECASE):
            return name
    return "plain"
```

```python
# append to studio/gate/dimensions.py
from . import parse as _parse   # add at top of file with the other imports


def score_motion_variety(ev: dict, t: dict) -> DimResult:
    cfg = t["dimensions"]["motion_variety"]
    floor = float(cfg["floor"])
    html = ev.get("index_html") or ""
    blocks = _parse.scene_blocks(html)
    if not blocks:
        return DimResult("motion_variety", None, floor, None,
                         ["no scenes parsed from index.html"], {})
    choreo = html  # signatures scan the whole doc (choreography is inline)
    sigs = [_parse.scene_signature(b["html"], choreo) for b in blocks]
    distinct = len(set(sigs))
    ratio = distinct / len(sigs)
    score = band_score(ratio, *cfg["band"])
    diags = []
    # dominant signature share
    dom = max(set(sigs), key=sigs.count)
    dom_n = sigs.count(dom)
    if dom_n > 1 and dom_n / len(sigs) >= 0.5:
        diags.append(f"{dom_n}/{len(sigs)} scenes share the '{dom}' beat → templated")
    if score < floor:
        diags.append(f"only {distinct} distinct beat(s) across {len(sigs)} scenes")
    return DimResult("motion_variety", score, floor, score >= floor, diags,
                     {"distinct": distinct, "scenes": len(sigs), "signatures": sigs})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_gate_motion_variety.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add studio/gate/parse.py studio/gate/dimensions.py studio/tests/test_gate_motion_variety.py
git commit -m "feat(gate): motion_variety anti-spam metric + scene-signature parser"
```

---

### Task 5: `content_fidelity` — scripted content actually rendered

**Files:**
- Modify: `studio/gate/parse.py` (append `normalize_text`, `is_attributed_quote`)
- Modify: `studio/gate/dimensions.py` (append `score_content_fidelity`)
- Test: `studio/tests/test_gate_content_fidelity.py`

**Interfaces:**
- Consumes: evidence `index_html:str`, `scenes:[{scene_no, on_screen_text, ...}]` (per-scene text from the VO grid), and `script.scenes:[{scene_no, on_screen_text, claims:[...]}]`.
- Produces: `parse.normalize_text(s) -> str` (lowercase, strip punctuation/whitespace for substring matching); `parse.is_attributed_quote(s) -> bool` (a quote with an attribution — `"…" — Name`); `dimensions.score_content_fidelity(ev, t) -> DimResult`.

- [ ] **Step 1: Write the failing test**

```python
# studio/tests/test_gate_content_fidelity.py
from studio.gate.types import load_thresholds
from studio.gate import parse, dimensions as D

T = load_thresholds()


def test_is_attributed_quote():
    assert parse.is_attributed_quote('"Behavioral cocaine." — Aza Raskin')
    assert not parse.is_attributed_quote("141 minutes a day")


def test_missing_attributed_quote_forces_below_floor():
    # script scene 5 has the quote card; the composition dropped it.
    ev = {
        "index_html": '<section id="s5" class="scene clip"><div class="lead">THEY ADMIT IT</div></section>',
        "script": {"scenes": [
            {"scene_no": 5, "on_screen_text": '"Behavioral cocaine." — Aza Raskin', "claims": []}]},
        "scenes": [{"scene_no": 5, "on_screen_text": '"Behavioral cocaine." — Aza Raskin'}],
    }
    r = D.score_content_fidelity(ev, T)
    assert r.passed is False
    assert any("quote" in d.lower() and "raskin" in d.lower() for d in r.diagnostics)


def test_all_content_present_passes():
    ev = {
        "index_html": '<section id="s1" class="scene clip"><div class="lead">141 MINUTES A DAY</div></section>',
        "script": {"scenes": [{"scene_no": 1, "on_screen_text": "141 minutes a day", "claims": []}]},
        "scenes": [{"scene_no": 1, "on_screen_text": "141 minutes a day"}],
    }
    r = D.score_content_fidelity(ev, T)
    assert r.passed is True


def test_no_script_is_none():
    r = D.score_content_fidelity({"index_html": "<section id='s1'></section>", "script": {"scenes": []}}, T)
    assert r.score is None and r.passed is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_gate_content_fidelity.py -v`
Expected: FAIL — `AttributeError: ... 'is_attributed_quote'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to studio/gate/parse.py
def normalize_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def is_attributed_quote(s: str) -> bool:
    s = s or ""
    has_quote = ('"' in s) or ("“" in s) or ("”" in s)
    has_attrib = bool(re.search(r"[—\-–]\s*[A-Z][a-z]", s))
    return has_quote and has_attrib
```

```python
# append to studio/gate/dimensions.py
def score_content_fidelity(ev: dict, t: dict) -> DimResult:
    cfg = t["dimensions"]["content_fidelity"]
    floor = float(cfg["floor"])
    script_scenes = ((ev.get("script") or {}).get("scenes")) or []
    html = ev.get("index_html") or ""
    if not script_scenes or not html:
        return DimResult("content_fidelity", None, floor, None,
                         ["no script scenes / no index.html to compare"], {})
    blocks = {b["scene_no"]: _parse.normalize_text(b["html"]) for b in _parse.scene_blocks(html)}
    whole = _parse.normalize_text(html)

    total = present = 0
    missing_quote = False
    diags = []
    for sc in script_scenes:
        no = sc.get("scene_no")
        hay = blocks.get(no, whole)   # fall back to whole doc if block id differs
        items = []
        ost = sc.get("on_screen_text")
        if ost:
            items.append(("text", ost))
        for c in (sc.get("claims") or []):
            ctext = c.get("text") if isinstance(c, dict) else c
            if ctext:
                items.append(("claim", ctext))
        for kind, raw in items:
            total += 1
            # match if a healthy fraction of the item's content words appear
            words = [w for w in _parse.normalize_text(raw).split() if len(w) > 2]
            if not words:
                present += 1
                continue
            hit = sum(1 for w in words if w in hay)
            ok = hit / len(words) >= 0.6
            if ok:
                present += 1
            else:
                if _parse.is_attributed_quote(raw):
                    missing_quote = True
                    who = (raw.split("—")[-1].split("-")[-1].strip()[:24]) if ("—" in raw or "-" in raw) else "?"
                    diags.append(f"scene {no} dropped the attributed QUOTE ({who})")
                else:
                    diags.append(f"scene {no} missing on-screen {kind}: {raw[:40]!r}")
    frac = present / total if total else 1.0
    score = band_score(frac, *cfg["band"])
    if missing_quote:
        score = min(score, floor - 0.5)   # a dropped attributed quote cannot pass
    return DimResult("content_fidelity", round(score, 3), floor, score >= floor, diags,
                     {"present": present, "total": total, "missing_quote": missing_quote})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_gate_content_fidelity.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add studio/gate/parse.py studio/gate/dimensions.py studio/tests/test_gate_content_fidelity.py
git commit -m "feat(gate): content_fidelity dimension (dropped attributed quote = below floor)"
```

---

### Task 6: `compliance.py` — the hard publish blockers

**Files:**
- Create: `studio/gate/compliance.py`
- Test: `studio/tests/test_gate_compliance.py`

**Interfaces:**
- Consumes: `ComplianceResult` (Task 1); `studio.review.critics.technical_scan(html) -> {nondeterminism:list, registers_timeline:bool}`; project files under `studio.config.PROJECTS_DIR/<slug>/` (`factcheck_report.json`, asset manifests); HyperFrames `inspect` (via a `inspect_fn` seam, default shells out).
- Produces: `check_determinism(html) -> ComplianceResult`; `check_factcheck(pdir) -> ComplianceResult`; `check_licenses(pdir) -> ComplianceResult`; `check_overflow(pdir, *, inspect_fn=None) -> ComplianceResult`; `check_no_likeness(ev, *, vision_fn=None) -> ComplianceResult`; `run_compliance(ev, pdir, t, *, inspect_fn=None, vision_fn=None) -> list[ComplianceResult]`.

- [ ] **Step 1: Write the failing test**

```python
# studio/tests/test_gate_compliance.py
import json
from studio.gate.types import load_thresholds
from studio.gate import compliance as C

T = load_thresholds()


def test_determinism_fails_on_math_random():
    r = C.check_determinism('<script>var x = Math.random();\nwindow.__timelines["a"]=t;</script>')
    assert r.passed is False and "random" in r.reason.lower()


def test_determinism_passes_clean():
    r = C.check_determinism('<script>window.__timelines["a"] = gsap.timeline();</script>')
    assert r.passed is True


def test_factcheck_block_fails(tmp_path):
    (tmp_path / "factcheck_report.json").write_text(json.dumps({"verdict": "block"}))
    assert C.check_factcheck(tmp_path).passed is False


def test_factcheck_pass(tmp_path):
    (tmp_path / "factcheck_report.json").write_text(json.dumps({"verdict": "pass"}))
    assert C.check_factcheck(tmp_path).passed is True


def test_overflow_blocks_when_inspect_reports_clip(tmp_path):
    fake = lambda pdir: {"overflow": [{"scene": 2, "text": "DARK TRUTH BEHIN"}], "ok": False}
    r = C.check_overflow(tmp_path, inspect_fn=fake)
    assert r.passed is False and "2" in r.reason


def test_overflow_unavailable_is_none(tmp_path):
    r = C.check_overflow(tmp_path, inspect_fn=lambda pdir: None)
    assert r.passed is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_gate_compliance.py -v`
Expected: FAIL — `ModuleNotFoundError: studio.gate.compliance`.

- [ ] **Step 3: Write minimal implementation**

```python
# studio/gate/compliance.py
"""studio.gate.compliance — the HARD publish blockers (pass/fail, not 0-5). A failure here
is un-approvable (same semantics as the factcheck gate). Every check degrades to
passed=None when its toolchain is unavailable; the scorecard decides whether None blocks
(per thresholds: overflow_blocks / likeness_blocks)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .types import ComplianceResult


def check_determinism(html: str) -> ComplianceResult:
    from studio.review.critics import technical_scan
    scan = technical_scan(html or "")
    nd = scan.get("nondeterminism") or []
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_gate_compliance.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add studio/gate/compliance.py studio/tests/test_gate_compliance.py
git commit -m "feat(gate): hard compliance checks (determinism, factcheck, license, overflow, likeness)"
```

---

### Task 7: `judge.py` — the `polish_vs_reference` LLM dimension

**Files:**
- Create: `studio/gate/judge.py`
- Test: `studio/tests/test_gate_judge.py`

**Interfaces:**
- Consumes: evidence `polish_vs_reference: {rate:float|None, n:int, error}` (already ensembled in `studio.review.evidence.polish_vs_reference`).
- Produces: `score_polish(ev, t) -> DimResult`.

- [ ] **Step 1: Write the failing test**

```python
# studio/tests/test_gate_judge.py
from studio.gate.types import load_thresholds
from studio.gate import judge

T = load_thresholds()


def test_polish_high_rate_passes():
    ev = {"polish_vs_reference": {"rate": 0.9, "n": 5}}
    r = judge.score_polish(ev, T)
    assert r.passed is True and r.score >= T["dimensions"]["polish_vs_reference"]["floor"]


def test_polish_low_rate_fails_with_reason():
    ev = {"polish_vs_reference": {"rate": 0.0, "n": 5}}
    r = judge.score_polish(ev, T)
    assert r.passed is False and any("reference" in d.lower() for d in r.diagnostics)


def test_polish_too_few_votes_is_none_not_block():
    ev = {"polish_vs_reference": {"rate": 0.0, "n": 1}}   # below min_votes=3
    r = judge.score_polish(ev, T)
    assert r.score is None and r.passed is None


def test_polish_unmeasured_is_none():
    r = judge.score_polish({"polish_vs_reference": {"rate": None, "n": 0}}, T)
    assert r.score is None and r.passed is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_gate_judge.py -v`
Expected: FAIL — `ModuleNotFoundError: studio.gate.judge`.

- [ ] **Step 3: Write minimal implementation**

```python
# studio/gate/judge.py
"""studio.gate.judge — the LLM holistic dimension (polish_vs_reference). It does NOT call
the model itself: studio.review.evidence already runs the ensembled, order-randomised,
seeded pairwise vote vs the pack's reference frames. This maps that rate → 0-5 and applies
the ensemble margin (need >= min_votes countable votes, else None so a thin sample never
blocks). Deterministic dims remain the primary blockers."""
from __future__ import annotations

from .types import DimResult, band_score


def score_polish(ev: dict, t: dict) -> DimResult:
    cfg = t["dimensions"]["polish_vs_reference"]
    floor = float(cfg["floor"])
    pol = ev.get("polish_vs_reference") or {}
    rate, n = pol.get("rate"), int(pol.get("n") or 0)
    min_votes = int(cfg.get("min_votes", 3))
    if rate is None or n < min_votes:
        return DimResult("polish_vs_reference", None, floor, None,
                         [f"polish anchor inconclusive ({n} votes < {min_votes})"], {})
    score = band_score(rate, *cfg["band"])
    margin = float(cfg.get("margin", 0.0))
    passed = score >= (floor + margin)
    diags = [] if passed else [f"loses to the reference on {round((1-rate)*n)}/{n} votes — below the polish bar"]
    return DimResult("polish_vs_reference", score, floor, passed, diags,
                     {"rate": rate, "votes": n})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_gate_judge.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add studio/gate/judge.py studio/tests/test_gate_judge.py
git commit -m "feat(gate): polish_vs_reference LLM dimension with ensemble margin"
```

---

### Task 8: `scorecard.py` + the public `gate.score()` orchestrator

**Files:**
- Create: `studio/gate/scorecard.py`
- Modify: `studio/gate/__init__.py` (export `score`, `build_scorecard`)
- Test: `studio/tests/test_gate_scorecard.py`

**Interfaces:**
- Consumes: all dimension scorers (Tasks 2–5,7), `run_compliance` (Task 6), `load_thresholds`.
- Produces:
  - `build_scorecard(dims: list[DimResult], compliance: list[ComplianceResult], t) -> dict` returning `{verdict: "PASS"|"BLOCKED", reasons: list[str], overall: float|None, dimensions: [...], compliance: [...]}`.
  - `score(slug=None, *, video=None, index_html=None, script=None, pdir=None, thresholds=None, evidence=None, vision_fn=None, inspect_fn=None, polish=True) -> dict` — the public seam. With `slug` it builds evidence via `collect_evidence`; with explicit `index_html`/`script`/`video` (the twin-less reference path) it builds a minimal evidence dict without a studio project.

- [ ] **Step 1: Write the failing test**

```python
# studio/tests/test_gate_scorecard.py
from studio.gate.types import load_thresholds, DimResult, ComplianceResult
from studio.gate import scorecard

T = load_thresholds()


def test_compliance_failure_blocks_even_if_dims_pass():
    dims = [DimResult("motion_variety", 5.0, 3.0, True, [], {})]
    comp = [ComplianceResult("determinism", False, "Math.random present")]
    sc = scorecard.build_scorecard(dims, comp, T)
    assert sc["verdict"] == "BLOCKED"
    assert any("random" in r.lower() for r in sc["reasons"])


def test_dim_below_floor_blocks_with_reason():
    dims = [DimResult("motion_variety", 1.0, 3.0, False, ["8/9 scenes share the 'underline' beat → templated"], {}),
            DimResult("audio", 5.0, 3.0, True, [], {})]
    comp = [ComplianceResult("determinism", True, "")]
    sc = scorecard.build_scorecard(dims, comp, T)
    assert sc["verdict"] == "BLOCKED"
    assert any("templated" in r for r in sc["reasons"])


def test_all_pass_is_pass():
    dims = [DimResult("motion_variety", 5.0, 3.0, True, [], {}),
            DimResult("polish_vs_reference", None, 2.5, None, ["inconclusive"], {})]  # None = non-blocking
    comp = [ComplianceResult("determinism", True, ""), ComplianceResult("overflow", None, "unavailable")]
    sc = scorecard.build_scorecard(dims, comp, T)
    assert sc["verdict"] == "PASS"


def test_score_with_explicit_paths_uses_injected_evidence():
    # the reference path: no studio project, evidence injected directly
    ev = {"index_html": "<section id='s1' class='scene clip'><div class='lead'>A</div></section>",
          "global": {"motion_energy": 6.0, "cut_rhythm": 4.0},
          "motion": {"any_flag": False, "scenes": [{"scene_no": 1, "flags": []}]},
          "loudness": {"integrated_lufs": -14.0, "true_peak_dbtp": -2.0, "clipping": False},
          "polish_vs_reference": {"rate": None, "n": 0},
          "script": {"scenes": []}, "frames": []}
    sc = scorecard.score(evidence=ev, pdir=None, thresholds=T,
                         inspect_fn=lambda p: None, polish=False)
    assert sc["verdict"] in ("PASS", "BLOCKED") and "dimensions" in sc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_gate_scorecard.py -v`
Expected: FAIL — `ModuleNotFoundError: studio.gate.scorecard`.

- [ ] **Step 3: Write minimal implementation**

```python
# studio/gate/scorecard.py
"""studio.gate.scorecard — combine the 0-5 dimensions + the hard compliance checks into a
single verdict + the SPECIFIC reasons a block happened, and the public gate.score() seam.

Blocking rule (mirrors the spec):
  BLOCKED if any compliance check fails (passed is False),
           or any thresholds-flagged compliance check is unavailable (passed is None and
           the threshold marks it blocking),
           or any scored dimension is below its floor (passed is False).
A dimension with score=None is skipped (non-blocking, noted)."""
from __future__ import annotations

from pathlib import Path

from .types import DimResult, ComplianceResult, load_thresholds
from . import dimensions as D
from . import judge as J
from . import compliance as CO


def build_scorecard(dims, compliance, t: dict) -> dict:
    reasons: list[str] = []

    # compliance: hard fails always block; None blocks only if the threshold says so
    comp_cfg = t.get("compliance", {})
    _block_if_unavailable = {"overflow": comp_cfg.get("overflow_blocks", False),
                             "likeness": comp_cfg.get("likeness_blocks", False)}
    comp_rows = []
    for c in compliance:
        blocking = (c.passed is False) or (c.passed is None and _block_if_unavailable.get(c.name, False))
        if c.passed is False:
            reasons.append(f"COMPLIANCE {c.name}: {c.reason}")
        elif c.passed is None and blocking:
            reasons.append(f"COMPLIANCE {c.name}: unavailable and required ({c.reason})")
        comp_rows.append({"name": c.name, "passed": c.passed, "reason": c.reason, "blocking": blocking})

    # dimensions: below-floor blocks
    dim_rows = []
    weighted, wsum = 0.0, 0.0
    for d in dims:
        if d.passed is False:
            why = "; ".join(d.diagnostics) or f"score {d.score} < floor {d.floor}"
            reasons.append(f"{d.name} {d.score}/5 (floor {d.floor}): {why}")
        if d.score is not None:
            w = float(t["dimensions"].get(d.name, {}).get("weight", 0.0))
            weighted += w * d.score
            wsum += w
        dim_rows.append({"name": d.name, "score": d.score, "floor": d.floor,
                         "passed": d.passed, "diagnostics": d.diagnostics, "detail": d.detail})

    blocked = any(r["passed"] is False or r["blocking"] for r in comp_rows) or \
        any(d.passed is False for d in dims)
    overall = round(weighted / wsum, 3) if wsum else None
    return {"verdict": "BLOCKED" if blocked else "PASS",
            "reasons": reasons, "overall": overall,
            "dimensions": dim_rows, "compliance": comp_rows}


def _all_dimensions(ev: dict, t: dict) -> list[DimResult]:
    return [
        D.score_motion_energy(ev, t),
        D.score_motion_variety(ev, t),
        D.score_content_fidelity(ev, t),
        D.score_dead_air(ev, t),
        D.score_pacing(ev, t),
        D.score_audio(ev, t),
        J.score_polish(ev, t),
    ]


def score(slug: str | None = None, *, video=None, index_html=None, script=None,
          pdir=None, thresholds=None, evidence=None, vision_fn=None, inspect_fn=None,
          polish: bool = True) -> dict:
    """Score a draft. Three input modes:
      - slug=...                         → build evidence via studio.review.evidence
      - evidence={...}                   → use the injected evidence pack (tests / reference)
      - index_html=..., script=..., video=... → minimal evidence for a twin-less artifact
    """
    t = thresholds or load_thresholds()

    if evidence is None and slug is not None:
        from studio.review import evidence as ev_mod
        evidence = ev_mod.collect_evidence(slug, video=video, vision_fn=vision_fn, polish=polish)
        from studio import config
        pdir = pdir or (config.PROJECTS_DIR / slug)
    elif evidence is None:
        # explicit-artifact mode: assemble the minimal pack the dimensions need.
        from studio.review import motion_check as mc
        html = Path(index_html).read_text(encoding="utf-8") if index_html else ""
        scr = {}
        if script:
            import json as _json
            scr = _json.loads(Path(script).read_text(encoding="utf-8"))
        evidence = {"index_html": html, "script": scr, "video": str(video) if video else None,
                    "frames": [], "scenes": scr.get("scenes", []),
                    "global": {}, "motion": {}, "loudness": {},
                    "polish_vs_reference": {"rate": None, "n": 0}, "errors": []}
        if video:
            try:
                evidence["global"] = mc.global_measures(video, {}) or {}
            except Exception:
                pass

    dims = _all_dimensions(evidence, t)
    compliance = CO.run_compliance(evidence, pdir or Path("."), t,
                                   inspect_fn=inspect_fn, vision_fn=vision_fn)
    sc = build_scorecard(dims, compliance, t)
    sc["slug"] = slug
    sc["video"] = evidence.get("video")
    return sc
```

```python
# studio/gate/__init__.py  (append)
from .scorecard import score, build_scorecard   # noqa: E402,F401
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_gate_scorecard.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add studio/gate/scorecard.py studio/gate/__init__.py studio/tests/test_gate_scorecard.py
git commit -m "feat(gate): scorecard verdict+reasons and the public gate.score() seam"
```

---

### Task 9: `calibrate.py` + the discrimination proof (the task's verification)

**Files:**
- Create: `studio/gate/calibrate.py`
- Test: `studio/tests/test_gate_calibrate.py`

**Interfaces:**
- Consumes: `score()` (Task 8). Anchors: LOW = `studio/projects/dark-truth-v2` (a studio project with `vo.grid.json`); HIGH = `reference/dark-truth-social` (explicit `index_html` + `renders/dark-truth-social.mp4`, no studio project).
- Produces: `calibrate(*, vision_fn=None) -> dict` `{low: scorecard, high: scorecard, discriminates: bool, summary: str}`; a `main()` CLI printer.

- [ ] **Step 1: Write the failing test**

The deterministic dims alone must discriminate (no LLM/vision needed). The test injects synthetic evidence packs representing the two anchors so it runs offline in CI, then asserts the verdict split. (A separate, network/render-gated manual run uses the real artifacts via `calibrate()`.)

```python
# studio/tests/test_gate_calibrate.py
from studio.gate.types import load_thresholds
from studio.gate import scorecard

T = load_thresholds()

# the flat draft: one repeated beat, a dropped attributed quote, dead air, quiet audio
LOW_EV = {
    "index_html": "".join(
        f"<section id='s{i}' class='scene clip'><div class='lead'>X</div>"
        f"<div class='fx'></div></section><script>makeOutlineDraw({{mount:'#s{i} .fx'}});</script>"
        for i in range(1, 10)),
    "script": {"scenes": [
        {"scene_no": 5, "on_screen_text": '"Behavioral cocaine." — Aza Raskin', "claims": []}]},
    "scenes": [{"scene_no": i, "on_screen_text": ""} for i in range(1, 10)],
    "global": {"motion_energy": 0.9, "cut_rhythm": 11.0},
    "motion": {"any_flag": True, "scenes": [
        {"scene_no": n, "flags": (["trailing_static"] if n in (3, 6, 8) else [])} for n in range(1, 10)]},
    "loudness": {"integrated_lufs": -22.0, "true_peak_dbtp": -3.0, "clipping": False},
    "polish_vs_reference": {"rate": 0.0, "n": 5}, "frames": [],
}

# the reference: distinct beats per scene, content present, alive, on-target audio
HIGH_EV = {
    "index_html": "".join(
        f"<section id='s{i}' class='scene clip'><div class='lead'>L{i}</div>{extra}</section>"
        for i, extra in enumerate(
            ["<span class='count-host'></span>", "<div class='fx'>portrait</div>",
             "<div class='cards'></div>", "<div class='fx'>phone</div>",
             "<div class='cards'>quote</div>", "<div class='shatter'></div>",
             "<div class='strike'></div>", "<div class='checklist'></div>",
             "<div class='signature'></div>"], start=1))
    + "<script>countUp();makeOrbitCluster();quoteCards();</script>",
    "script": {"scenes": []},
    "scenes": [{"scene_no": i, "on_screen_text": ""} for i in range(1, 10)],
    "global": {"motion_energy": 5.5, "cut_rhythm": 4.0},
    "motion": {"any_flag": False, "scenes": [{"scene_no": n, "flags": []} for n in range(1, 10)]},
    "loudness": {"integrated_lufs": -14.0, "true_peak_dbtp": -2.0, "clipping": False},
    "polish_vs_reference": {"rate": 1.0, "n": 5}, "frames": [],
}


def test_gate_discriminates_low_from_high():
    low = scorecard.score(evidence=LOW_EV, pdir=None, thresholds=T,
                          inspect_fn=lambda p: None, polish=False)
    high = scorecard.score(evidence=HIGH_EV, pdir=None, thresholds=T,
                           inspect_fn=lambda p: None, polish=False)
    assert low["verdict"] == "BLOCKED", low["reasons"]
    assert high["verdict"] == "PASS", high["reasons"]
    # and the block reasons are specific/actionable
    blob = " ".join(low["reasons"]).lower()
    assert "templated" in blob          # motion_variety
    assert "quote" in blob              # content_fidelity
    assert "dead air" in blob           # dead_air
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_gate_calibrate.py -v`
Expected: FAIL — `ModuleNotFoundError: studio.gate.calibrate` (import in calibrate test once added) — but THIS test only imports `scorecard`, so it should fail on an *assertion* if thresholds are mis-tuned. Run it; if any assert fails, TUNE `thresholds.json` bands/floors until LOW blocks and HIGH passes (this is the calibration step the spec requires). Do not weaken a floor below what still blocks the real flat draft.

- [ ] **Step 3: Write minimal implementation**

```python
# studio/gate/calibrate.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_gate_calibrate.py -v`
Expected: PASS. If it fails, tune `thresholds.json` (Task 1) until the synthetic LOW blocks and HIGH passes, then re-run.

Then run the REAL discrimination proof (best-effort; needs the rendered artifacts + cv2/ffmpeg):

Run: `python -m studio.gate.calibrate`
Expected: prints `DISCRIMINATES: True` and the flat draft's specific block reasons (templated motion, dropped quote cards, dead air, quiet audio, clipped text if inspect ran). If the real run can't measure some dims (no cv2/ffmpeg), the deterministic structural dims (motion_variety, content_fidelity from index.html) must still block the LOW anchor — confirm that in the printed reasons.

- [ ] **Step 5: Commit**

```bash
git add studio/gate/calibrate.py studio/tests/test_gate_calibrate.py studio/gate/thresholds.json
git commit -m "feat(gate): calibration proof — flat draft BLOCKED, reference PASS"
```

---

### Task 10: Wire the gate into the pipeline's final publish gate

**Files:**
- Modify: `studio/pipeline.py:492-524` (the final-gate block) + add a `_default_gate` seam near `_default_motion` (line ~559)
- Test: `studio/tests/test_pipeline_gate.py`

**Interfaces:**
- Consumes: `studio.gate.score()` (Task 8).
- Produces: the final gate's `details` now carries the full scorecard; a `BLOCKED` scorecard makes the gate **un-approvable** (the run cannot ship a sub-bar render even with `--approve final` / `--unattended`), surfacing the specific reasons. `produce(..., gate_fn=...)` is added so tests inject a fake scorer.

- [ ] **Step 1: Write the failing test**

```python
# studio/tests/test_pipeline_gate.py
from studio import pipeline


def _fake_scorecard(verdict, reasons):
    return {"verdict": verdict, "reasons": reasons, "overall": 2.0,
            "dimensions": [], "compliance": []}


def test_blocked_scorecard_prevents_final_render(monkeypatch, tmp_path):
    # a BLOCKED gate must not ship, even with explicit final approval.
    calls = {"render_final": 0}

    def fake_render(pdir, final=False):
        if final:
            calls["render_final"] += 1
        return {"ok": True, "video": str(tmp_path / "out.mp4")}

    state = pipeline.produce(
        {"topic": "t"}, "gate-block-test",
        approve={"final"}, gates=True,
        run_config={"pack_id": "p", "voice": "v", "render_budget_sec": 999},
        research_fn=lambda topic, angle: {"topic": "t", "verified_facts": [], "sources": []},
        script_fn=lambda b: {"scenes": [{"scene_no": 1, "narration": "n",
                                         "on_screen_text": "o", "claims": []}]},
        factcheck_fn=lambda s, b: {"verdict": "pass", "summary": {}, "claims": []},
        vo_fn=lambda s, d, **kw: {"total_duration_sec": 30, "grid": {"NS": [0], "total": 30}},
        compose_fn=lambda slug, pack_id: (pipeline.project_dir(slug) / "index.html").write_text(
            '<div id="root"><script>window.__timelines["x"]=1;</script></div>'),
        render_fn=fake_render,
        review_fn=lambda slug, mode: {"synthesis": {"fixes": [], "counts": {}}, "apply": {"applied": []}},
        motion_fn=lambda slug: {"any_flag": False},
        gate_fn=lambda slug: _fake_scorecard("BLOCKED", ["motion_variety 1/5: 8/9 scenes share 'underline'"]),
    )
    assert state["status"] == "blocked_at_gate"
    assert calls["render_final"] == 0
    assert any("templated" in r or "share" in r for r in state["gates"]["final"]["details"]["reasons"])


def test_passing_scorecard_allows_final(monkeypatch, tmp_path):
    def fake_render(pdir, final=False):
        return {"ok": True, "video": str(tmp_path / "out.mp4")}
    state = pipeline.produce(
        {"topic": "t"}, "gate-pass-test",
        approve={"final"}, gates=True,
        run_config={"pack_id": "p", "voice": "v", "render_budget_sec": 999},
        research_fn=lambda topic, angle: {"topic": "t", "verified_facts": [], "sources": []},
        script_fn=lambda b: {"scenes": [{"scene_no": 1, "narration": "n",
                                         "on_screen_text": "o", "claims": []}]},
        factcheck_fn=lambda s, b: {"verdict": "pass", "summary": {}, "claims": []},
        vo_fn=lambda s, d, **kw: {"total_duration_sec": 30, "grid": {"NS": [0], "total": 30}},
        compose_fn=lambda slug, pack_id: (pipeline.project_dir(slug) / "index.html").write_text(
            '<div id="root"><script>window.__timelines["x"]=1;</script></div>'),
        render_fn=fake_render,
        review_fn=lambda slug, mode: {"synthesis": {"fixes": [], "counts": {}}, "apply": {"applied": []}},
        motion_fn=lambda slug: {"any_flag": False},
        gate_fn=lambda slug: _fake_scorecard("PASS", []),
    )
    assert state["status"] == "complete"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_pipeline_gate.py -v`
Expected: FAIL — `produce() got an unexpected keyword argument 'gate_fn'`.

- [ ] **Step 3: Write minimal implementation**

In `studio/pipeline.py`, add the status constant near the others (line ~230):

```python
STATUS_BLOCKED_BY_GATE = "blocked_at_gate"
```

Add `gate_fn=None` to the `produce(...)` signature (with the other `*_fn` params, ~line 328).

Add a default seam near `_default_motion` (~line 559):

```python
def _default_gate(slug: str) -> dict:
    from . import gate
    try:
        return gate.score(slug=slug)
    except Exception as exc:  # noqa: BLE001
        # a gate that cannot run must NOT silently pass — block with the reason.
        return {"verdict": "BLOCKED", "reasons": [f"gate error: {exc}"],
                "overall": None, "dimensions": [], "compliance": []}
```

Replace the final-gate block ([studio/pipeline.py:492-524](../../../studio/pipeline.py#L492-L524)) so the scorecard is computed and a `BLOCKED` verdict is an un-approvable stop BEFORE any approval/auto/bypass path:

```python
    # 8. final GATE → video.mp4
    if _stage_status(state, GATE_FINAL) != "done":
        run_motion = motion_fn or (lambda slug: _default_motion(slug))
        motion = run_motion(slug)
        motion_ok = not bool((motion or {}).get("any_flag"))

        # The quality gate scorecard — the publish blocker. A BLOCKED verdict can NEVER be
        # approved away (same hard semantics as the factcheck gate): a sub-bar render is not
        # something a human or --unattended can sign off.
        run_gate = gate_fn or (lambda slug: _default_gate(slug))
        scorecard = run_gate(slug)
        gate_blocked = scorecard.get("verdict") == "BLOCKED"

        review_unresolved = state["stages"].get("review", {}).get("unresolved", []) or []
        review_ok = not review_unresolved
        est = _estimate_render_sec(pdir)
        budget = float(run_config.get("render_budget_sec", config.DEFAULT_RENDER_BUDGET_SEC))
        under_budget = isinstance(est, (int, float)) and est <= budget

        details = {"motion_ok": motion_ok, "review_ok": review_ok,
                   "review_unresolved": review_unresolved,
                   "est_runtime_sec": est, "render_budget_sec": budget,
                   "under_budget": under_budget,
                   "verdict": scorecard.get("verdict"), "overall": scorecard.get("overall"),
                   "reasons": scorecard.get("reasons", []), "scorecard": scorecard}

        if gate_blocked:
            state["gates"][GATE_FINAL] = {"status": "blocked", "approvable": False,
                                          "details": details,
                                          "reason": "quality gate BLOCKED — " + "; ".join(scorecard.get("reasons", [])[:4])}
            _set_stage(state, GATE_FINAL, "blocked")
            state["status"] = STATUS_BLOCKED_BY_GATE
            _log(state, GATE_FINAL, "BLOCKED by quality gate", "; ".join(scorecard.get("reasons", [])))
            _save_state(pdir, state)
            return state

        human_approved = GATE_FINAL in approve
        auto_ok = unattended and motion_ok and review_ok and under_budget
        bypass = not gates

        if not (human_approved or auto_ok or bypass):
            reason = ("awaiting human approval" if not unattended else
                      f"unattended hold: motion_ok={motion_ok} review_ok={review_ok} "
                      f"under_budget={under_budget} (est {est}s vs {budget}s)")
            state["gates"][GATE_FINAL] = {"status": "awaiting_approval", "approvable": True,
                                          "details": details, "reason": reason}
            _set_stage(state, GATE_FINAL, "awaiting_approval")
            state["status"] = STATUS_AWAITING_FINAL
            _log(state, GATE_FINAL, "paused at final gate", reason)
            _save_state(pdir, state)
            return state

        approver = ("human" if human_approved else "unattended-auto" if auto_ok else "no-gates")
        res = render_fn(pdir, final=True)
        if not res.get("ok"):
            state["stages"].setdefault("final", {})["error"] = res.get("error") or "render failed"
            _set_stage(state, "final", "error")
            state["status"] = STATUS_RENDER_FAILED
            _log(state, GATE_FINAL, "final render failed", str(res.get("error")))
            _save_state(pdir, state)
            return state
        state["gates"][GATE_FINAL] = {"status": "passed", "approvable": True,
                                      "approved_by": approver, "details": details}
        state["artifacts"]["video"] = res.get("video")
        _set_stage(state, GATE_FINAL, "done")
        state["status"] = STATUS_COMPLETE
        _log(state, GATE_FINAL, f"final gate cleared ({approver}) → video.mp4",
             str(res.get("video")))
        _save_state(pdir, state)
```

Also map the new status in `studio/run.py:_cmd_produce` return-code dict (line ~203) so the CLI reports it:

```python
    return {"complete": 0, "render_failed": 1, "blocked_at_factcheck": 2,
            "awaiting_final_gate": 3, "blocked_at_gate": 4}.get(status, 0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_pipeline_gate.py -v`
Expected: PASS (2 tests).

Then run the full studio suite to confirm no regression:

Run: `python -m pytest studio/tests/ -q`
Expected: PASS (existing suite + the new gate tests).

- [ ] **Step 5: Commit**

```bash
git add studio/pipeline.py studio/run.py studio/tests/test_pipeline_gate.py
git commit -m "feat(gate): wire the scorecard into the final publish gate (BLOCKED is un-approvable)"
```

---

## Self-Review

**1. Spec coverage:**
- 0–5 per-dimension score with actionable diagnostics → Tasks 2–5,7 (every `DimResult` carries `diagnostics`).
- `motion_variety` anti-spam metric → Task 4. `content_fidelity` w/ attributed-quote severity → Task 5. `legibility/overflow` blocking via `inspect` → Task 6 (`check_overflow`). Determinism/license/likeness/factcheck compliance → Task 6. Polish-vs-reference frame judge with ensemble margin + pack-golden reference strategy → Task 7 (consumes `evidence.polish_vs_reference`, which already samples the pack/reference frames per the spec's reference strategy).
- Deterministic-first, LLM-layered → Tasks 2–6 are LLM-free; Task 7 is the only LLM dim and is non-blocking when inconclusive.
- BLOCK below bar, compliance un-approvable → Task 8 `build_scorecard` + Task 10 wiring (`blocked_at_gate`, un-approvable).
- Calibration discriminates the two anchors → Task 9 (synthetic test in CI + real `python -m studio.gate.calibrate`).
- `gate/` placement, reuse `review/` evidence, no dup → all tasks import `studio.review`/`eval`, none re-implement frame/loudness logic.

**2. Placeholder scan:** No TBD/TODO; every code step has runnable code; no "add error handling" hand-waves (graceful-degradation is shown explicitly).

**3. Type consistency:** `DimResult`/`ComplianceResult` field names are used identically across Tasks 1–10. `score()` kwargs (`evidence`, `pdir`, `thresholds`, `inspect_fn`, `vision_fn`, `polish`) match between Task 8 definition and Task 9 usage. `band_score(value, low, high)` signature consistent. `build_scorecard(dims, compliance, t)` consistent between Tasks 8 and the calibrate path.

**Out-of-scope (Plan 2 — compose recipe book):** the storyboard/Iris archetype tagging, the archetype registry, and the lifted bespoke beats are NOT in this plan. This plan delivers the working, calibrated, wired BLOCKING gate — the task's required end-to-end verification — and gives Plan 2 the live score to iterate against.
