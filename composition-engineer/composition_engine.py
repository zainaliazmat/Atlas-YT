"""Mason's engine: the upstream artifacts -> deterministic per-scene HyperFrames
projects, gated and draft-rendered.

Mason BUILDS to spec. The Art Director (Iris) decides the look; Mason lays exactly
the bricks she drew — he never redesigns. This engine is PURE, DETERMINISTIC code:
it calls no LLM. Given {script scene, style_guide, storyboard scene + effects,
asset_manifest assets, narration transcript segment} it emits one
`scenes/scene-NN/index.html` that obeys the HyperFrames contract and the determinism
contract, then runs the auto-gate (self-scan -> lint -> validate -> inspect) before
spending a render.

THE SPLIT (mirrors the siblings): there is no "brain" here — the taste calls were
already made by Iris. PURE CODE enforces every hard invariant and assembles the HTML,
so the whole engine is unit-testable with no network and no render:

  1. the composition root <div data-composition-id ...> is the FIRST body element;
     all layers/overlays nest INSIDE it (overlays are absolute children, never
     siblings before the root)
  2. every element carrying data-start/data-duration also carries class="clip"
  3. initial visibility uses CSS opacity:0, never gsap.set
  4. all motion lives on ONE paused master timeline registered as
     window.__timelines["scene-NN"]; build-time .from()/.to()/.fromTo() only
  5. the 12fps stutter is steps(round(12 * duration)) — 12 is CONSTANT, decoupled
     from the render fps (render fps = style_guide.fps, default 30)
  6. EXACTLY the signature-beat scene carries the highlighter-FFD000 sweep
  7. motion.max_per_scene is respected: (non-cut transition + effects) <= budget,
     and the mandatory signature highlighter is never trimmed off its scene
  8. a partial exists for EVERY token across all four axes (layouts, transitions,
     effects, textures); an unknown/out-of-vocabulary token is REJECTED at input
     validation, never silently dropped
  9. every asset URI is local; an http(s):// URI is a hard input-validation block;
     a missing local file becomes a deterministic styled placeholder panel (no fetch)

Three axes are kept strictly separate (matching Iris's vocabulary):
  - LAYOUTS   = composition (where things sit)            -> storyboard scene.layout
  - TEXTURES  = the always-on global hand-made overlay    -> style_guide.textures
  - EFFECTS   = per-scene, varying techniques             -> storyboard scene.effects
  - TRANSITIONS apply at the render_video ASSEMBLY step (between two scenes), never
    inside a scene body (scene bodies are transition-clean).

Decoupling boundary: this engine emits plain dicts and HTML and NEVER imports atlas.
Atlas stamps `schema_version` on the composition_manifest and validates it against
the frozen contract at the adapter boundary.
"""
from __future__ import annotations

import html as _html
import os
import pathlib
import re
import shutil
import time

import chat_state  # atomic_write_json / load_json — corruption-safe file helpers
import hf_tools    # subprocess wrappers around the HyperFrames CLI (gate + render)
                   # NOTE: imported at TOP LEVEL (not lazily) so it resolves during the
                   # atlas loader's import window — a lazy import at call time would fail
                   # after the loader restores sys.path. hf_tools is stdlib-only, so this
                   # is harmless for the pure unit tests too.

HERE = pathlib.Path(__file__).parent
SKILL = (HERE / "SKILL.md").read_text()
SOUL = (HERE / "soul" / "SOUL.md").read_text()
MEMORY = HERE / "memory.json"

# The composition_manifest schema_version Mason stamps locally; in the pipeline,
# atlas re-stamps authoritatively at the adapter boundary.
SCHEMA_VERSION = "1.0"

# ----------------------------------------------------------------------
# Canvas + render constants (verified against HyperFrames v0.6.115, Phase 0)
# ----------------------------------------------------------------------
CANVAS_W, CANVAS_H = 1920, 1080
GSAP_CDN = "https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"

DEFAULT_FPS = 30                # base render fps (style_guide.fps default)
FPS_MIN, FPS_MAX = 12, 60
STUTTER_FPS = 12                # the Vox stutter cadence — CONSTANT, NOT the render fps
DEFAULT_MAX_PER_SCENE = 2       # Iris's restrained motion budget (when absent)
DEFAULT_DURATION = 6.0          # scene fallback duration when nothing upstream says
DEFAULT_TEXTURES = ("paper", "grain")  # baseline overlay when the style guide omits it

SIGNATURE_HIGHLIGHT = "#FFD000"
SIGNATURE_EFFECT = "highlighter-FFD000"

# ----------------------------------------------------------------------
# THE FINITE VOCABULARIES — must mirror the Art Director's vocabulary exactly.
# Mason implements a partial for EVERY token; an unknown token is rejected, not
# dropped. (Verified equal to art-director/art_engine.py at build time by a test.)
# ----------------------------------------------------------------------
LAYOUTS = (
    "centered-statement", "split-screen", "full-bleed-image", "lower-third",
    "data-chart", "quote-card", "map-focus", "list-stack", "comparison-2up",
    "title-card",
)
TRANSITIONS = ("cut", "dip-to-black", "push", "wipe", "match-cut")
EFFECTS = (
    "stutter-12fps", "stepped-ease", SIGNATURE_EFFECT, "map-draw",
    "chromatic-aberration", "push-in", "parallax",
)
TEXTURES = ("paper", "grain", "halftone", "vignette", "scanlines")

# ----------------------------------------------------------------------
# BRAND CHIPS (issue #2, Direction A) — model/logo shots are rendered here as
# typographic HTML/SVG chips, NEVER sourced (the logos are trademarked and absent
# from the Asset Sourcer's CC0/PD/CC allowlist, where a keyword fallback ships
# irrelevant stock). This registry is the canonical home; the Art Director mirrors the
# alias list for its auto-tagger (guarded by a cross-engine test). Each entry:
#   aliases  — lowercased names matched (as delimited units) in shot content/asset_ref
#   display  — the chip's typographic label
#   color    — the brand accent (border + text)
#   logo_svg — OPTIONAL inline SVG; when present Mason renders it INSTEAD of the label.
#              v1 ships chips only (no curated SVGs); add real logos as data, no code change.
BRAND_CHIPS = {
    "openai":    {"aliases": ("gpt-4o", "gpt4o", "gpt-4", "gpt", "chatgpt", "openai"),
                  "display": "GPT-4o", "color": "#10A37F", "logo_svg": ""},
    "anthropic": {"aliases": ("claude", "anthropic"),
                  "display": "Claude", "color": "#D97757", "logo_svg": ""},
    "google":    {"aliases": ("gemini", "google gemini"),
                  "display": "Gemini", "color": "#4285F4", "logo_svg": ""},
    "deepseek":  {"aliases": ("deepseek", "deep seek"),
                  "display": "DeepSeek", "color": "#4D6BFE", "logo_svg": ""},
}

