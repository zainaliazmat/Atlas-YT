# Compose Recipe Book Implementation Plan (Plan 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `studio/compose` from a 4-beat keyword enum into a bespoke per-scene **archetype director** that (a) renders ALL scripted content (no dropped quotes/stats/checklists), (b) is tagged by Iris's closed-vocab storyboard, and (c) draws on a growing, parameterized library of bespoke per-scene motion lifted from `reference/dark-truth-social/index.html` — so `dark-truth-v2` climbs from the gate's BLOCKED toward the reference's PASS.

**Architecture:** Three phases, gate-measured. **Phase A (content fidelity FIRST):** compose renders every `on_screen_text` line + every `claims[].text` per scene, so the gate's `content_fidelity` dimension stops blocking (the flat draft dropped the Raskin/Brichter quote cards, 2 of 3 stats, the checklist, the juries footnote). **Phase B (storyboard tagging):** a new `storyboard` pipeline stage runs Iris (`art_engine.build_storyboard`) to tag each scene with an archetype from the closed `LAYOUTS` vocab; compose reads the tag (heuristic `classify()` is the fallback). **Phase C (archetype library):** a registry of bespoke, parameterized archetype builders, each shipping its own `motion_variety` beat-token in the SAME commit (a parity-tested invariant), lifting the reference's calendar-crumble / slot-reel / shatter / drain / highlighter-swipe / checklist / strike / signature beats into the shared motion library.

**Tech Stack:** Python 3 (studio module style); deterministic HTML+GSAP authoring (no per-render LLM in compose — determinism is enforced by `pipeline._enforce_determinism`); pytest. Reuses Iris (`atlas` art_engine) via the existing `studio.engines.load_engine` seam. No new runtime deps.

## Global Constraints

- **Determinism is absolute.** Every authored beat is deterministic JS (no `Math.random` / `Date.now` / `new Date` / `fetch` / `XMLHttpRequest`); the master timeline is registered on `window.__timelines`. `pipeline._enforce_determinism` RAISES on violation — every new beat factory and archetype is determinism-tested.
- **Gate-measured, not vibes.** Phase A success = the gate's `content_fidelity` dimension passes on `dark-truth-v2`; Phase C success = `motion_variety` climbs (more distinct beat tokens across scenes). Re-run `python -m studio.gate.calibrate` and `python -m studio.gate` (via `gate.score(slug="dark-truth-v2")`) to measure, never assert "looks better".
- **The archetype↔token parity INVARIANT (CEO-mandated):** every archetype in the compose registry MUST have a corresponding `motion_variety` beat-token in `studio/gate/parse.py::_BEAT_TOKENS`, and vice-versa, enforced by a parity test. A new archetype ships with its token in the SAME commit. This prevents the gate false-blocking a genuinely-varied future video whose new beats it can't yet recognize.
- **Closed archetype vocab = Iris `LAYOUTS`** (`art-director/art_engine.py` `LAYOUTS`, 13 values). It is the single source of truth; the compose registry keys and the storyboard tags both draw from it. A parity test asserts registry-keys ⊆ `LAYOUTS` and every `LAYOUTS` value resolves (to a builder or an explicit fallback).
- **Reuse, never fork:** the VO-lock re-timer, transitions, ticker, grain filters already live in pack partials (`studio/design-packs/<pack>/partials/` + `_shared/`); new beats go in the motion library via `studio/compose/_motion.py::ensure_motion_library` (write-back to the pack). Iris is reused via `studio.engines`.
- **Tests** live in `studio/tests/`, run with `python -m pytest studio/tests/<file> -v` from the repo root (`/home/zain-ali/Documents/YT-AGENTS`).
- `git add` ONLY the explicit files each task lists — never `-A`/`.` (the repo carries unrelated working-tree state).
- Commit message bodies end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## File Structure

```
studio/compose/
  __init__.py            # Composer — MODIFIED: _plan_scene renders full content; _scene_beat
                         #   delegates to the archetype registry; reads storyboard.json tags
  _content.py            # NEW (Phase A): render_on_screen_text(scene), render_claims(scene)
                         #   → the content blocks every scene must show (quotes/stats/lists/footnotes)
  _motion.py             # MODIFIED (Phase C): add lifted beat factories + BEATS registry entries
  archetypes/
    __init__.py          # NEW (Phase C): the registry {archetype: builder}, classify() fallback,
                         #   ARCHETYPES vocab (mirrors Iris LAYOUTS), build(scene, ctx)
    quote_cards.py       # NEW: one builder module per archetype (Phase C, one per task)
    stats_trio.py
    checklist_reveal.py
    ...                  # the rest, one per task, each shipping its motion_variety token
studio/
  storyboard.py          # NEW (Phase B): tag_archetypes(script, pdir) — runs Iris, maps
                         #   layout→archetype, writes storyboard.json; classify() fallback
  engines.py             # MODIFIED (Phase B): + storyboard() seam (reuses Iris art_engine)
  pipeline.py            # MODIFIED (Phase B): + "storyboard" stage between factcheck and vo
  config.py              # MODIFIED (Phase B): + ART_DIRECTOR_DIR if absent
studio/gate/
  parse.py               # MODIFIED (Phase C): grow _BEAT_TOKENS as each archetype lands
  tests/test_archetype_token_parity.py  # NEW: the invariant test
```