# Layouts whose partial embeds a media slot (call _media_html). Brand chips land in that
# slot for these; for the text-only layouts they are injected as a centered focal layer.
MEDIA_SLOT_LAYOUTS = frozenset({
    "split-screen", "full-bleed-image", "lower-third", "data-chart", "map-focus",
})


# ======================================================================
# Memory — a log of past composition runs (provider-agnostic, on our disk)
# ======================================================================
def load_memory():
    return chat_state.load_json(MEMORY, {"runs": []})


def save_memory(mem):
    chat_state.atomic_write_json(MEMORY, mem)


# ======================================================================
# Small pure helpers (unit-tested)
# ======================================================================
def clamp_fps(value) -> int:
    """An int fps clamped to [FPS_MIN, FPS_MAX]; DEFAULT_FPS when unusable."""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return DEFAULT_FPS
    return max(FPS_MIN, min(FPS_MAX, v))


def stutter_steps(duration_sec, stutter_fps: int = STUTTER_FPS) -> int:
    """GSAP steps(n) count for the 12fps stutter: n = round(stutter_fps * duration).

    The stutter cadence (12fps) is CONSTANT and decoupled from the render fps —
    steps(round(30*dur)) would produce no visible stutter. Always >= 1.
    """
    try:
        n = int(round(float(stutter_fps) * float(duration_sec)))
    except (TypeError, ValueError):
        n = stutter_fps
    return max(1, n)


def _names(items) -> list[str]:
    """Pull the token names out of a list of bare strings / {name, params} dicts."""
    out = []
    for it in items or []:
        if isinstance(it, str) and it.strip():
            out.append(it.strip())
        elif isinstance(it, dict) and str(it.get("name", "")).strip():
            out.append(str(it["name"]).strip())
    return out


def _as_named(items) -> list[dict]:
    """Normalize a list of strings / {name,params} into {name, params} dicts."""
    out = []
    for it in items or []:
        if isinstance(it, str) and it.strip():
            out.append({"name": it.strip(), "params": {}})
        elif isinstance(it, dict) and str(it.get("name", "")).strip():
            params = it.get("params") if isinstance(it.get("params"), dict) else {}
            out.append({"name": str(it["name"]).strip(), "params": params})
    return out


def scene_duration(script_scene: dict, segments: list[dict]) -> float:
    """Scene length: prefer the narration span (sync), else the script estimate."""
    segs = [s for s in (segments or []) if s.get("scene_no") == script_scene.get("scene_no")]
    if segs:
        start = min(float(s.get("start_sec", 0.0)) for s in segs)
        end = max(float(s.get("end_sec", 0.0)) for s in segs)
        if end > start:
            return round(end - start, 3)
    try:
        d = float(script_scene.get("duration_est_sec", 0.0))
        if d > 0:
            return round(d, 3)
    except (TypeError, ValueError):
        pass
    return DEFAULT_DURATION


def scene_captions(segments: list[dict], scene_no: int) -> list[dict]:
    """Caption reveals for one scene, offset to the scene's LOCAL timeline.

    narration.transcript.json segment start/end are GLOBAL (cumulative across the
    whole video). A scene composition's timeline is local (starts at 0), so we
    subtract the scene's first-segment start. Clamped to non-negative.
    """
    segs = sorted((s for s in (segments or []) if s.get("scene_no") == scene_no),
                  key=lambda s: float(s.get("start_sec", 0.0)))
    if not segs:
        return []
    base = min(float(s.get("start_sec", 0.0)) for s in segs)
    out = []
    for s in segs:
        ls = max(0.0, round(float(s.get("start_sec", 0.0)) - base, 3))
        le = max(ls, round(float(s.get("end_sec", 0.0)) - base, 3))
        text = str(s.get("text", "")).strip()
        if text:
            out.append({"start": ls, "duration": round(le - ls, 3), "text": text})
    return out


def trim_effects(effects: list[dict], transition: str, max_per_scene: int,
                 signature: bool) -> list[dict]:
    """Respect the motion budget: (non-cut transition + effects) <= max_per_scene.

    The mandatory signature highlighter is kept first and never trimmed off its
    scene; other effects are dropped from the tail until the budget holds.
    """
    budget = max(1, int(max_per_scene or DEFAULT_MAX_PER_SCENE))
    transition_cost = 0 if (transition or "cut") == "cut" else 1
    room = max(0, budget - transition_cost)
    if signature:
        # The mandatory #FFD000 highlighter is the signature beat — NEVER trimmed off
        # its scene, even when the budget is already spent. Others fill the rest.
        highlighter = next((e for e in effects if e["name"] == SIGNATURE_EFFECT),
                           {"name": SIGNATURE_EFFECT, "params": {}})
        others = [e for e in effects if e["name"] != SIGNATURE_EFFECT]
        return [highlighter] + others[:max(0, room - 1)]
    # the signature sweep only ever lives on the signature scene
    fx = [e for e in effects if e["name"] != SIGNATURE_EFFECT]
    return fx[:room]


# ======================================================================
# The determinism SELF-SCAN — owns EXACTLY the three rules HyperFrames `lint`
# misses (Phase 0 verified lint catches Math.random/Date.now/repeat:-1 itself).
# Runs in pure Python BEFORE the CLI gate.
# ======================================================================
_NET_RE = re.compile(
    r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|importScripts)\s*\(|"
    r"navigator\s*\.\s*sendBeacon\s*\(")
_SVG_ANIM_RE = re.compile(r"<animate(?:Transform|Motion)?\b", re.IGNORECASE)
_GSAP_SET_RE = re.compile(r"gsap\s*\.\s*set\s*\(")
_ASYNC_MARKERS = (".then(", ".catch(", ".finally(", "setTimeout(", "setInterval(",
                  "requestAnimationFrame(", "addEventListener(")


def scan_determinism(html_text: str) -> list[dict]:
    """Return a list of determinism violations Mason owns (lint won't catch these).

    Each: {"rule": ..., "message": ..., "match": <snippet>}.
    - render-time fetch / network call in script
    - animated SVG filter (SMIL <animate*> — breaks frame-exact seeking)
    - late/async gsap.set (initial/visibility state mutated outside build time)
    """
    violations: list[dict] = []
    for m in _NET_RE.finditer(html_text):
        violations.append({
            "rule": "render_time_fetch",
            "message": "render-time network call — assets must be local; nothing is "
                       "fetched at render time.",
            "match": html_text[m.start():m.start() + 40]})
    for m in _SVG_ANIM_RE.finditer(html_text):
        violations.append({
            "rule": "animated_svg_filter",
            "message": "SMIL <animate> in SVG — time-based animation breaks the "
                       "frame-seek engine. Animate via the paused GSAP timeline.",
            "match": html_text[m.start():m.start() + 40]})
    for m in _GSAP_SET_RE.finditer(html_text):
        pre = html_text[max(0, m.start() - 160):m.start()]
        if any(marker in pre for marker in _ASYNC_MARKERS):
            violations.append({
                "rule": "late_async_gsap_set",
                "message": "gsap.set inside an async/deferred callback — set initial "
                           "state with CSS and place all motion at build time.",
                "match": html_text[m.start():m.start() + 40]})
    return violations


# ======================================================================
# Input validation — never spend a render on something we can't compose
# ======================================================================
def validate_inputs(script: dict, style_guide: dict, storyboard: dict,
                    asset_manifest: dict) -> tuple[bool, list[str]]:
    """Return (ok, errors). Hard-blocks: no scenes; unknown vocabulary tokens (never
    silently dropped); any http(s):// asset URI (no render-time fetch)."""
    errors: list[str] = []

    scenes = (script or {}).get("scenes")
    if not isinstance(scenes, list) or not scenes:
        errors.append("script has no scenes — nothing to compose.")

    # Vocabulary: every token across all four axes must be known.
    for t in _names((style_guide or {}).get("textures")):
        if t not in TEXTURES:
            errors.append(f"unknown texture token {t!r} (not in the texture vocabulary).")
    for sc in (storyboard or {}).get("scenes", []):
        n = sc.get("scene_no")
        layout = sc.get("layout")
        if layout is not None and layout not in LAYOUTS:
            errors.append(f"scene {n}: unknown layout token {layout!r}.")
        trans = sc.get("transition")
        if trans is not None and trans not in TRANSITIONS:
            errors.append(f"scene {n}: unknown transition token {trans!r}.")
        for fx in _names(sc.get("effects")):
            if fx not in EFFECTS:
                errors.append(f"scene {n}: unknown effect token {fx!r}.")

    # Assets: no remote URIs (Phase 0 proved a remote 404 silently ships a broken,
    # non-reproducible MP4). Missing-local is NOT blocked here — it becomes a
    # placeholder panel + an integrity flag for the human gate.
    for a in (asset_manifest or {}).get("assets", []):
        uri = str(a.get("uri", "")).strip()
        if uri.lower().startswith(("http://", "https://")):
            errors.append(f"asset {a.get('asset_id')!r} (scene {a.get('scene_no')}): "
                          f"remote URI {uri!r} — assets must be localized before composing.")
    return (not errors), errors


# ======================================================================
# Asset resolution
# ======================================================================
def resolve_scene_assets(asset_manifest: dict, scene_no: int,
                         pdir: pathlib.Path) -> list[dict]:
    """Resolve the manifest assets for one scene into render-ready descriptors.

    Each: {asset_id, type, uri, status, present(bool), placeholder(bool),
    integrity_flag(str|None), label}. `present` means the local file exists;
    `integrity_flag` is set when a sourced/cleared asset's file is MISSING (an
    integrity problem to surface at the human gate), as distinct from a Magpie-
    declared `placeholder` status (expected, not an error).
    """
    out = []
    for a in (asset_manifest or {}).get("assets", []):
        if a.get("scene_no") != scene_no:
            continue
        uri = str(a.get("uri", "")).strip()
        status = str(a.get("status", "placeholder"))
        present = bool(uri) and (pdir / uri).exists()
        declared_placeholder = status == "placeholder"
        placeholder = declared_placeholder or not present
        integrity_flag = None
        if not present and not declared_placeholder:
            integrity_flag = (f"{status} asset {a.get('asset_id')!r} has no local file "
                              f"at {uri!r}")
        out.append({
            "asset_id": a.get("asset_id"), "type": a.get("type", "image"),
            "uri": uri, "status": status, "present": present,
            "placeholder": placeholder, "integrity_flag": integrity_flag,
            "label": str(a.get("asset_id") or "asset"),
        })
    return out


def _copy_asset_local(pdir: pathlib.Path, scene_dir: pathlib.Path, asset: dict) -> str | None:
    """Copy a present local asset into the scene project's own assets/ dir for a
    self-contained standalone project. Returns the in-project relative src, or None."""
    if not asset.get("present"):
        return None
    src = pdir / asset["uri"]
    dest_dir = scene_dir / "assets"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    try:
        shutil.copyfile(src, dest)
    except OSError:
        return None
    return f"assets/{src.name}"


# ======================================================================
# THE TECHNIQUE LIBRARY — one partial per token, all four axes.
# Builders return fragments: {"css": str, "html": str, "tl": [js lines]}.
# An entry exists for EVERY vocabulary token (asserted by a test).
# ======================================================================
def _esc(text) -> str:
    return _html.escape(str(text or ""), quote=True)


def detect_brands(text: str) -> list[str]:
    """Return the registry brand keys named in `text`, ordered by first appearance, deduped.

    Each alias is matched as a delimited unit (not inside a word), so 'gpt' fires on
    'gpt-4o' and 'gpt_reasoning' but not on a larger token. Pure + deterministic.
    """
    low = (text or "").lower()
    pos: dict[str, int] = {}
    for key, spec in BRAND_CHIPS.items():
        best = None
        for a in spec["aliases"]:
            m = re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", low)
            if m and (best is None or m.start() < best):
                best = m.start()
        if best is not None:
            pos[key] = best
    return [k for k, _ in sorted(pos.items(), key=lambda kv: kv[1])]


def scene_brand_keys(shots) -> list[str]:
    """Brand keys named across a scene's storyboard shots (content + asset_ref), ordered.

    Detection is by model NAME, independent of shot.kind, so the existing storyboards
    (kind:'graphic'/'panel'/…) render chips too, alongside Iris's newer kind:'brand'.
    """
    text = " ".join(f"{s.get('content', '')} {s.get('asset_ref', '')}"
                    for s in (shots or []) if isinstance(s, dict))
    return detect_brands(text)


def render_brand_chips(keys, *, cls: str = "brand-chips") -> str:
    """Render one styled chip per brand key (inline SVG logo if the entry has one, else
    the typographic display name in the brand color). Several keys -> a 'matchup' row."""
    chips = []
    for k in keys or []:
        b = BRAND_CHIPS.get(k)
        if not b:
            continue
        inner = b.get("logo_svg") or f'<span class="brand-chip-name">{_esc(b["display"])}</span>'
        chips.append(f'<div class="brand-chip" style="--brand:{b["color"]}">{inner}</div>')
    return f'<div class="{cls}">' + "".join(chips) + "</div>"