---

# PHASE A — Content fidelity first (every scripted line + claim renders)

### Task A1: Render the COMPLETE on-screen text (not just one lead line)

**Problem:** [studio/compose/__init__.py:269-274](../../../studio/compose/__init__.py#L269-L274) renders only `scene.on_screen_text` (or `point`) as a single `.lead` line with the last word emphasized. Multi-line on-screen text (titles like "DARK TRUTH / BEHIND THE / SOCIAL MEDIA", statements) is collapsed/truncated — which both reads wrong and fails `content_fidelity`.

**Files:**
- Create: `studio/compose/_content.py`
- Test: `studio/tests/test_compose_content.py`

**Interfaces:**
- Produces: `render_on_screen_text(text: str) -> str` — returns HTML for a `.lead` block that preserves ALL lines (split on `/` and newlines into stacked `.lead-line` spans), last word of the LAST line emphasized with `<span class="em">`. Empty → `&nbsp;`.

- [ ] **Step 1: Write the failing test**

```python
# studio/tests/test_compose_content.py
from studio.compose import _content


def test_multiline_text_preserves_all_lines():
    html = _content.render_on_screen_text("DARK TRUTH / BEHIND THE / SOCIAL MEDIA")
    assert html.count("lead-line") == 3
    assert "DARK" in html and "BEHIND" in html and "SOCIAL" in html
    assert '<span class="em">MEDIA</span>' in html  # last word of last line emphasized


def test_single_line_emphasizes_last_word():
    html = _content.render_on_screen_text("IT'S NOT A BUG")
    assert html.count("lead-line") == 1
    assert '<span class="em">BUG</span>' in html


def test_empty_is_nbsp():
    assert "&nbsp;" in _content.render_on_screen_text("")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_compose_content.py -v`
Expected: FAIL — `ModuleNotFoundError: studio.compose._content`.

- [ ] **Step 3: Write minimal implementation**

```python
# studio/compose/_content.py
"""studio.compose._content — the content blocks EVERY scene must render so nothing the
scriptwriter put on screen is silently dropped (the gate's content_fidelity dimension).
Pure HTML-string builders; no GSAP (motion is layered by the archetype). Deterministic."""
from __future__ import annotations

import html
import re


def render_on_screen_text(text: str) -> str:
    """Stacked `.lead` block preserving every line. Lines split on `/` or newline; the last
    word of the LAST line is emphasized. Empty → a non-breaking space so the slot still lays
    out."""
    raw = (text or "").strip()
    if not raw:
        return '<div class="lead"><span class="lead-line">&nbsp;</span></div>'
    lines = [seg.strip() for seg in re.split(r"\s*/\s*|\n", raw) if seg.strip()]
    out = []
    for i, line in enumerate(lines):
        words = [html.escape(w) for w in line.split()]
        if i == len(lines) - 1 and words:
            words[-1] = f'<span class="em">{words[-1]}</span>'
        out.append(f'<span class="lead-line">{" ".join(words)}</span>')
    return '<div class="lead">' + "".join(out) + "</div>"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_compose_content.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add studio/compose/_content.py studio/tests/test_compose_content.py
git commit -m "feat(compose): render full multi-line on-screen text (no truncation)"
```

---

### Task A2: Render every claim (sourced stats, attributed quotes, list items)

**Problem:** compose never renders `scene.claims[]` at all — so the Raskin/Brichter attributed quotes, the stat values, and the checklist items never appear on screen, failing `content_fidelity` (a dropped attributed quote forces that dimension below floor).

**Files:**
- Modify: `studio/compose/_content.py` (append `render_claims`)
- Test: `studio/tests/test_compose_content.py` (append)

**Interfaces:**
- Consumes: a scene's `claims` — list of `{claim_id, text, source_ref}` (from Marlow; see `dark-truth-v2/script.json`).
- Produces: `render_claims(scene: dict) -> str` — HTML for a `.claims` block: each claim is a `.claim-card`; an attributed quote (`parse.is_attributed_quote`) becomes a `.quote-card` with the quote body + a `.byline` attribution; a numeric claim keeps its number visible; everything carries the claim text so it is present on screen. Empty claims → "".

- [ ] **Step 1: Write the failing test**

```python
# append to studio/tests/test_compose_content.py
def test_attributed_quote_renders_as_quote_card_with_byline():
    scene = {"claims": [
        {"claim_id": "c1", "text": '"Sprinkling behavioral cocaine over your interface." — Aza Raskin',
         "source_ref": "F1"}]}
    html_ = _content.render_claims(scene)
    assert "quote-card" in html_
    assert "behavioral cocaine" in html_.lower()
    assert "Aza Raskin" in html_          # byline attribution present
    assert "byline" in html_


def test_plain_claim_renders_text_visibly():
    scene = {"claims": [{"claim_id": "c1", "text": "141 minutes a day", "source_ref": "F2"}]}
    html_ = _content.render_claims(scene)
    assert "claim" in html_ and "141 minutes a day" in html_


def test_no_claims_is_empty():
    assert _content.render_claims({"claims": []}) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_compose_content.py -v`
Expected: FAIL — `AttributeError: ... 'render_claims'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to studio/compose/_content.py
from studio.gate.parse import is_attributed_quote   # reuse the gate's quote detector (one source)


def _split_quote(text: str) -> tuple[str, str]:
    """('"quote body"', 'Attribution') from a `"..." — Name` string. Best-effort."""
    m = re.split(r"\s*[—–-]\s*", text.strip(), maxsplit=1)
    body = m[0].strip()
    who = m[1].strip() if len(m) > 1 else ""
    return body, who


def render_claims(scene: dict) -> str:
    """Render each scripted claim as a visible on-screen card so nothing is dropped.
    Attributed quotes become quote cards with a byline; other claims keep their text."""
    claims = scene.get("claims") or []
    if not claims:
        return ""
    cards = []
    for c in claims:
        text = (c.get("text") if isinstance(c, dict) else c) or ""
        if not text.strip():
            continue
        if is_attributed_quote(text):
            body, who = _split_quote(text)
            cards.append(
                f'<div class="claim-card quote-card anim">'
                f'<div class="quote-body">{html.escape(body)}</div>'
                f'<div class="byline mono">{html.escape(who)}</div></div>')
        else:
            cards.append(
                f'<div class="claim-card anim"><span class="claim mono">{html.escape(text)}</span></div>')
    if not cards:
        return ""
    return '<div class="claims">' + "".join(cards) + "</div>"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_compose_content.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add studio/compose/_content.py studio/tests/test_compose_content.py
git commit -m "feat(compose): render every claim (attributed quote cards, sourced stats)"
```

---

### Task A3: Wire content into the Composer + add the CSS, verify content_fidelity climbs

**Files:**
- Modify: `studio/compose/__init__.py` (`_plan_scene` uses `_content.render_on_screen_text` + appends `render_claims`)
- Modify: `studio/compose/_css.py` (add `.lead-line`, `.claims`, `.claim-card`, `.quote-card`, `.byline` styles, tinted from pack tokens)
- Test: `studio/tests/test_compose_content_wired.py`

**Interfaces:**
- Consumes: `_content.render_on_screen_text`, `_content.render_claims`.
- Produces: a composed `index.html` whose every scene section contains its full on-screen text AND a `.claims` block when the scene has claims.

- [ ] **Step 1: Write the failing test**

```python
# studio/tests/test_compose_content_wired.py
"""Compose a tiny project end-to-end (offline, fake assets) and assert scripted content lands."""
import json
from pathlib import Path
import pytest

from studio import config
from studio.compose import compose


@pytest.fixture
def mini_project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    pdir = tmp_path / "mini"
    pdir.mkdir()
    (pdir / "research_brief.json").write_text(json.dumps({"topic": "t"}))
    (pdir / "script.json").write_text(json.dumps({"working_title": "T", "scenes": [
        {"scene_no": 1, "on_screen_text": "THE MACHINE", "point": "p",
         "narration": "n", "duration_est_sec": 6,
         "claims": [{"claim_id": "c1",
                     "text": '"Pull-to-refresh is addictive." — Loren Brichter',
                     "source_ref": "F1"}]}]}))
    return pdir


def test_compose_renders_claim_quote_into_index(mini_project):
    # uses the project's real pack via the default registry; if a pack id is needed,
    # the test for your environment passes pack_id="dark-truth-social".
    out = compose("mini", pack_id="dark-truth-social")
    html = Path(out).read_text(encoding="utf-8")
    assert "quote-card" in html
    assert "Pull-to-refresh is addictive" in html
    assert "Loren Brichter" in html
    assert "THE" in html and "MACHINE" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_compose_content_wired.py -v`
Expected: FAIL — the composed HTML lacks `quote-card` / the attribution (claims not yet wired).

- [ ] **Step 3: Write minimal implementation**

In `studio/compose/__init__.py` `_plan_scene` (around [studio/compose/__init__.py:269-287](../../../studio/compose/__init__.py#L269-L287)), replace the inline `.lead` construction with the content builders:

```python
        from . import _content
        lead_html = _content.render_on_screen_text(
            scene.get("on_screen_text") or scene.get("point") or "")
        claims_html = _content.render_claims(scene)

        beat, extra_html = self._scene_beat(i, scene)
        sec = (
            f'      <section id="{sid}" class="scene clip" data-start="{_fmt(sec_start)}" '
            f'data-duration="{_fmt(sec_dur)}" data-track-index="{ti}">\n'
            f'        <div class="scene-content">\n'
            f'          <div class="label faint mono anim">FIELD REPORT // FIG. {i+1:02d}</div>\n'
            f'{lead_html}\n'
            f'{claims_html}\n'
            f'{extra_html}'
            f'        </div>\n'
            f'        <div class="fx" data-layout-ignore="" aria-hidden="true"></div>\n'
            f'      </section>'
        )
```

In `studio/compose/_css.py` `composition_css`, add (tinted from `tokens["colors"]`):

```python
    .lead-line { display:block; }
    .claims { display:flex; flex-direction:column; gap:18px; margin-top:28px; }
    .claim-card { background:var(--paper-shade,#e4e0c8); border-left:4px solid var(--spray,#2e5e1f);
                  padding:18px 22px; border-radius:6px; }
    .quote-card .quote-body { font-size:34px; line-height:1.25; color:var(--ink,#1f1f1e); }
    .quote-card .byline { margin-top:10px; color:var(--spray,#2e5e1f); font-size:18px; }
    .claim { color:var(--ink,#1f1f1e); font-size:24px; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_compose_content_wired.py -v`
Expected: PASS.

Then MEASURE on the real flat draft (re-compose + score; best-effort if the toolchain is present):

Run:
```bash
python -c "from studio.compose import compose; compose('dark-truth-v2', pack_id='dark-truth-social')"
python -c "from studio.gate import score; from studio.gate.types import load_thresholds; \
import json; sc=score(slug='dark-truth-v2', polish=False); \
cf=[d for d in sc['dimensions'] if d['name']=='content_fidelity'][0]; \
print('content_fidelity', cf['score'], 'passed', cf['passed']); print(cf['diagnostics'])"
```
Expected: `content_fidelity` score rises vs the pre-A3 baseline and its diagnostics no longer list the dropped attributed quotes. (Re-rendering the video for the motion-based dims is Phase C; content_fidelity is parsed from `index.html`, so it improves immediately.)

- [ ] **Step 5: Commit**

```bash
git add studio/compose/__init__.py studio/compose/_css.py studio/tests/test_compose_content_wired.py
git commit -m "feat(compose): wire full content (text+claims) into scenes; content_fidelity climbs"
```

---

# PHASE B — Storyboard archetype tagging (Iris)

### Task B1: The closed archetype vocab + the parity test (the invariant)

**Files:**
- Create: `studio/compose/archetypes/__init__.py` (registry skeleton + `ARCHETYPES` vocab + `classify`)
- Create: `studio/tests/test_archetype_token_parity.py`
- Modify: `studio/gate/parse.py` (extend `_BEAT_TOKENS` so every archetype's token exists — initially the existing tokens)

**Interfaces:**
- Produces: `archetypes.ARCHETYPES` — the closed vocab, exactly Iris's `LAYOUTS` values (`centered-statement`, `split-screen`, `full-bleed-image`, `lower-third`, `data-chart`, `quote-card`, `map-focus`, `list-stack`, `comparison-2up`, `title-card`, `big-number`, `timeline`, `diagram`); `archetypes.REGISTRY` — `{archetype: builder}` (empty/stub at first, filled in Phase C); `archetypes.token_for(archetype) -> str` — the `motion_variety` beat-token an archetype emits; `archetypes.classify(scene) -> str` — heuristic fallback tag.
- The parity test asserts: every `REGISTRY` key is in `ARCHETYPES`; every `token_for(a)` is present in `studio.gate.parse._BEAT_TOKENS`; and `ARCHETYPES` ⊆ Iris `LAYOUTS`.

- [ ] **Step 1: Write the failing test**

```python
# studio/tests/test_archetype_token_parity.py
from studio.compose import archetypes as A
from studio.gate import parse as P


def test_registry_keys_are_in_the_closed_vocab():
    for k in A.REGISTRY:
        assert k in A.ARCHETYPES, f"{k} not in the closed archetype vocab"


def test_every_registered_archetype_token_is_known_to_motion_variety():
    # THE INVARIANT: a new archetype ships with its motion_variety token in the same commit.
    token_names = {name for name, _pat in P._BEAT_TOKENS}
    for a in A.REGISTRY:
        tok = A.token_for(a)
        assert tok in token_names, (
            f"archetype {a!r} emits token {tok!r} but it is not in gate.parse._BEAT_TOKENS")


def test_vocab_matches_iris_layouts():
    from studio import engines
    layouts = set(engines.iris_layouts())
    assert set(A.ARCHETYPES) <= layouts, "archetype vocab drifted from Iris LAYOUTS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_archetype_token_parity.py -v`
Expected: FAIL — `ModuleNotFoundError: studio.compose.archetypes` (and `engines.iris_layouts` missing).

- [ ] **Step 3: Write minimal implementation**

```python
# studio/compose/archetypes/__init__.py
"""studio.compose.archetypes — the bespoke per-scene archetype registry.

ARCHETYPES is the CLOSED vocab (== Iris art_engine LAYOUTS). REGISTRY maps an archetype to
its builder(scene, ctx) -> {"html": str, "beats_js": str, "token": str}. token_for() names
the motion_variety beat-token an archetype emits — the gate must recognize it (parity test).
classify() is the heuristic fallback when a scene has no Iris tag."""
from __future__ import annotations

ARCHETYPES = (
    "centered-statement", "split-screen", "full-bleed-image", "lower-third",
    "data-chart", "quote-card", "map-focus", "list-stack", "comparison-2up",
    "title-card", "big-number", "timeline", "diagram",
)

# archetype -> the motion_variety beat-token it emits. Grown as builders land (Phase C).
# Until a builder exists, an archetype maps to an already-known token so parity holds.
_TOKEN = {
    "big-number": "count-up",
    "quote-card": "quote-cards",
    "list-stack": "checklist",
    "centered-statement": "underline",
}

REGISTRY: dict = {}   # filled by Phase C builder tasks via register()


def token_for(archetype: str) -> str:
    return _TOKEN.get(archetype, "underline")


def register(archetype: str, builder, token: str) -> None:
    """Register an archetype builder AND its motion_variety token together (the invariant)."""
    REGISTRY[archetype] = builder
    _TOKEN[archetype] = token


def classify(scene: dict) -> str:
    """Heuristic fallback tag when a scene carries no Iris archetype. Mirrors the old
    keyword logic but returns a vocab archetype."""
    from studio.gate.parse import is_attributed_quote
    ost = (scene.get("on_screen_text") or "") + " " + (scene.get("narration") or "")
    claims = scene.get("claims") or []
    if any(is_attributed_quote((c.get("text") if isinstance(c, dict) else c) or "") for c in claims):
        return "quote-card"
    import re
    if re.search(r"\d", scene.get("on_screen_text") or ""):
        return "big-number"
    if any(w in ost.lower() for w in ("checklist", "steps", "off", "on ")):
        return "list-stack"
    return "centered-statement"
```

```python
# studio/engines.py — add (reuse Iris's closed vocab as the single source of truth)
def iris_layouts() -> tuple:
    """Iris's closed LAYOUTS vocab — the canonical archetype vocabulary."""
    mod = load_engine(config.ART_DIRECTOR_DIR, "art_engine")
    return tuple(getattr(mod, "LAYOUTS"))
```

If `config.ART_DIRECTOR_DIR` is absent, add it to `studio/config.py` following the existing
`SAGE_DIR` / `SCRIPTWRITER_DIR` pattern (point at the repo's `art-director/` sibling).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_archetype_token_parity.py -v`
Expected: PASS (3 tests). (`REGISTRY` is empty, so the first two pass vacuously; the third pins the vocab to Iris.)

- [ ] **Step 5: Commit**

```bash
git add studio/compose/archetypes/__init__.py studio/engines.py studio/config.py studio/tests/test_archetype_token_parity.py
git commit -m "feat(compose): closed archetype vocab (Iris LAYOUTS) + token-parity invariant test"
```

---

### Task B2: The `storyboard` pipeline stage (Iris tags each scene)

**Files:**
- Create: `studio/storyboard.py`
- Modify: `studio/engines.py` (+ `storyboard()` seam reusing Iris `build_storyboard`)
- Modify: `studio/pipeline.py` (+ `"storyboard"` in `STAGES` between `factcheck` and `vo`; the stage block; `storyboard_fn` seam in `produce()`)
- Test: `studio/tests/test_storyboard.py`

**Interfaces:**
- Produces: `studio.storyboard.tag_archetypes(script: dict, pdir, *, iris_fn=None) -> dict` — returns `{"scenes": [{"scene_no", "archetype"}...]}`, one tag per script scene. Calls Iris `build_storyboard` (via `iris_fn` seam, default `engines.storyboard`), reads each planned scene's `layout`, and uses it as the archetype if it is in `ARCHETYPES`, else falls back to `archetypes.classify(scene)`. Writes nothing itself (the pipeline writes `storyboard.json`). Never raises — on any Iris failure, every scene falls back to `classify`.
- `pipeline.produce(..., storyboard_fn=None)` threads the seam; the stage writes `pdir/storyboard.json` and marks itself done (resumable like every stage).

- [ ] **Step 1: Write the failing test**

```python
# studio/tests/test_storyboard.py
from studio import storyboard


def test_tag_uses_iris_layout_when_in_vocab():
    script = {"scenes": [{"scene_no": 1, "on_screen_text": "X", "claims": []},
                         {"scene_no": 2, "on_screen_text": "Y", "claims": []}]}
    fake_iris = lambda s, p: {"scenes": [{"scene_no": 1, "layout": "quote-card"},
                                         {"scene_no": 2, "layout": "big-number"}]}
    board = storyboard.tag_archetypes(script, None, iris_fn=fake_iris)
    tags = {s["scene_no"]: s["archetype"] for s in board["scenes"]}
    assert tags == {1: "quote-card", 2: "big-number"}


def test_tag_falls_back_to_classify_on_unknown_or_iris_failure():
    script = {"scenes": [{"scene_no": 1, "on_screen_text": "141 users", "claims": []}]}
    def boom(s, p):
        raise RuntimeError("iris down")
    board = storyboard.tag_archetypes(script, None, iris_fn=boom)
    # classify() sees a number → big-number
    assert board["scenes"][0]["archetype"] == "big-number"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_storyboard.py -v`
Expected: FAIL — `ModuleNotFoundError: studio.storyboard`.

- [ ] **Step 3: Write minimal implementation**

```python
# studio/storyboard.py
"""studio.storyboard — the archetype-tagging stage. Iris (art_engine.build_storyboard) plans
each scene's layout from her closed LAYOUTS vocab; we adopt that layout as the scene's
archetype tag (compose reads it). Heuristic classify() is the fallback when Iris is
unavailable or emits a layout outside the vocab. Never raises — a tagging gap degrades to
classify, never blocks the pipeline."""
from __future__ import annotations

from .compose import archetypes as A


def tag_archetypes(script: dict, pdir, *, iris_fn=None) -> dict:
    from . import engines
    iris_fn = iris_fn or (lambda s, p: engines.storyboard(s, p))
    scenes = script.get("scenes") or []
    try:
        board = iris_fn(script, pdir) or {}
        by_no = {s.get("scene_no"): s for s in (board.get("scenes") or [])}
    except Exception:
        by_no = {}
    out = []
    for sc in scenes:
        no = sc.get("scene_no")
        layout = (by_no.get(no) or {}).get("layout")
        archetype = layout if layout in A.ARCHETYPES else A.classify(sc)
        out.append({"scene_no": no, "archetype": archetype})
    return {"scenes": out}
```

```python
# studio/engines.py — add
def storyboard(script: dict, pdir=None) -> dict:
    """Run Iris's storyboard planner; returns {scenes:[{scene_no, layout, ...}]}."""
    mod = load_engine(config.ART_DIRECTOR_DIR, "art_engine")
    return mod.build_storyboard(script, None)
```

In `studio/pipeline.py`: add `"storyboard"` to `STAGES` between `"factcheck"` and `"vo"`; add `storyboard_fn=None` to `produce()`; insert the stage block (model it on the `vo` block at [studio/pipeline.py:418-439](../../../studio/pipeline.py#L418-L439)):

```python
    # 3b. storyboard — Iris tags each scene with an archetype (compose reads the tag)
    if _stage_status(state, "storyboard") != "done":
        from . import storyboard as sb_mod
        script = _read_json(pdir / "script.json", {})
        run_sb = storyboard_fn or (lambda s, d: sb_mod.tag_archetypes(s, d))
        board = run_sb(script, pdir)
        _write_json(pdir / "storyboard.json", board)
        state["artifacts"]["storyboard"] = "storyboard.json"
        _set_stage(state, "storyboard", "done")
        _log(state, "storyboard", "scene archetypes tagged",
             f"{len(board.get('scenes', []))} scenes")
        _save_state(pdir, state)
    if stop_after == "storyboard":
        state["status"] = "stopped_after_storyboard"
        _save_state(pdir, state)
        return state
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_storyboard.py -v`
Then the full pipeline suite (the new stage must not break resume/e2e):
Run: `python -m pytest studio/tests/test_pipeline.py studio/tests/test_pipeline_e2e.py -q`
Expected: PASS. If e2e seams don't inject `storyboard_fn`, add a `fake_storyboard` to their `_seams()` helper (mirroring `fake_motion_pass`).

- [ ] **Step 5: Commit**

```bash
git add studio/storyboard.py studio/engines.py studio/pipeline.py studio/tests/test_storyboard.py studio/tests/test_pipeline_e2e.py
git commit -m "feat(studio): storyboard stage — Iris tags each scene's archetype before VO"
```

---

### Task B3: Compose reads the archetype tag (registry dispatch, classify fallback)

**Files:**
- Modify: `studio/compose/__init__.py` (`_scene_beat`/`_plan_scene` read `storyboard.json` tags; dispatch to `archetypes.REGISTRY[tag]` when present, else the legacy beat / `classify`)
- Test: `studio/tests/test_compose_archetype_dispatch.py`

**Interfaces:**
- Consumes: `pdir/storyboard.json` (`{scenes:[{scene_no, archetype}]}`), `archetypes.REGISTRY`, `archetypes.classify`.
- Produces: the Composer resolves each scene's archetype = storyboard tag (if present) else `classify(scene)`; when `REGISTRY` has a builder for that archetype it uses it, otherwise it keeps the current generic beat (so this task is safe BEFORE Phase C builders exist).

- [ ] **Step 1: Write the failing test**

```python
# studio/tests/test_compose_archetype_dispatch.py
from studio.compose import archetypes as A
from studio.compose.__init__ import Composer  # adjust import to the public seam if needed


def test_composer_resolves_tag_then_classify(monkeypatch, tmp_path):
    # a scene with a storyboard tag uses it; without one, classify() decides.
    tagged = {"scene_no": 1}
    assert A_resolve(tagged, {"1": "quote-card"}) == "quote-card"
    assert A_resolve({"scene_no": 2, "on_screen_text": "141"}, {}) == "big-number"


def A_resolve(scene, tags):
    # mirrors the resolution the Composer must implement
    return tags.get(str(scene["scene_no"])) or A.classify(scene)
```

> Note: keep this test at the resolution-logic level (a small `_archetype_for(scene)` helper on the Composer) so it is unit-testable without a full compose. The implementer extracts `_archetype_for` and the test targets it directly.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest studio/tests/test_compose_archetype_dispatch.py -v`
Expected: FAIL until `_archetype_for` exists and is wired.

- [ ] **Step 3: Write minimal implementation**

Add to `Composer` an `_archetype_for(self, scene) -> str` that reads `self._storyboard` (loaded from `storyboard.json` in `author()`), returns the tag for the scene or `archetypes.classify(scene)`; and in `_scene_beat`, when `archetypes.REGISTRY` has the archetype, call its builder for the html+beats, else fall through to the existing beat logic. (Full wiring code is the implementer's; the contract is: tag wins, classify is fallback, missing-builder is safe.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest studio/tests/test_compose_archetype_dispatch.py studio/tests/test_compose_content_wired.py -q`
Expected: PASS (dispatch resolves; content still renders).

- [ ] **Step 5: Commit**

```bash
git add studio/compose/__init__.py studio/tests/test_compose_archetype_dispatch.py
git commit -m "feat(compose): dispatch scenes by archetype tag (storyboard), classify fallback"
```

---

# PHASE C — The archetype library (bespoke beats, one per task)

Each archetype is ONE task following this **template**. The reference's beat implementations are documented with exact line refs in `studio/GOLDEN_REFERENCE.md` — lift them into deterministic, parameterized factories.

**Per-archetype task template:**
1. **Add the beat factory** to `studio/compose/_motion.py` (a `function makeXxx(opts){...}` matching the `makeOrbitCluster` contract: single `opts`, `if(!tl)return null`, `if(!mount)return null`, deterministic, returns the DOM node) + a `BEATS` registry entry (so `ensure_motion_library` writes it back to the pack).
2. **Add the archetype builder** `studio/compose/archetypes/<name>.py`: `build(scene, ctx) -> {"html","beats_js","token"}` — the bespoke layout (using `_content` for text/claims) + the `beats_js` calling the factory, and call `archetypes.register("<layout>", build, "<token>")` so the parity invariant holds.
3. **Grow `_BEAT_TOKENS`** in `studio/gate/parse.py` with the new token (SAME commit — the invariant).
4. **Tests:** (a) determinism — the emitted `beats_js` + factory contain no `Math.random`/`Date.now`/`fetch`; (b) parity — `test_archetype_token_parity` still green; (c) motion_variety — a scene tagged with this archetype produces a DISTINCT `scene_signature` vs other archetypes.
5. **Commit** `_motion.py`, `archetypes/<name>.py`, `gate/parse.py`, the tests.

**Build order (highest gate-leverage first), each lifting from GOLDEN_REFERENCE.md:**

| Task | Archetype (Iris layout) | Bespoke beat lifted | Token | Reference |
|---|---|---|---|---|
| C1 | `quote-card` | parallax quote cards + highlighter-swipe under the key phrase | `quote-cards` | GOLDEN_REFERENCE §3, reference S5 |
| C2 | `big-number` | counter tick + stat-card punch-in | `count-up` | reference S1/S3 |
| C3 | `list-stack` | sequential checkmark draw + (optional grayscale drain) | `checklist` | reference S8 |
| C4 | `data-chart` | calendar-grid fill cell-by-cell then crumble to grain | `calendar-crumble` | reference S3 |
| C5 | `comparison-2up` | shatter-bar (focus bar → drifting shards) + RGB-split glitch | `shatter` | reference S6 |
| C6 | `centered-statement` | strike-through + spray-over restatement + stamp | `strike` | reference S7 |
| C7 | `full-bleed-image` | simulated device mockup: fake cursor + infinite-feed loop + slot-reel | `device-loop` | reference S4 |
| C8 | `title-card` | title-with-portrait: halftone silhouette self-draw + orbiting icons settle | `orbit` (exists) | reference S2 |
| C9 | `lower-third` | signature-outro: self-writing signature + handle row | `signature` | reference S9 |
| C10 | `split-screen` | two-panel tile w/ internal image parallax | `tile-parallax` | GOLDEN_REFERENCE §3 (T-TILE) |
| C11 | `map-focus` | map-draw self-drawing route + pin pop | `map-draw` | Iris EFFECTS `map-draw` |
| C12 | `timeline` | timeline rail with sequential node reveals | `timeline-rail` | reference ticker tech |
| C13 | `diagram` | flat in-HTML/SVG diagram reveal (nodes/edges draw in) | `diagram-draw` | SVG experiments (memory) |

**Worked example — Task C1 (`quote-card`), fully specified:**

**Files:** Modify `studio/compose/_motion.py`; Create `studio/compose/archetypes/quote_cards.py`; Modify `studio/gate/parse.py`; Test `studio/tests/test_archetype_quote_cards.py`.

- [ ] **Step 1: failing test**

```python
# studio/tests/test_archetype_quote_cards.py
from studio.compose import archetypes as A
from studio.compose.archetypes import quote_cards  # registers on import
from studio.gate import parse as P


def test_quote_card_registered_with_token():
    assert "quote-card" in A.REGISTRY
    assert A.token_for("quote-card") == "quote-cards"
    assert "quote-cards" in {n for n, _ in P._BEAT_TOKENS}


def test_quote_card_build_is_deterministic_and_renders_quote():
    scene = {"scene_no": 5, "on_screen_text": "THEY ADMIT IT",
             "claims": [{"claim_id": "c1",
                         "text": '"behavioral cocaine." — Aza Raskin', "source_ref": "F1"}]}
    out = A.REGISTRY["quote-card"](scene, {"sid": "s5", "spray": "#2e5e1f",
                                           "width": 1920, "height": 1080})
    blob = out["html"] + out["beats_js"]
    assert "behavioral cocaine" in out["html"].lower() and "Aza Raskin" in out["html"]
    assert out["token"] == "quote-cards"
    for bad in ("Math.random", "Date.now", "fetch(", "new Date"):
        assert bad not in blob


def test_quote_card_signature_is_distinct():
    # a scene built by this archetype must read as the 'quote-cards' beat, not 'plain'
    out = A.REGISTRY["quote-card"]({"scene_no": 5, "claims": [
        {"text": '"x." — Y'}]}, {"sid": "s5", "spray": "#2e5e1f", "width": 1920, "height": 1080})
    sig = P.scene_signature(out["html"], out["beats_js"], "s5")
    assert sig == "quote-cards"
```

- [ ] **Step 2: run → fail** (`ModuleNotFoundError: ...archetypes.quote_cards`).

- [ ] **Step 3: implement** — add `makeHighlighterSwipe` is already in `_motion.py` BEATS (`highlighter-swipe`); add a `quoteCards` factory if a bespoke parallax bob is wanted, else reuse `makeHighlighterSwipe` over `_content.render_claims` quote cards. The builder:

```python
# studio/compose/archetypes/quote_cards.py
"""Archetype: quote-card — parallax attributed-quote cards with a highlighter swipe under
the key phrase (reference S5). Deterministic; lifts GOLDEN_REFERENCE §3."""
from __future__ import annotations

from .. import _content
from . import register


def build(scene: dict, ctx: dict) -> dict:
    sid = ctx["sid"]
    html = _content.render_claims(scene)   # quote cards w/ bylines (Phase A)
    # highlighter swipe under each quote card's key phrase; makeHighlighterSwipe is in _motion BEATS
    beats = (
        f'        (function(){{ document.querySelectorAll("#{sid} .quote-card").forEach('
        f'function(card, k){{ makeHighlighterSwipe({{ tl: tl, mount: card.querySelector(".quote-body"), '
        f'at: {ctx.get("at", 0.6)} + k*0.5, color: SPRAY, dur: 0.5 }}); '
        f'tl.from(card, {{ y: 36, opacity: 0, duration: 0.6, ease: "power3.out" }}, '
        f'{ctx.get("at", 0.6)} + k*0.5); }}); }})();'
    )
    return {"html": html, "beats_js": beats, "token": "quote-cards"}


register("quote-card", build, "quote-cards")
```

In `studio/gate/parse.py` `_BEAT_TOKENS`, ensure a `("quote-cards", r'quoteCards|makeHighlighterSwipe|class="[^"]*quote-card')` entry exists (so a quote-card scene reads as `quote-cards`, distinct from `plain`).

- [ ] **Step 4: run → pass**; then `python -m pytest studio/tests/test_archetype_token_parity.py studio/tests/test_archetype_quote_cards.py -q`.

- [ ] **Step 5: commit** `studio/compose/_motion.py studio/compose/archetypes/quote_cards.py studio/gate/parse.py studio/tests/test_archetype_quote_cards.py`.

Repeat the template for C2–C13 (one task each), per the table above.

---

### Task C-FINAL: Re-render `dark-truth-v2` and measure the climb

**Files:** none (verification task); may add `studio/tests/test_compose_variety_regression.py`.

- [ ] **Step 1:** Re-run the full back half on the flat draft:
```bash
python -m studio.run produce --resume dark-truth-v2   # re-storyboard → re-compose → re-draft
```
or recompose+rerender directly if resume isn't wired for re-tag.

- [ ] **Step 2:** Score it:
```bash
python -m studio.gate.calibrate
python -c "from studio.gate import score; sc=score(slug='dark-truth-v2', polish=False); \
print(sc['verdict'], sc['overall']); [print(d['name'], d['score'], d['passed']) for d in sc['dimensions']]"
```
Expected: `motion_variety` and `content_fidelity` are materially higher than the Plan-1 baseline (`motion_variety 0.37→`, `content_fidelity` passes); `dark-truth-v2` moves from BLOCKED toward PASS. Record the before/after in the commit message.

- [ ] **Step 3:** Add a regression test asserting a composed `dark-truth-v2` (or a representative fixture) yields ≥ N distinct beat signatures, so variety can't silently regress.

- [ ] **Step 4:** Commit the regression test + a CHANGELOG note.

---

## Self-Review

**1. Spec coverage** (against the design doc Part 2): rich ~12–15 archetypes → Phase C table (13, = Iris LAYOUTS); parameterized within archetype → builders consume `_content` + scene data (stats 1/3/5, quotes 1–N); growing library → `register()` + the per-archetype task template + `ensure_motion_library` write-back; lift real implementations → Phase C lifts from `GOLDEN_REFERENCE.md` (the re-timer/transitions/ticker/grain are already in partials); Iris tags the archetype in a storyboard stage → Phase B; closed-vocab parity-tested → Task B1 + the invariant test; content_fidelity FIRST → Phase A precedes Phase C (CEO directive); archetype-ships-with-token invariant → Task B1 + every Phase C task step 3.

**2. Placeholder scan:** Phase A and Phase B tasks carry complete code. Phase C is a fully-specified TEMPLATE with one archetype (C1) fully coded and a per-archetype spec table (beat, token, reference) — the repetitive builders are deliberately templated, not vague: each names its file, factory, token, reference, and tests. This is the honest shape of a "growing library" and avoids 13× duplicated inlined GSAP; the implementer has the contract + a worked example.

**3. Type consistency:** `build(scene, ctx) -> {"html","beats_js","token"}` is uniform across Phase C; `archetypes.register(archetype, builder, token)` / `token_for` / `classify` / `REGISTRY` / `ARCHETYPES` are consistent between B1, B3, and C; `storyboard.tag_archetypes(script, pdir, *, iris_fn)` and `engines.storyboard`/`iris_layouts` match between B1 and B2; `parse.scene_signature(block_html, choreo_js, sid)` matches the (post-Plan-1) signature.

**4. Sequencing guard:** Phase A is independently shippable and improves `content_fidelity` immediately (it's parsed from `index.html`, no re-render needed). Phase B is safe before Phase C (missing builders fall through to the existing beat). Phase C archetypes are independent of each other (one per task), each gated by determinism + parity + a distinct-signature test, so `motion_variety` climbs monotonically and can't regress.