def _media_html(ctx: dict, cls: str = "media") -> str:
    """Brand chips (if this is a brand scene) take the media slot; else an <img> for a
    present local asset, else a deterministic placeholder panel.

    Brand chips take PRECEDENCE over any sourced asset: for a brand scene the sourced
    image is irrelevant by construction (the logos are un-sourceable), so the chip wins.
    """
    if ctx.get("brand_keys"):
        return f'<div class="{cls} brand-media">{render_brand_chips(ctx["brand_keys"])}</div>'
    asset = next((a for a in ctx["assets"] if a["type"] in ("image", "video", "data-viz")),
                 None)
    if asset and asset.get("src_rel"):
        return f'<img class="{cls}" src="{_esc(asset["src_rel"])}" alt="" />'
    label = _esc(asset["label"]) if asset else "visual"
    return (f'<div class="{cls} placeholder-panel" role="img" aria-label="{label}">'
            f'<span>{label}</span></div>')


# ---- LAYOUTS (composition) -------------------------------------------
def _layout_centered(ctx):
    return {"css": "", "html":
            f'<div class="layout centered-statement"><h1 class="scene-title">'
            f'{_esc(ctx["title"])}</h1></div>', "tl": []}


def _layout_split(ctx):
    return {"css": "", "html":
            f'<div class="layout split-screen"><div class="split-pane left">'
            f'{_media_html(ctx)}</div><div class="split-pane right">'
            f'<h1 class="scene-title">{_esc(ctx["title"])}</h1></div></div>', "tl": []}


def _layout_full_bleed(ctx):
    return {"css": "", "html":
            f'<div class="layout full-bleed-image">{_media_html(ctx, "media bleed")}'
            f'<div class="lower-strip"><h1 class="scene-title">{_esc(ctx["title"])}'
            f'</h1></div></div>', "tl": []}


def _layout_lower_third(ctx):
    return {"css": "", "html":
            f'<div class="layout lower-third">{_media_html(ctx, "media bleed")}'
            f'<div class="name-strip"><h2 class="scene-title">{_esc(ctx["title"])}'
            f'</h2></div></div>', "tl": []}


def _layout_data_chart(ctx):
    return {"css": "", "html":
            f'<div class="layout data-chart"><div class="chart-frame">{_media_html(ctx)}'
            f'</div><h2 class="scene-title">{_esc(ctx["title"])}</h2></div>', "tl": []}


def _layout_quote(ctx):
    return {"css": "", "html":
            f'<div class="layout quote-card"><blockquote class="scene-title">'
            f'{_esc(ctx["title"])}</blockquote></div>', "tl": []}


def _layout_map_focus(ctx):
    return {"css": "", "html":
            f'<div class="layout map-focus"><div class="map-frame">{_media_html(ctx)}'
            f'</div><h2 class="scene-title">{_esc(ctx["title"])}</h2></div>', "tl": []}


def _layout_list_stack(ctx):
    return {"css": "", "html":
            f'<div class="layout list-stack"><h2 class="scene-title">'
            f'{_esc(ctx["title"])}</h2><ul class="stack"><li>•</li></ul></div>', "tl": []}


def _layout_comparison(ctx):
    return {"css": "", "html":
            f'<div class="layout comparison-2up"><div class="cmp myth">myth</div>'
            f'<div class="cmp fact"><h2 class="scene-title">{_esc(ctx["title"])}</h2>'
            f'</div></div>', "tl": []}


def _layout_title_card(ctx):
    return {"css": "", "html":
            f'<div class="layout title-card"><h1 class="scene-title display">'
            f'{_esc(ctx["title"])}</h1></div>', "tl": []}


LAYOUT_BUILDERS = {
    "centered-statement": _layout_centered, "split-screen": _layout_split,
    "full-bleed-image": _layout_full_bleed, "lower-third": _layout_lower_third,
    "data-chart": _layout_data_chart, "quote-card": _layout_quote,
    "map-focus": _layout_map_focus, "list-stack": _layout_list_stack,
    "comparison-2up": _layout_comparison, "title-card": _layout_title_card,
}


# ---- TEXTURES (global, always-on overlays — static CSS, mix-blend-mode) ----
def _texture(name, blend, css_bg):
    def build(params):
        return {"css": f".tex-{name}{{position:absolute;inset:0;pointer-events:none;"
                       f"mix-blend-mode:{blend};{css_bg}}}",
                "html": f'<div class="overlay tex-{name}"></div>', "tl": []}
    return build


TEXTURE_BUILDERS = {
    # static (no infinite CSS animation) — deterministic under frame-seek
    "paper": _texture("paper", "multiply",
                      "background:repeating-linear-gradient(0deg,#0000,#0000 3px,#00000008 4px);opacity:.5;"),
    "grain": _texture("grain", "overlay",
                      "background:radial-gradient(#fff2 1px,#0000 1px);background-size:3px 3px;opacity:.35;"),
    "halftone": _texture("halftone", "multiply",
                         "background:radial-gradient(#0003 28%,#0000 30%);background-size:6px 6px;opacity:.4;"),
    "vignette": _texture("vignette", "multiply",
                         "background:radial-gradient(ellipse at center,#0000 55%,#0009 100%);"),
    "scanlines": _texture("scanlines", "overlay",
                          "background:repeating-linear-gradient(0deg,#0000,#0000 2px,#0000000f 3px);opacity:.5;"),
}


# ---- EFFECTS (per-scene motion on the paused timeline) ----------------
def _fx_stutter(ctx):
    steps = stutter_steps(ctx["duration"])
    return {"css": ".fx-stutter-tick{position:absolute;top:60px;left:5%;width:90%;height:4px;"
                   "background:#FFFFFF22;}.fx-stutter-fill{height:100%;width:100%;"
                   "transform-origin:left center;background:#FFFFFF66;}",
            "html": '<div class="fx-stutter-tick"><div class="fx-stutter-fill"></div></div>',
            "tl": [f'tl.fromTo(".fx-stutter-fill",{{scaleX:0}},'
                   f'{{scaleX:1,duration:{ctx["duration"]:.3f},ease:"steps({steps})"}},0);']}


def _fx_stepped(ctx):
    return {"css": "", "html": "",
            "tl": ['tl.fromTo(".scene-title",{x:-10},{x:0,duration:0.6,'
                   'ease:"steps(6)"},0);']}


def _fx_highlighter(ctx):
    color = ctx["highlight"]
    return {"css": f".hl-sweep{{position:absolute;left:-6px;right:-6px;top:0;bottom:0;"
                   f"background:{color};z-index:-1;transform-origin:left center;}}"
                   ".scene-title{position:relative;}",
            "html": '',  # injected by the title wrapper below
            "tl": ['tl.fromTo(".hl-sweep",{scaleX:0},{scaleX:1,duration:0.5,'
                   'ease:"power1.inOut"},0.45);']}


def _fx_map_draw(ctx):
    return {"css": ".map-draw-svg{position:absolute;inset:0;}.map-path{fill:none;"
                   "stroke:#FFD000;stroke-width:6;stroke-dasharray:1;stroke-dashoffset:1;}",
            "html": '<svg class="map-draw-svg" viewBox="0 0 1920 1080" '
                    'preserveAspectRatio="none"><path class="map-path" pathLength="1" '
                    'd="M240,840 C700,700 1100,500 1680,260" /></svg>',
            "tl": [f'tl.to(".map-path",{{strokeDashoffset:0,duration:1.2,'
                   f'ease:"power1.inOut"}},0.2);']}


def _fx_chromatic(ctx):
    # restrained, STATIC RGB split (no animated SVG filter) on the title
    return {"css": ".scene-title.chroma{text-shadow:-2px 0 #ff004055,2px 0 #00e5ff55;}",
            "html": "", "tl": ['document.querySelector(".scene-title")'
                               '&&document.querySelector(".scene-title")'
                               '.classList.add("chroma");']}


def _fx_push_in(ctx):
    return {"css": ".push-host{position:absolute;inset:0;overflow:hidden;}"
                   ".push-host .media{will-change:transform;}",
            "html": "",
            "tl": [f'tl.fromTo(".media",{{scale:1.0}},{{scale:1.08,'
                   f'duration:{ctx["duration"]:.3f},ease:"none",transformOrigin:"50% 50%"}},0);']}


def _fx_parallax(ctx):
    return {"css": ".px-layer{position:absolute;inset:0;}",
            "html": '<div class="px-layer px-back"></div>',
            "tl": [f'tl.fromTo(".px-back",{{yPercent:-3}},{{yPercent:3,'
                   f'duration:{ctx["duration"]:.3f},ease:"none"}},0);']}


EFFECT_BUILDERS = {
    "stutter-12fps": _fx_stutter, "stepped-ease": _fx_stepped,
    SIGNATURE_EFFECT: _fx_highlighter, "map-draw": _fx_map_draw,
    "chromatic-aberration": _fx_chromatic, "push-in": _fx_push_in,
    "parallax": _fx_parallax,
}


# ---- TRANSITIONS (applied at the render_video ASSEMBLY step) ----------
# Maps a storyboard transition token to an FFmpeg assembly spec used by
# build_assembly_plan(). 'cut'/'match-cut' are hard joins (concat, no xfade); the
# others are real cross-scene transitions (both scenes available at assembly).
TRANSITION_ASSEMBLY = {
    "cut": {"mode": "concat", "xfade": None, "duration": 0.0},
    "match-cut": {"mode": "concat", "xfade": None, "duration": 0.0},
    "dip-to-black": {"mode": "xfade", "xfade": "fadeblack", "duration": 0.4},
    "push": {"mode": "xfade", "xfade": "slideleft", "duration": 0.4},
    "wipe": {"mode": "xfade", "xfade": "wipeleft", "duration": 0.4},
}


# ======================================================================
# Per-scene HTML assembly (pure, deterministic)
# ======================================================================
_BASE_CSS = (
    f"*{{margin:0;padding:0;box-sizing:border-box;}}"
    f"html,body{{width:{CANVAS_W}px;height:{CANVAS_H}px;overflow:hidden;}}"
    f"#scene-root{{position:relative;width:{CANVAS_W}px;height:{CANVAS_H}px;overflow:hidden;}}"
    ".layout{position:absolute;inset:0;display:flex;align-items:center;"
    "justify-content:center;padding:8%;}"
    ".scene-title{font-size:96px;line-height:1.05;font-weight:800;max-width:80%;text-align:center;}"
    ".scene-title.display{font-size:120px;text-transform:uppercase;letter-spacing:-2px;}"
    ".media{width:100%;height:100%;object-fit:cover;display:block;}"
    ".placeholder-panel{display:flex;align-items:center;justify-content:center;"
    "background:repeating-linear-gradient(45deg,#2a2a2a,#2a2a2a 16px,#222 16px,#222 32px);"
    "color:#888;font-size:28px;letter-spacing:2px;text-transform:uppercase;}"
    ".overlay{z-index:50;}"
    ".split-screen{padding:0;}.split-pane{position:absolute;top:0;bottom:0;width:50%;"
    "display:flex;align-items:center;justify-content:center;padding:5%;}"
    ".split-pane.left{left:0;}.split-pane.right{right:0;}"
    ".full-bleed-image .lower-strip,.lower-third .name-strip{position:absolute;left:0;"
    "right:0;bottom:140px;padding:32px 8%;background:linear-gradient(0deg,#000c,#0000);}"
    ".full-bleed-image .scene-title,.lower-third .scene-title{text-align:left;font-size:64px;}"
    ".caption{position:absolute;left:8%;right:8%;bottom:28px;text-align:center;"
    "font-size:40px;font-weight:600;text-shadow:0 2px 8px #000a;opacity:0;}"
    ".caption.clip{opacity:1;}"
    ".cmp{position:absolute;top:0;bottom:0;width:50%;display:flex;align-items:center;"
    "justify-content:center;}.cmp.myth{left:0;background:#1a1a1a;color:#777;}"
    ".cmp.fact{right:0;}"
    # Brand chips (issue #2, Direction A): typographic badge in the brand color; a row
    # of them when several models appear (the matchup). Static — deterministic under
    # frame-seek; the only motion is a build-time GSAP entrance on the paused timeline.
    ".brand-chips{display:flex;flex-wrap:wrap;gap:40px;align-items:center;"
    "justify-content:center;max-width:92%;}"
    ".brand-chip{display:flex;align-items:center;justify-content:center;gap:18px;"
    "padding:28px 56px;border-radius:28px;border:4px solid var(--brand);color:var(--brand);"
    "background:#ffffff0d;font-size:64px;font-weight:800;letter-spacing:-1px;line-height:1;"
    "white-space:nowrap;}"
    ".brand-chip svg{height:84px;width:auto;display:block;}"
    ".brand-media{display:flex;align-items:center;justify-content:center;width:100%;height:100%;}"
    ".layout.has-brand{flex-direction:column;gap:64px;}"
)


def compose_scene_html(ctx: dict) -> str:
    """Assemble ONE scene's deterministic standalone HyperFrames composition.

    `ctx` (built by _scene_ctx): scene_no, comp_id, duration, fps, title, layout,
    transition, effects [{name,params}], textures [{name,params}], signature,
    highlight, captions [{start,duration,text}], assets [resolved descriptors].
    """
    palette = ctx["palette"]
    dyn_css = (f"html,body{{background:{palette.get('bg', '#0d0d0d')};"
               f"color:{palette.get('text', '#f5f5f5')};"
               f"font-family:{palette.get('font', 'Inter')},system-ui,sans-serif;}}")
    css_parts = [_BASE_CSS, dyn_css]
    tl_lines: list[str] = []

    # --- LAYOUT (always present; default centered-statement) ---
    layout = ctx["layout"] if ctx["layout"] in LAYOUT_BUILDERS else "centered-statement"
    lb = LAYOUT_BUILDERS[layout](ctx)
    css_parts.append(lb["css"])
    layout_html = lb["html"]
    tl_lines += lb["tl"]

    # --- BRAND CHIPS (issue #2, Direction A) ---
    # Media-slot layouts already received the chips via _media_html (brand precedence).
    # For text-only layouts, inject a centered focal row so the brand shows there too —
    # this is what finally puts the logos on screen in title-card / list-stack /
    # centered-statement / quote-card / comparison scenes.
    brand_keys = ctx.get("brand_keys") or []
    if brand_keys and layout not in MEDIA_SLOT_LAYOUTS:
        chips = render_brand_chips(brand_keys)
        marker = 'class="layout '
        idx = layout_html.find(marker)
        if idx != -1:
            layout_html = layout_html.replace(marker, 'class="layout has-brand ', 1)
            gt = layout_html.find(">", idx)
            if gt != -1:
                layout_html = layout_html[:gt + 1] + chips + layout_html[gt + 1:]

    # --- TEXTURES (global overlays, absolute children INSIDE the root) ---
    overlay_html: list[str] = []
    for tex in ctx["textures"]:
        tb = TEXTURE_BUILDERS.get(tex["name"])
        if tb:
            frag = tb(tex.get("params", {}))
            css_parts.append(frag["css"])
            overlay_html.append(frag["html"])

    # --- EFFECTS (per-scene motion) ---
    effect_html: list[str] = []
    has_highlighter = any(e["name"] == SIGNATURE_EFFECT for e in ctx["effects"])
    for fx in ctx["effects"]:
        eb = EFFECT_BUILDERS.get(fx["name"])
        if eb:
            frag = eb(ctx)
            css_parts.append(frag["css"])
            if frag["html"]:
                effect_html.append(frag["html"])
            tl_lines += frag["tl"]

    # The highlighter sweep nests behind the title text (the one beat Mason hand-tunes).
    # Insert the sweep span as the first child of the title element so it sits behind
    # the text within the title's box (CSS: .scene-title{position:relative}, z-index:-1).
    if has_highlighter:
        idx = layout_html.find('class="scene-title')
        if idx != -1:
            gt = layout_html.find(">", idx)
            if gt != -1:
                layout_html = (layout_html[:gt + 1] + '<span class="hl-sweep"></span>'
                               + layout_html[gt + 1:])

    # Brand-chip entrance (build-time, deterministic; a gentle staggered fade-in).
    if brand_keys:
        tl_lines.append('tl.from(".brand-chip",{opacity:0,y:24,duration:0.5,'
                        'ease:"power2.out",stagger:0.08},0.1);')

    # Title entrance (core motion, gentle ease; stepped effects own their own motion)
    tl_lines.insert(0, 'tl.from(".scene-title",{opacity:0,y:24,duration:0.6,'
                       'ease:"power2.out"},0);')

    # --- CAPTIONS (native .clip mechanism; LOCAL timing; build-time only) ---
    caption_html: list[str] = []
    for c in ctx["captions"]:
        ls = max(0.0, min(float(c["start"]), ctx["duration"]))
        dur = max(0.1, min(float(c["duration"]), ctx["duration"] - ls))
        caption_html.append(
            f'<div class="caption clip" data-start="{ls:.3f}" data-duration="{dur:.3f}">'
            f'{_esc(c["text"])}</div>')

    # --- ROOT (FIRST body element; everything nests inside) ---
    inner = "\n      ".join([layout_html] + effect_html + overlay_html + caption_html)
    timeline_js = "\n      ".join(tl_lines)
    body = (
        f'<div id="scene-root" data-composition-id="{ctx["comp_id"]}" '
        f'data-width="{CANVAS_W}" data-height="{CANVAS_H}" data-start="0" '
        f'data-duration="{ctx["duration"]:.3f}">\n      {inner}\n'
        f'      <script>\n'
        f'        const tl = gsap.timeline({{ paused: true }});\n'
        f'      {timeline_js}\n'
        f'        window.__timelines = window.__timelines || {{}};\n'
        f'        window.__timelines["{ctx["comp_id"]}"] = tl;\n'
        f'      </script>\n    </div>'
    )
    css = "\n".join(p for p in css_parts if p)
    return (
        "<!doctype html>\n<html lang=\"en\">\n  <head>\n"
        "    <meta charset=\"UTF-8\" />\n"
        f"    <meta name=\"viewport\" content=\"width={CANVAS_W}, height={CANVAS_H}\" />\n"
        f"    <title>Scene {ctx['scene_no']}</title>\n"
        f"    <script src=\"{GSAP_CDN}\"></script>\n"
        f"    <style>\n{css}\n    </style>\n  </head>\n"
        f"  <body>\n    {body}\n  </body>\n</html>\n"
    )


# ======================================================================
# Building scene PROJECTS on disk + the composition orchestration
# ======================================================================
_HYPERFRAMES_JSON = (
    '{\n  "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",\n'
    '  "paths": { "blocks": "compositions", "components": "compositions/components", '
    '"assets": "assets" }\n}\n')


def _scene_ctx(n, script_scene, style_guide, board_scene, segments, scene_assets,
               pdir, scene_dir) -> dict:
    palette = (style_guide or {}).get("palette", {}) or {}
    typ = (style_guide or {}).get("typography", {}) or {}
    font = typ.get("heading") or typ.get("body") or "Inter"
    max_per = (((style_guide or {}).get("motion") or {}).get("max_per_scene")
               or DEFAULT_MAX_PER_SCENE)
    textures = _as_named((style_guide or {}).get("textures")) or \
        [{"name": t, "params": {}} for t in DEFAULT_TEXTURES]
    signature = bool(board_scene.get("signature_beat"))
    transition = board_scene.get("transition") or "cut"
    effects = _as_named(board_scene.get("effects"))
    if signature and not any(e["name"] == SIGNATURE_EFFECT for e in effects):
        effects = [{"name": SIGNATURE_EFFECT, "params": {}}] + effects
    effects = trim_effects(effects, transition, max_per, signature)
    duration = scene_duration(script_scene, segments)

    # localize present assets into the scene project; resolve src
    resolved = []
    for a in scene_assets:
        a = dict(a)
        a["src_rel"] = _copy_asset_local(pdir, scene_dir, a) if a.get("present") else None
        resolved.append(a)

    highlight = palette.get("signature_highlight", SIGNATURE_HIGHLIGHT)
    shots = board_scene.get("shots") or []
    return {
        "scene_no": n, "comp_id": f"scene-{n:02d}", "duration": duration,
        "fps": clamp_fps((style_guide or {}).get("fps", DEFAULT_FPS)),
        "title": script_scene.get("on_screen_text") or script_scene.get("point") or "",
        "layout": board_scene.get("layout") or "centered-statement",
        "transition": transition, "effects": effects, "textures": textures,
        "signature": signature, "palette": {**palette, "font": font},
        "highlight": highlight,
        "captions": scene_captions(segments, n), "assets": resolved,
        "shots": shots, "brand_keys": scene_brand_keys(shots),
    }


def _skip_render() -> bool:
    """Env escape hatch (default OFF): skip the actual MP4 renders for fast offline
    pipeline/CI checks. The HTML build + self-scan still run; the auto-gate still runs
    if the toolchain is available. Real composition leaves this unset."""
    return os.environ.get("MASON_SKIP_RENDER", "").strip().lower() in ("1", "yes", "true")


def compose(pdir, *, render: bool = True, gate: bool = True) -> dict:
    """Build every scene's index.html, self-scan, run the auto-gate, draft-render.

    Returns the composition_manifest dict (without schema_version — atlas stamps it).
    The Artifact summary preserves "auto-gate PASS" so the pipeline's gate check holds.
    Raises ValueError on invalid inputs (never spends a render on bad inputs).
    """
    pdir = pathlib.Path(pdir)
    script = chat_state.load_json(pdir / "script.json", {})
    style_guide = chat_state.load_json(pdir / "style_guide.json", {})
    storyboard = chat_state.load_json(pdir / "storyboard.json", {})
    asset_manifest = chat_state.load_json(pdir / "asset_manifest.json", {})
    transcript = chat_state.load_json(pdir / "audio" / "narration.transcript.json", {})
    segments = transcript.get("segments", [])

    ok, errors = validate_inputs(script, style_guide, storyboard, asset_manifest)
    if not ok:
        raise ValueError("cannot compose — " + "; ".join(errors))

    board_by_no = {s.get("scene_no"): s for s in storyboard.get("scenes", [])}
    scenes_out = []
    skip_render = _skip_render() or not render

    for sc in script.get("scenes", []):
        n = sc.get("scene_no")
        scene_dir = pdir / "scenes" / f"scene-{n:02d}"
        scene_dir.mkdir(parents=True, exist_ok=True)
        board_scene = board_by_no.get(n, {})
        scene_assets = resolve_scene_assets(asset_manifest, n, pdir)
        ctx = _scene_ctx(n, sc, style_guide, board_scene, segments, scene_assets,
                         pdir, scene_dir)
        html = compose_scene_html(ctx)
        (scene_dir / "index.html").write_text(html)
        (scene_dir / "hyperframes.json").write_text(_HYPERFRAMES_JSON)
        _emit_motion_sidecar(scene_dir, ctx)

        self_scan = scan_determinism(html)
        scan_ok = not self_scan
        gate_res = {"lint": None, "validate": None, "inspect": None}
        gate_ok = scan_ok
        render_path = None
        render_status = "skipped"
        contrast_failures = 0

        if gate and scan_ok:
            gate_res = hf_tools.run_gate(scene_dir, motion_strict=_motion_strict(ctx))
            gate_ok = all((gate_res[k] or {}).get("ok") for k in ("lint", "validate", "inspect"))
            contrast_failures = (gate_res.get("validate") or {}).get("contrast_failures", 0)

        if gate_ok and not skip_render:
            rendered = hf_tools.run_render(scene_dir)
            if rendered.get("ok"):
                render_path = rendered.get("output")
                render_status = "rendered"
            else:
                render_status = "failed"

        scenes_out.append({
            "scene_no": n,
            "html_path": str((scene_dir / "index.html").relative_to(pdir)),
            "render_path": (str(pathlib.Path(render_path).relative_to(pdir))
                            if render_path else None),
            "duration_sec": ctx["duration"], "fps": ctx["fps"],
            "layout": ctx["layout"], "transition": ctx["transition"],
            "effects": [e["name"] for e in ctx["effects"]],
            "signature_beat": ctx["signature"],
            "self_scan": {"ok": scan_ok, "violations": self_scan},
            "gate": gate_res,
            "assets": {
                "used": sum(1 for a in ctx["assets"] if a.get("src_rel")),
                "placeholders": [a["asset_id"] for a in ctx["assets"] if a["placeholder"]],
                "integrity_flags": [a["integrity_flag"] for a in ctx["assets"]
                                    if a.get("integrity_flag")],
                "contrast_failures": contrast_failures,
            },
            "render_status": render_status,
        })

    gated_ok = sum(1 for s in scenes_out
                   if s["self_scan"]["ok"] and
                   all((s["gate"][k] or {}).get("ok", False) for k in
                       ("lint", "validate", "inspect")))
    if not gate:
        gated_ok = sum(1 for s in scenes_out if s["self_scan"]["ok"])
    rendered = sum(1 for s in scenes_out if s["render_status"] == "rendered")
    all_ok = bool(scenes_out) and gated_ok == len(scenes_out)
    return {
        "scenes": scenes_out,
        "verdict": "pass" if all_ok else "blocked",
        "summary": {
            "total": len(scenes_out), "gated_ok": gated_ok, "rendered": rendered,
            "auto_gate": "PASS" if all_ok else "FAIL",
            "integrity_flags": sum(len(s["assets"]["integrity_flags"]) for s in scenes_out),
            "contrast_failures": sum(s["assets"]["contrast_failures"] for s in scenes_out),
        },
    }


def _motion_strict(ctx: dict) -> bool:
    """Run inspect --strict (with the motion sidecar) on the signature-beat scene and
    any scene carrying map-draw or the highlighter sweep."""
    names = {e["name"] for e in ctx["effects"]}
    return ctx["signature"] or bool(names & {"map-draw", SIGNATURE_EFFECT})


def _emit_motion_sidecar(scene_dir: pathlib.Path, ctx: dict) -> None:
    """Emit a *.motion.json sidecar asserting the signature motion endpoints so
    `inspect` can machine-verify them. Only for scenes worth asserting."""
    if not _motion_strict(ctx):
        return
    dur = round(ctx["duration"], 3)
    names = {e["name"] for e in ctx["effects"]}
    # Assertion kinds verified against the HyperFrames inspect motion-spec surface:
    # appearsBy {selector, bySec}, staysInFrame {selector}.
    assertions = [{"kind": "staysInFrame", "selector": ".scene-title"}]
    if SIGNATURE_EFFECT in names:
        # the #FFD000 sweep grows from scaleX 0 -> it must be visible by scene end
        assertions.append({"kind": "appearsBy", "selector": ".hl-sweep", "bySec": dur})
    if "map-draw" in names:
        # the drawn route reveals via stroke-dashoffset 1 -> 0 by scene end
        assertions.append({"kind": "appearsBy", "selector": ".map-path", "bySec": dur})
    sidecar = {"version": 1, "duration": dur, "assertions": assertions}
    chat_state.atomic_write_json(scene_dir / "index.motion.json", sidecar)


# ======================================================================
# Final assembly (render_video, POST human-gate): concat scene renders +
# storyboard transitions + narration mux. The PLAN is pure + unit-tested; the
# execution (FFmpeg/HyperFrames) is integration.
# ======================================================================
def build_assembly_plan(manifest: dict, storyboard: dict, audio_manifest: dict) -> dict:
    """Pure: turn the composition manifest + storyboard transitions + audio into a
    deterministic assembly plan (scene render list, per-boundary transition specs,
    narration track). Unknown transition tokens are flagged, never silently dropped."""
    board_by_no = {s.get("scene_no"): s for s in (storyboard or {}).get("scenes", [])}
    scenes = sorted(manifest.get("scenes", []), key=lambda s: s.get("scene_no", 0))
    steps, flags = [], []
    for i, s in enumerate(scenes):
        n = s.get("scene_no")
        steps.append({"scene_no": n, "render": s.get("render_path")})
        if i < len(scenes) - 1:
            trans = (board_by_no.get(n, {}).get("transition") or "cut")
            spec = TRANSITION_ASSEMBLY.get(trans)
            if spec is None:
                flags.append(f"scene {n}->{scenes[i+1].get('scene_no')}: unknown "
                             f"transition {trans!r}")
                spec = TRANSITION_ASSEMBLY["cut"]
            steps.append({"boundary_after": n, "transition": trans, **spec})
    narration = None
    for t in (audio_manifest or {}).get("tracks", []):
        if t.get("role") == "narration":
            narration = t.get("uri")
            break
    return {
        "scene_count": len(scenes),
        "missing_renders": [s.get("scene_no") for s in scenes if not s.get("render_path")],
        "steps": steps, "narration": narration,
        "flags": flags,
    }


def run_render(pdir) -> dict:
    """Final assembly (integration). Renders each scene at standard quality, applies
    transitions at boundaries, muxes narration -> video.mp4. Honors MASON_SKIP_RENDER
    (writes a placeholder + records skipped) for fast offline pipeline checks."""
    pdir = pathlib.Path(pdir)
    manifest = chat_state.load_json(pdir / "composition_manifest.json", {})
    storyboard = chat_state.load_json(pdir / "storyboard.json", {})
    audio_manifest = chat_state.load_json(pdir / "audio" / "audio_manifest.json", {})
    plan = build_assembly_plan(manifest, storyboard, audio_manifest)

    if _skip_render():
        (pdir / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42MASON-SKIP")
        return {"ok": True, "video": "video.mp4", "skipped": True, "plan": plan}
    return hf_tools.assemble_final(pdir, plan)


# ======================================================================
# CLI / chat conveniences (load + compose + save + log) — used by run.py/chat.py
# ======================================================================
def _resolve_pdir(path: str) -> pathlib.Path:
    p = pathlib.Path(path).expanduser()
    if p.is_dir():
        return p
    if p.name == "script.json" or p.suffix == ".json":
        return p.parent
    raise ValueError(f"{path!r} is not a project directory (or a file inside one).")


def plan(pdir) -> dict:
    """Cheap, pure dry-run: validate inputs and summarize the per-scene build plan —
    no HTML written, no gate, no render. Powers the chat preview + the [y/N] gate.
    Raises ValueError on invalid inputs."""
    pdir = pathlib.Path(pdir)
    script = chat_state.load_json(pdir / "script.json", {})
    style_guide = chat_state.load_json(pdir / "style_guide.json", {})
    storyboard = chat_state.load_json(pdir / "storyboard.json", {})
    asset_manifest = chat_state.load_json(pdir / "asset_manifest.json", {})
    transcript = chat_state.load_json(pdir / "audio" / "narration.transcript.json", {})
    segments = transcript.get("segments", [])
    ok, errors = validate_inputs(script, style_guide, storyboard, asset_manifest)
    if not ok:
        raise ValueError("cannot compose — " + "; ".join(errors))

    board_by_no = {s.get("scene_no"): s for s in storyboard.get("scenes", [])}
    max_per = (((style_guide or {}).get("motion") or {}).get("max_per_scene")
               or DEFAULT_MAX_PER_SCENE)
    out = []
    for sc in script.get("scenes", []):
        n = sc.get("scene_no")
        b = board_by_no.get(n, {})
        signature = bool(b.get("signature_beat"))
        transition = b.get("transition") or "cut"
        effects = _as_named(b.get("effects"))
        if signature and not any(e["name"] == SIGNATURE_EFFECT for e in effects):
            effects = [{"name": SIGNATURE_EFFECT, "params": {}}] + effects
        effects = trim_effects(effects, transition, max_per, signature)
        assets = resolve_scene_assets(asset_manifest, n, pdir)
        out.append({
            "scene_no": n, "layout": b.get("layout") or "centered-statement",
            "transition": transition, "effects": [e["name"] for e in effects],
            "duration_sec": scene_duration(sc, segments), "signature_beat": signature,
            "captions": len(scene_captions(segments, n)),
            "placeholders": [a["asset_id"] for a in assets if a["placeholder"]],
            "integrity_flags": [a["integrity_flag"] for a in assets if a.get("integrity_flag")],
        })
    sig = next((s["scene_no"] for s in out if s["signature_beat"]), None)
    return {"total": len(out), "fps": clamp_fps((style_guide or {}).get("fps", DEFAULT_FPS)),
            "signature_scene": sig, "scenes": out}


def run_compose(path: str, *, render: bool = True) -> tuple[dict, pathlib.Path]:
    """Compose a project's scenes, write composition_manifest.json, log the run."""
    pdir = _resolve_pdir(path)
    manifest = compose(pdir, render=render)
    stamped = {"schema_version": SCHEMA_VERSION, **manifest}
    out = pdir / "composition_manifest.json"
    chat_state.atomic_write_json(out, stamped)
    _log_run("compose", manifest)
    return stamped, out


def _log_run(kind: str, manifest: dict) -> None:
    mem = load_memory()
    summ = manifest.get("summary", {})
    mem.setdefault("runs", []).append({
        "kind": kind, "scenes": summ.get("total"), "auto_gate": summ.get("auto_gate"),
        "rendered": summ.get("rendered"),
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    save_memory(mem)
