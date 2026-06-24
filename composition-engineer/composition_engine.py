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
# BUNDLED OFL FONTS (HyperFrames forbids render-time font fetch) — local @font-face.
# The .ttf files live beside this module in fonts/; at scene-build time they are
# copied into each scene project's assets/fonts/ (the same deterministic localizing
# pattern as _copy_asset_local) and referenced by a LOCAL relative url() — never http.
# All SIL OFL 1.1 (see fonts/LICENSES.md). Fraunces/Inter are variable -> the @font-face
# carries `font-weight:100 900`. Iris's typography.display.family resolves to "Fraunces"
# and .body.family to "Inter"; an UNBUNDLED family name falls back to a bundled face
# (Noto Serif Display for a display/serif role, Noto Sans for body) so a render is never
# fontless. Map: family name -> {file, variable, fallback_for}.
FONTS_DIR = HERE / "fonts"
BUNDLED_FONTS = {
    "Fraunces":           {"file": "Fraunces.ttf",                 "variable": True},
    "Inter":              {"file": "Inter.ttf",                    "variable": True},
    "Noto Serif Display": {"file": "NotoSerifDisplay-Regular.ttf", "variable": False},
    "Noto Sans":          {"file": "NotoSans-Regular.ttf",         "variable": False},
}
DEFAULT_DISPLAY_FAMILY = "Fraunces"        # the bundled editorial display face
DEFAULT_BODY_FAMILY = "Inter"              # the bundled neutral body face
FALLBACK_DISPLAY_FAMILY = "Noto Serif Display"   # guaranteed-present OFL display fallback
FALLBACK_BODY_FAMILY = "Noto Sans"               # guaranteed-present OFL body fallback

# ----------------------------------------------------------------------
# THE FINITE VOCABULARIES — must mirror the Art Director's vocabulary exactly.
# Mason implements a partial for EVERY token; an unknown token is rejected, not
# dropped. (Verified equal to art-director/art_engine.py at build time by a test.)
# ----------------------------------------------------------------------
LAYOUTS = (
    "centered-statement", "split-screen", "full-bleed-image", "lower-third",
    "data-chart", "quote-card", "map-focus", "list-stack", "comparison-2up",
    "title-card", "big-number", "timeline",
)
TRANSITIONS = ("cut", "dip-to-black", "push", "wipe", "match-cut")
EFFECTS = (
    "stutter-12fps", "stepped-ease", SIGNATURE_EFFECT, "map-draw",
    "chromatic-aberration", "push-in", "parallax", "count-up",
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
# Real brand logos (Lobe Icons, @lobehub/icons-static-svg, MIT) inlined as self-
# contained SVG marks — no runtime dependency, no render-time fetch. OpenAI's mark is
# fill="currentColor" (tinted to its brand color via .brand-chip-logo); the others
# carry their own brand color / gradient. width/height stripped so CSS sizes them.
_LOGO_OPENAI = '<svg fill="currentColor" fill-rule="evenodd" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>OpenAI</title><path d="M9.205 8.658v-2.26c0-.19.072-.333.238-.428l4.543-2.616c.619-.357 1.356-.523 2.117-.523 2.854 0 4.662 2.212 4.662 4.566 0 .167 0 .357-.024.547l-4.71-2.759a.797.797 0 00-.856 0l-5.97 3.473zm10.609 8.8V12.06c0-.333-.143-.57-.429-.737l-5.97-3.473 1.95-1.118a.433.433 0 01.476 0l4.543 2.617c1.309.76 2.189 2.378 2.189 3.948 0 1.808-1.07 3.473-2.76 4.163zM7.802 12.703l-1.95-1.142c-.167-.095-.239-.238-.239-.428V5.899c0-2.545 1.95-4.472 4.591-4.472 1 0 1.927.333 2.712.928L8.23 5.067c-.285.166-.428.404-.428.737v6.898zM12 15.128l-2.795-1.57v-3.33L12 8.658l2.795 1.57v3.33L12 15.128zm1.796 7.23c-1 0-1.927-.332-2.712-.927l4.686-2.712c.285-.166.428-.404.428-.737v-6.898l1.974 1.142c.167.095.238.238.238.428v5.233c0 2.545-1.974 4.472-4.614 4.472zm-5.637-5.303l-4.544-2.617c-1.308-.761-2.188-2.378-2.188-3.948A4.482 4.482 0 014.21 6.327v5.423c0 .333.143.571.428.738l5.947 3.449-1.95 1.118a.432.432 0 01-.476 0zm-.262 3.9c-2.688 0-4.662-2.021-4.662-4.519 0-.19.024-.38.047-.57l4.686 2.71c.286.167.571.167.856 0l5.97-3.448v2.26c0 .19-.07.333-.237.428l-4.543 2.616c-.619.357-1.356.523-2.117.523zm5.899 2.83a5.947 5.947 0 005.827-4.756C22.287 18.339 24 15.84 24 13.296c0-1.665-.713-3.282-1.998-4.448.119-.5.19-.999.19-1.498 0-3.401-2.759-5.947-5.946-5.947-.642 0-1.26.095-1.88.31A5.962 5.962 0 0010.205 0a5.947 5.947 0 00-5.827 4.757C1.713 5.447 0 7.945 0 10.49c0 1.666.713 3.283 1.998 4.448-.119.5-.19 1-.19 1.499 0 3.401 2.759 5.946 5.946 5.946.642 0 1.26-.095 1.88-.309a5.96 5.96 0 004.162 1.713z"></path></svg>'
_LOGO_CLAUDE = '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>Claude</title><path d="M4.709 15.955l4.72-2.647.08-.23-.08-.128H9.2l-.79-.048-2.698-.073-2.339-.097-2.266-.122-.571-.121L0 11.784l.055-.352.48-.321.686.06 1.52.103 2.278.158 1.652.097 2.449.255h.389l.055-.157-.134-.098-.103-.097-2.358-1.596-2.552-1.688-1.336-.972-.724-.491-.364-.462-.158-1.008.656-.722.881.06.225.061.893.686 1.908 1.476 2.491 1.833.365.304.145-.103.019-.073-.164-.274-1.355-2.446-1.446-2.49-.644-1.032-.17-.619a2.97 2.97 0 01-.104-.729L6.283.134 6.696 0l.996.134.42.364.62 1.414 1.002 2.229 1.555 3.03.456.898.243.832.091.255h.158V9.01l.128-1.706.237-2.095.23-2.695.08-.76.376-.91.747-.492.584.28.48.685-.067.444-.286 1.851-.559 2.903-.364 1.942h.212l.243-.242.985-1.306 1.652-2.064.73-.82.85-.904.547-.431h1.033l.76 1.129-.34 1.166-1.064 1.347-.881 1.142-1.264 1.7-.79 1.36.073.11.188-.02 2.856-.606 1.543-.28 1.841-.315.833.388.091.395-.328.807-1.969.486-2.309.462-3.439.813-.042.03.049.061 1.549.146.662.036h1.622l3.02.225.79.522.474.638-.079.485-1.215.62-1.64-.389-3.829-.91-1.312-.329h-.182v.11l1.093 1.068 2.006 1.81 2.509 2.33.127.578-.322.455-.34-.049-2.205-1.657-.851-.747-1.926-1.62h-.128v.17l.444.649 2.345 3.521.122 1.08-.17.353-.608.213-.668-.122-1.374-1.925-1.415-2.167-1.143-1.943-.14.08-.674 7.254-.316.37-.729.28-.607-.461-.322-.747.322-1.476.389-1.924.315-1.53.286-1.9.17-.632-.012-.042-.14.018-1.434 1.967-2.18 2.945-1.726 1.845-.414.164-.717-.37.067-.662.401-.589 2.388-3.036 1.44-1.882.93-1.086-.006-.158h-.055L4.132 18.56l-1.13.146-.487-.456.061-.746.231-.243 1.908-1.312-.006.006z" fill="#D97757" fill-rule="nonzero"></path></svg>'
_LOGO_GEMINI = '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>Gemini</title><path d="M20.616 10.835a14.147 14.147 0 01-4.45-3.001 14.111 14.111 0 01-3.678-6.452.503.503 0 00-.975 0 14.134 14.134 0 01-3.679 6.452 14.155 14.155 0 01-4.45 3.001c-.65.28-1.318.505-2.002.678a.502.502 0 000 .975c.684.172 1.35.397 2.002.677a14.147 14.147 0 014.45 3.001 14.112 14.112 0 013.679 6.453.502.502 0 00.975 0c.172-.685.397-1.351.677-2.003a14.145 14.145 0 013.001-4.45 14.113 14.113 0 016.453-3.678.503.503 0 000-.975 13.245 13.245 0 01-2.003-.678z" fill="#3186FF"></path><path d="M20.616 10.835a14.147 14.147 0 01-4.45-3.001 14.111 14.111 0 01-3.678-6.452.503.503 0 00-.975 0 14.134 14.134 0 01-3.679 6.452 14.155 14.155 0 01-4.45 3.001c-.65.28-1.318.505-2.002.678a.502.502 0 000 .975c.684.172 1.35.397 2.002.677a14.147 14.147 0 014.45 3.001 14.112 14.112 0 013.679 6.453.502.502 0 00.975 0c.172-.685.397-1.351.677-2.003a14.145 14.145 0 013.001-4.45 14.113 14.113 0 016.453-3.678.503.503 0 000-.975 13.245 13.245 0 01-2.003-.678z" fill="url(#lobe-icons-gemini-0-_R_0_)"></path><path d="M20.616 10.835a14.147 14.147 0 01-4.45-3.001 14.111 14.111 0 01-3.678-6.452.503.503 0 00-.975 0 14.134 14.134 0 01-3.679 6.452 14.155 14.155 0 01-4.45 3.001c-.65.28-1.318.505-2.002.678a.502.502 0 000 .975c.684.172 1.35.397 2.002.677a14.147 14.147 0 014.45 3.001 14.112 14.112 0 013.679 6.453.502.502 0 00.975 0c.172-.685.397-1.351.677-2.003a14.145 14.145 0 013.001-4.45 14.113 14.113 0 016.453-3.678.503.503 0 000-.975 13.245 13.245 0 01-2.003-.678z" fill="url(#lobe-icons-gemini-1-_R_0_)"></path><path d="M20.616 10.835a14.147 14.147 0 01-4.45-3.001 14.111 14.111 0 01-3.678-6.452.503.503 0 00-.975 0 14.134 14.134 0 01-3.679 6.452 14.155 14.155 0 01-4.45 3.001c-.65.28-1.318.505-2.002.678a.502.502 0 000 .975c.684.172 1.35.397 2.002.677a14.147 14.147 0 014.45 3.001 14.112 14.112 0 013.679 6.453.502.502 0 00.975 0c.172-.685.397-1.351.677-2.003a14.145 14.145 0 013.001-4.45 14.113 14.113 0 016.453-3.678.503.503 0 000-.975 13.245 13.245 0 01-2.003-.678z" fill="url(#lobe-icons-gemini-2-_R_0_)"></path><defs><linearGradient gradientUnits="userSpaceOnUse" id="lobe-icons-gemini-0-_R_0_" x1="7" x2="11" y1="15.5" y2="12"><stop stop-color="#08B962"></stop><stop offset="1" stop-color="#08B962" stop-opacity="0"></stop></linearGradient><linearGradient gradientUnits="userSpaceOnUse" id="lobe-icons-gemini-1-_R_0_" x1="8" x2="11.5" y1="5.5" y2="11"><stop stop-color="#F94543"></stop><stop offset="1" stop-color="#F94543" stop-opacity="0"></stop></linearGradient><linearGradient gradientUnits="userSpaceOnUse" id="lobe-icons-gemini-2-_R_0_" x1="3.5" x2="17.5" y1="13.5" y2="12"><stop stop-color="#FABC12"></stop><stop offset=".46" stop-color="#FABC12" stop-opacity="0"></stop></linearGradient></defs></svg>'
_LOGO_DEEPSEEK = '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>DeepSeek</title><path d="M23.748 4.482c-.254-.124-.364.113-.512.234-.051.039-.094.09-.137.136-.372.397-.806.657-1.373.626-.829-.046-1.537.214-2.163.848-.133-.782-.575-1.248-1.247-1.548-.352-.156-.708-.311-.955-.65-.172-.241-.219-.51-.305-.774-.055-.16-.11-.323-.293-.35-.2-.031-.278.136-.356.276-.313.572-.434 1.202-.422 1.84.027 1.436.633 2.58 1.838 3.393.137.093.172.187.129.323-.082.28-.18.552-.266.833-.055.179-.137.217-.329.14a5.526 5.526 0 01-1.736-1.18c-.857-.828-1.631-1.742-2.597-2.458a11.365 11.365 0 00-.689-.471c-.985-.957.13-1.743.388-1.836.27-.098.093-.432-.779-.428-.872.004-1.67.295-2.687.684a3.055 3.055 0 01-.465.137 9.597 9.597 0 00-2.883-.102c-1.885.21-3.39 1.102-4.497 2.623C.082 8.606-.231 10.684.152 12.85c.403 2.284 1.569 4.175 3.36 5.653 1.858 1.533 3.997 2.284 6.438 2.14 1.482-.085 3.133-.284 4.994-1.86.47.234.962.327 1.78.397.63.059 1.236-.03 1.705-.128.735-.156.684-.837.419-.961-2.155-1.004-1.682-.595-2.113-.926 1.096-1.296 2.746-2.642 3.392-7.003.05-.347.007-.565 0-.845-.004-.17.035-.237.23-.256a4.173 4.173 0 001.545-.475c1.396-.763 1.96-2.015 2.093-3.517.02-.23-.004-.467-.247-.588zM11.581 18c-2.089-1.642-3.102-2.183-3.52-2.16-.392.024-.321.471-.235.763.09.288.207.486.371.739.114.167.192.416-.113.603-.673.416-1.842-.14-1.897-.167-1.361-.802-2.5-1.86-3.301-3.307-.774-1.393-1.224-2.887-1.298-4.482-.02-.386.093-.522.477-.592a4.696 4.696 0 011.529-.039c2.132.312 3.946 1.265 5.468 2.774.868.86 1.525 1.887 2.202 2.891.72 1.066 1.494 2.082 2.48 2.914.348.292.625.514.891.677-.802.09-2.14.11-3.054-.614zm1-6.44a.306.306 0 01.415-.287.302.302 0 01.2.288.306.306 0 01-.31.307.303.303 0 01-.304-.308zm3.11 1.596c-.2.081-.399.151-.59.16a1.245 1.245 0 01-.798-.254c-.274-.23-.47-.358-.552-.758a1.73 1.73 0 01.016-.588c.07-.327-.008-.537-.239-.727-.187-.156-.426-.199-.688-.199a.559.559 0 01-.254-.078c-.11-.054-.2-.19-.114-.358.028-.054.16-.186.192-.21.356-.202.767-.136 1.146.016.352.144.618.408 1.001.782.391.451.462.576.685.914.176.265.336.537.445.848.067.195-.019.354-.25.452z" fill="#4D6BFE"></path></svg>'

BRAND_CHIPS = {
    "openai":    {"aliases": ("gpt-4o", "gpt4o", "gpt-4", "gpt", "chatgpt", "openai"),
                  "display": "GPT-4o", "color": "#10A37F", "logo_svg": _LOGO_OPENAI},
    "anthropic": {"aliases": ("claude", "anthropic"),
                  "display": "Claude", "color": "#D97757", "logo_svg": _LOGO_CLAUDE},
    "google":    {"aliases": ("gemini", "google gemini"),
                  "display": "Gemini", "color": "#8E6FF7", "logo_svg": _LOGO_GEMINI},
    "deepseek":  {"aliases": ("deepseek", "deep seek"),
                  "display": "DeepSeek", "color": "#4D6BFE", "logo_svg": _LOGO_DEEPSEEK},
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


def _resolve_bundled_family(name, role: str) -> str:
    """Resolve a requested font family to a BUNDLED family name.

    `role` is "display" or "body". If `name` names a bundled family we keep it;
    otherwise we fall back to the role's guaranteed-present OFL face so the render is
    never fontless (HyperFrames cannot fetch a font at render time). Pure + deterministic.
    """
    if isinstance(name, str) and name.strip() in BUNDLED_FONTS:
        return name.strip()
    return FALLBACK_DISPLAY_FAMILY if role == "display" else FALLBACK_BODY_FAMILY


def _copy_font_local(scene_dir: pathlib.Path, family: str) -> str | None:
    """Copy a bundled font's .ttf into the scene project's assets/fonts/ and return the
    in-project relative path (e.g. 'assets/fonts/Fraunces.ttf'). Mirrors _copy_asset_local
    so each scene project is a self-contained, network-free render. None if not bundled."""
    spec = BUNDLED_FONTS.get(family)
    if not spec:
        return None
    src = FONTS_DIR / spec["file"]
    dest_dir = scene_dir / "assets" / "fonts"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    try:
        shutil.copyfile(src, dest)
    except OSError:
        return None
    return f"assets/fonts/{src.name}"


def _font_face_css(family: str, rel_path: str) -> str:
    """A deterministic @font-face block pointing at a LOCAL bundled .ttf (no http).
    Variable faces declare the full 100 900 weight range."""
    spec = BUNDLED_FONTS.get(family, {})
    weight = "font-weight:100 900;" if spec.get("variable") else "font-weight:400 700;"
    return (f"@font-face{{font-family:'{family}';src:url('{rel_path}') format('truetype');"
            f"{weight}font-style:normal;font-display:block;}}")


# ======================================================================
# THE TECHNIQUE LIBRARY — one partial per token, all four axes.
# Builders return fragments: {"css": str, "html": str, "tl": [js lines]}.
# An entry exists for EVERY vocabulary token (asserted by a test).
# ======================================================================
def _esc(text) -> str:
    return _html.escape(str(text or ""), quote=True)


def _font_family(value, fallback: str = "Inter") -> str:
    """Resolve a typography slot to a bare font-family STRING.

    Iris emits typography slots as nested dicts (e.g. {"family":"GT Sectra",
    "weight":700}); older/simple inputs may pass a bare string. Either way we must
    inject a NAME into CSS, never a Python dict repr (which would leak '{' into
    font-family and break the cascade). Empty/unknown shapes fall back.
    """
    if isinstance(value, dict):
        fam = value.get("family")
        if isinstance(fam, str) and fam.strip():
            return fam.strip()
        return fallback
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


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


# A generic "the major models / four AI logos lined up" cue that NAMES no specific
# model (issue #2, H1). When a brand/logo shot reads like a roster lineup but no alias
# is matched, Mason falls back to the full BRAND_CHIPS matchup row rather than shipping
# an un-sourceable placeholder. Needs a "logos/models" word AND a lineup/count cue.
_COUNT_WORD = (r"two|three|four|five|several|all|the\s+(?:major|leading|top|big|main)|"
               r"\d+")
_ROSTER_CUE = re.compile(
    rf"\b(?:logos?|models?|ais?|chatbots?|assistants?)\b", re.IGNORECASE)
_LINEUP_CUE = re.compile(
    rf"(?:{_COUNT_WORD})|lined\s*up|line[\s-]?up|line-?up|lineup|in\s+a\s+row|"
    r"\brow\b|side[\s-]?by[\s-]?side|matchup|line\s*up", re.IGNORECASE)


def _is_generic_roster_shot(shot: dict) -> bool:
    """True when a shot cues a generic brand/model LINEUP that names no specific model.

    Fires on kind:'brand'/'logo' OR content like "four AI logos lined up" / "the major
    models in a row" — a roster word plus a count/lineup cue. Used only as a FALLBACK
    when detect_brands finds no named alias (issue #2, H1)."""
    if not isinstance(shot, dict):
        return False
    text = f"{shot.get('content', '')} {shot.get('asset_ref', '')}"
    kind = str(shot.get("kind", "")).strip().lower()
    if kind in ("brand", "logo", "logos"):
        return True
    return bool(_ROSTER_CUE.search(text) and _LINEUP_CUE.search(text))


def scene_brand_keys(shots) -> list[str]:
    """Brand keys named across a scene's storyboard shots (content + asset_ref), ordered.

    Detection is by model NAME, independent of shot.kind, so the existing storyboards
    (kind:'graphic'/'panel'/…) render chips too, alongside Iris's newer kind:'brand'.

    H1 fallback: a brand/logo shot (or a generic "four AI logos lined up" lineup cue)
    that names NO specific model falls back to the FULL roster as a matchup row, instead
    of shipping an un-sourceable placeholder.
    """
    text = " ".join(f"{s.get('content', '')} {s.get('asset_ref', '')}"
                    for s in (shots or []) if isinstance(s, dict))
    named = detect_brands(text)
    if named:
        return named
    if any(_is_generic_roster_shot(s) for s in (shots or [])):
        return list(BRAND_CHIPS.keys())
    return []


# A shot that frames a model as backgrounded/secondary (vs. a foregrounded "winner").
_DIM_CUE = re.compile(
    r"\b(dim|dimmed|background|backgrounded|faded?|muted|gray(ed)?|grey(ed)?|"
    r"de-?emphasi\w*|secondary|behind)\b", re.IGNORECASE)


def _dim_brands_in_shot(text: str, keys: list[str]) -> dict[str, bool]:
    """Per-brand dim decision within ONE shot (M4): scope the dim cue to the brand it
    actually describes, instead of dimming every brand in a multi-brand shot.

    - 0 brands: nothing to dim.
    - 1 brand: the whole-shot dim cue applies to it (legacy behavior).
    - 2+ brands: only dim a brand whose alias sits inside a de-emphasis clause (the cue
      and the alias in the SAME comma/'while'/'vs'-delimited clause). The foregrounded
      winner — named in a clause with no dim cue — stays bright.
    """
    if not keys:
        return {}
    if not _DIM_CUE.search(text):
        return {k: False for k in keys}
    if len(keys) == 1:
        return {keys[0]: True}
    # Split into clauses; a brand is dim only if its clause carries a dim cue.
    clauses = re.split(r"\s*(?:,|;|—|–|\bwhile\b|\bwhereas\b|\bvs\.?\b|\bversus\b|"
                       r"\bbut\b|\bas\b)\s*", text, flags=re.IGNORECASE)
    result = {k: False for k in keys}
    for clause in clauses:
        clause_dim = bool(_DIM_CUE.search(clause))
        for key in detect_brands(clause):
            if key in result and clause_dim:
                result[key] = True
    return result


def scene_brand_specs(shots) -> list[dict]:
    """Per-scene chip specs [{key, dim}], ordered, deduped (first mention wins).

    A brand named in a shot whose content reads as de-emphasized (e.g. "dimmed into the
    background") is marked dim:True, so the scene's named winners stand out from the rest
    — the intentional arrangement scene 4 ("Coding -> Claude/DeepSeek") needs. The dim
    cue is scoped PER-BRAND within a shot (M4): in a multi-brand shot the foregrounded
    winner is not dimmed just because a de-emphasized peer shares the frame.

    H1 fallback: when no model is named but a brand/logo lineup is cued, the full roster
    is returned (all bright) so the matchup still renders chips, not a placeholder.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for s in (shots or []):
        if not isinstance(s, dict):
            continue
        text = f"{s.get('content', '')} {s.get('asset_ref', '')}"
        keys = detect_brands(text)
        dims = _dim_brands_in_shot(text, keys)
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            out.append({"key": key, "dim": dims.get(key, False)})
    if not out and any(_is_generic_roster_shot(s) for s in (shots or [])):
        return [{"key": k, "dim": False} for k in BRAND_CHIPS.keys()]
    return out


def render_brand_chips(items, *, cls: str = "brand-chips") -> str:
    """Render one styled chip per brand. `items` are brand keys (str) or specs
    ({key, dim}). Each chip shows the inline SVG logo as the primary mark plus the model
    name as a label; an entry with no logo_svg falls back to the typographic name only.
    Several items -> a 'matchup' row; dim items are de-emphasized."""
    chips = []
    for it in items or []:
        key = it.get("key") if isinstance(it, dict) else it
        dim = bool(it.get("dim")) if isinstance(it, dict) else False
        b = BRAND_CHIPS.get(key)
        if not b:
            continue
        logo = b.get("logo_svg") or ""
        # Strip the decorative SVG <title>: HyperFrames' inspect gate reads it as text
        # occluded beneath the logo paths (text_occluded error) and blocks the render.
        # The visible name label below carries the name, so the title is redundant.
        logo = re.sub(r"<title>.*?</title>", "", logo, flags=re.S)
        mark = f'<span class="brand-chip-logo">{logo}</span>' if logo else ""
        name = f'<span class="brand-chip-name">{_esc(b["display"])}</span>'
        klass = "brand-chip dim" if dim else "brand-chip"
        chips.append(f'<div class="{klass}" style="--brand:{b["color"]}">{mark}{name}</div>')
    return f'<div class="{cls}">' + "".join(chips) + "</div>"


# A data point in a comparison/bar scene: a numeric magnitude with an optional label.
# Matches "Coffee ~95", "95 mg coffee", "coffee: 95", "29 mg green tea", etc. We pull
# (number, nearest word-label) pairs deterministically from the scene's text so a
# data-chart scene never renders as bare text even when the generated viz has no file.
_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")
# a short label is 1-3 words of letters (e.g. "green tea", "black tea", "coffee")
_LABEL_WORD = r"[A-Za-z][A-Za-z'-]*"
# "Coffee ~95", "Coffee: 95", "Coffee 95 mg" — label BEFORE the number
_LABEL_THEN_NUM = re.compile(
    rf"((?:{_LABEL_WORD}\s+){{0,2}}{_LABEL_WORD})\s*[:~≈]?\s*(\d+(?:\.\d+)?)")


def parse_chart_data(*texts: str, max_points: int = 6) -> list[dict]:
    """Extract ordered {label, value} pairs from comparison/data text (deterministic).

    Used by the native bar chart so a `data-chart` scene renders a real visual even
    when its generated data-viz asset has no file. Prefers "<label> <number>" pairs
    (e.g. "Coffee ~95"); ranges ("47-48") take the first number. Pure + ordered by
    appearance; deduped by label (first wins).
    """
    out: list[dict] = []
    seen: set[str] = set()
    for text in texts:
        s = str(text or "")
        for m in _LABEL_THEN_NUM.finditer(s):
            label = re.sub(r"\s+", " ", m.group(1)).strip()
            # trim leading filler words so "stepping up to 95" -> drop, not a real label
            _STOP = {"avg", "oz", "mg", "approx", "about", "around", "to", "and", "up",
                     "of", "at", "than", "with", "the", "a", "an", "stepping", "rising",
                     "reaching", "near", "over", "under"}
            words = label.split()
            while words and words[-1].lower() in _STOP:
                words.pop()
            label = " ".join(words)
            low = label.lower()
            if not label or low in _STOP:
                continue
            try:
                val = float(m.group(2))
            except ValueError:
                continue
            key = low
            if key in seen:
                continue
            seen.add(key)
            out.append({"label": label, "value": val})
            if len(out) >= max_points:
                return out
    return out


# A single dominant statistic for big-number: a number, an optional unit token right
# after it (%, mg, x, M, B, K, $ prefix), and a short label = the surrounding words.
_BIG_UNIT = r"%|×|x|mg|kg|oz|ms|fps|bn|m|k|b|hrs?|min|gb|mb|°c|°f"
_BIG_NUM_RE = re.compile(
    rf"(\$?)\s*(\d{{1,3}}(?:,\d{{3}})+|\d+(?:\.\d+)?)\s*({_BIG_UNIT})?\b", re.IGNORECASE)


def parse_hero_stat(*texts: str) -> dict | None:
    """Extract ONE dominant {value, unit, label} from a scene's text (deterministic).

    Picks the largest magnitude number (the 'hero' stat), keeping a trailing unit (%, mg,
    x, M…) or a leading $ and the short surrounding phrase as the label. Returns None when
    no number is present. Pure + deterministic."""
    best = None
    for text in texts:
        s = str(text or "")
        for m in _BIG_NUM_RE.finditer(s):
            raw = m.group(2).replace(",", "")
            try:
                val = float(raw)
            except ValueError:
                continue
            unit = (m.group(3) or "").strip()
            if m.group(1) == "$":
                unit = unit or "$"
            # label = the words around the number, minus the number/unit themselves
            label = re.sub(_BIG_NUM_RE, " ", s)
            label = re.sub(r"\s+", " ", label).strip(" .,:–-—·|")
            if best is None or val > best["value"]:
                best = {"value": val, "unit": unit, "label": label[:48]}
    return best


# A timeline entry: a year/date token + its short label. "1969 Moon landing",
# "2007: iPhone", "Step 1 — research". Deterministic, ordered by appearance.
_YEAR_RE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
_STEP_RE = re.compile(r"\b(?:step|phase|stage)\s*(\d+)\b", re.IGNORECASE)


def parse_timeline_data(*texts: str, max_points: int = 6) -> list[dict]:
    """Extract ordered {date, label} timeline entries from chronological/process text.

    Prefers explicit years (1969, 2007…); falls back to 'Step N'/'Phase N' markers. The
    label is the short phrase following the date token. Pure, ordered, deduped by date."""
    out: list[dict] = []
    seen: set[str] = set()
    for text in texts:
        s = str(text or "")
        marks = [(m.start(), m.group(1), "year") for m in _YEAR_RE.finditer(s)]
        if not marks:
            marks = [(m.start(), f"Step {m.group(1)}", "step") for m in _STEP_RE.finditer(s)]
        marks.sort()
        for i, (pos, date, _kind) in enumerate(marks):
            if date in seen:
                continue
            seen.add(date)
            end = marks[i + 1][0] if i + 1 < len(marks) else len(s)
            tail = s[pos + len(date):end]
            label = re.sub(r"\s+", " ", tail).strip(" .,:–-—·|()")
            out.append({"date": date, "label": label[:36]})
            if len(out) >= max_points:
                return out
    return out


def render_bar_chart(data: list[dict], *, cls: str = "media") -> str:
    """A deterministic, build-time native bar chart (pure inline SVG — no JS, no
    randomness, frame-seek safe). Bars scale to the max value; each carries its label
    and value. Returns '' when there's nothing chartable."""
    pts = [d for d in (data or []) if isinstance(d.get("value"), (int, float))]
    if not pts:
        return ""
    vw, vh = 1000.0, 600.0
    pad_b, pad_t = 90.0, 40.0
    n = len(pts)
    gap = 28.0
    bw = (vw - gap * (n + 1)) / n
    vmax = max(d["value"] for d in pts) or 1.0
    plot_h = vh - pad_b - pad_t
    bars = []
    for i, d in enumerate(pts):
        h = (float(d["value"]) / vmax) * plot_h
        x = gap + i * (bw + gap)
        y = pad_t + (plot_h - h)
        # accent the tallest bar (the magnitude "winner") with the signature color
        fill = SIGNATURE_HIGHLIGHT if d["value"] == vmax else "#F5F5F5"
        bars.append(
            f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
            f'height="{h:.1f}" rx="6" fill="{fill}" />'
            f'<text class="bar-val" x="{x + bw / 2:.1f}" y="{y - 12:.1f}" '
            f'text-anchor="middle">{_esc(_fmt_num(d["value"]))}</text>'
            f'<text class="bar-lbl" x="{x + bw / 2:.1f}" y="{vh - 28:.1f}" '
            f'text-anchor="middle">{_esc(d["label"])}</text>')
    return (f'<svg class="{cls} bar-chart" viewBox="0 0 {vw:.0f} {vh:.0f}" '
            f'preserveAspectRatio="xMidYMid meet" role="img">'
            f'<line class="axis" x1="0" y1="{vh - pad_b:.1f}" x2="{vw:.0f}" '
            f'y2="{vh - pad_b:.1f}" />' + "".join(bars) + "</svg>")


def _fmt_num(v) -> str:
    f = float(v)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _media_html(ctx: dict, cls: str = "media") -> str:
    """Brand chips (if this is a brand scene) take the media slot; else an <img> for a
    present local asset, else a deterministic placeholder panel.

    Brand chips take PRECEDENCE over any sourced asset: for a brand scene the sourced
    image is irrelevant by construction (the logos are un-sourceable), so the chip wins.
    """
    if ctx.get("brand_keys"):
        items = ctx.get("brand_specs") or ctx.get("brand_keys")
        return f'<div class="{cls} brand-media">{render_brand_chips(items)}</div>'
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
    # Title sits OVER a photo: wrap the text in a solid dark scrim plate
    # (.bleed-scrim) so it reaches WCAG contrast regardless of the image's
    # luminance — the old fade-to-transparent gradient was opaque only at the
    # very bottom, so the top of the title failed over light imagery.
    return {"css": "", "html":
            f'<div class="layout full-bleed-image">{_media_html(ctx, "media bleed")}'
            f'<div class="lower-strip"><h1 class="scene-title">'
            f'<span class="bleed-scrim">{_esc(ctx["title"])}</span>'
            f'</h1></div></div>', "tl": []}


def _layout_lower_third(ctx):
    return {"css": "", "html":
            f'<div class="layout lower-third">{_media_html(ctx, "media bleed")}'
            f'<div class="name-strip"><h2 class="scene-title">'
            f'<span class="bleed-scrim">{_esc(ctx["title"])}</span>'
            f'</h2></div></div>', "tl": []}


def _layout_data_chart(ctx):
    # A data-chart scene must render an actual VISUAL, never bare centered text (C5).
    # Precedence: a PRESENT data-viz/image asset -> a deterministic native bar chart
    # built from the scene's data -> the standard media/placeholder fallback. Brand
    # scenes still get chips (handled inside _media_html).
    if not ctx.get("brand_keys"):
        asset = next((a for a in ctx["assets"]
                      if a["type"] in ("data-viz", "image") and a.get("src_rel")), None)
        if asset:
            inner = f'<img class="media" src="{_esc(asset["src_rel"])}" alt="" />'
        else:
            chart = render_bar_chart(ctx.get("chart_data") or [])
            inner = chart if chart else _media_html(ctx)
    else:
        inner = _media_html(ctx)
    # Title ABOVE the chart: a centered column drops a below-chart title into the
    # bottom caption band, where the burned-in caption-scrim occludes it (inspect
    # 'text_occluded'). Top-zoned title + chart below keeps the title clear of the
    # caption and reads as the conventional "chart title on top".
    return {"css": "", "html":
            f'<div class="layout data-chart">'
            f'<h2 class="scene-title">{_esc(ctx["title"])}</h2>'
            f'<div class="chart-frame">{inner}</div></div>', "tl": []}


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


def _layout_big_number(ctx):
    # A single dominant statistic at HERO scale: a short label, the giant number (with an
    # optional unit), centered. Pure CSS/HTML, no JS. The number carries the #FFD000
    # signature tint only on the signature beat. The count-up effect (if present) tweens
    # this same .big-number-value 0->target on the paused timeline.
    stat = ctx.get("hero_stat") or {}
    value = stat.get("value")
    if value is None:
        value = _esc(ctx["title"])              # no parsed stat -> the line is the hero
        num_html = f'<div class="big-number-value">{value}</div>'
    else:
        num_html = (f'<div class="big-number-value" data-target="{_fmt_num(value)}">'
                    f'{_esc(_fmt_num(value))}</div>')
    label = stat.get("label") or ctx["title"]
    unit = stat.get("unit")
    unit_html = f'<span class="big-number-unit">{_esc(unit)}</span>' if unit else ""
    klass = "big-number sig" if ctx.get("signature") else "big-number"
    return {"css": "", "html":
            f'<div class="layout {klass}">'
            f'<div class="big-number-label">{_esc(label)}</div>'
            f'<div class="big-number-stat">{num_html}{unit_html}</div></div>', "tl": []}


def _tl_label_tspans(label: str, x: float, *, max_line: int = 16,
                     max_lines: int = 2, line_h: float = 34.0) -> str:
    """Wrap a node label into <=max_lines centered <tspan> lines (SVG text doesn't wrap
    natively) and truncate the rest with an ellipsis, so a long process label can never
    overflow the frame edge (inspect 'text_box_overflow'). Deterministic."""
    words = str(label).split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = (cur + " " + w).strip()
        if cur and len(cand) > max_line:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines:
                break
        else:
            cur = cand
    if cur and len(lines) < max_lines:
        lines.append(cur)
    used = sum(len(ln.split()) for ln in lines)
    if used < len(words) and lines:                       # ran out of room -> ellipsis
        lines[-1] = (lines[-1][:max_line - 1].rstrip() + "…")
    lines = [ln[:max_line + 2] for ln in lines] or [""]
    return "".join(
        f'<tspan x="{x:.1f}" dy="{0 if i == 0 else line_h:.1f}">{_esc(ln)}</tspan>'
        for i, ln in enumerate(lines))


def _layout_timeline(ctx):
    # A horizontal SVG baseline with N evenly-spaced nodes; each node carries a date/label
    # parsed from on_screen_text/shot content. Deterministic inline SVG, no animation.
    entries = ctx.get("timeline_data") or []
    if not entries:
        # nothing parsed -> a single node so the layout is still a real timeline visual
        entries = [{"date": "", "label": str(ctx["title"] or "now")}]
    entries = entries[:5]                                  # cap so nodes/labels stay roomy
    vw, vh = 1600.0, 380.0
    y = vh / 2.0
    n = len(entries)
    x0, x1 = 190.0, vw - 190.0                             # inset edge nodes so labels fit
    step = (x1 - x0) / (n - 1) if n > 1 else 0.0
    nodes = []
    for i, e in enumerate(entries):
        x = x0 + i * step
        date = _esc(e.get("date") or "")
        nodes.append(
            f'<circle class="tl-node" cx="{x:.1f}" cy="{y:.1f}" r="14" />'
            f'<text class="tl-date" x="{x:.1f}" y="{y - 44:.1f}" text-anchor="middle">{date}</text>'
            f'<text class="tl-label" y="{y + 60:.1f}" text-anchor="middle">'
            f'{_tl_label_tspans(e.get("label") or "", x)}</text>')
    svg = (f'<svg class="timeline-svg" viewBox="0 0 {vw:.0f} {vh:.0f}" '
           f'preserveAspectRatio="xMidYMid meet" role="img">'
           f'<line class="tl-base" x1="{x0:.1f}" y1="{y:.1f}" x2="{x1:.1f}" y2="{y:.1f}" />'
           + "".join(nodes) + "</svg>")
    return {"css": "", "html":
            f'<div class="layout timeline"><h2 class="scene-title tl-title">'
            f'{_esc(ctx["title"])}</h2>{svg}</div>', "tl": []}


LAYOUT_BUILDERS = {
    "centered-statement": _layout_centered, "split-screen": _layout_split,
    "full-bleed-image": _layout_full_bleed, "lower-third": _layout_lower_third,
    "data-chart": _layout_data_chart, "quote-card": _layout_quote,
    "map-focus": _layout_map_focus, "list-stack": _layout_list_stack,
    "comparison-2up": _layout_comparison, "title-card": _layout_title_card,
    "big-number": _layout_big_number, "timeline": _layout_timeline,
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


def _fx_count_up(ctx):
    # The hero number tweens 0 -> target on the PAUSED master timeline. The target is read
    # at build time from the .big-number-value's data-target; an onUpdate writes the
    # rounded value into textContent. HyperFrames seeks to fixed times, so the value at
    # each seeked frame is fully determined -> frame-deterministic. No Math.random/Date.now,
    # no late gsap.set: the initial 0 is written by the tween's start, the proxy object is
    # a plain build-time literal. Self-scan-clean.
    dur = max(0.3, min(1.2, ctx["duration"] * 0.5))
    return {"css": "", "html": "",
            "tl": ['(function(){var el=document.querySelector(".big-number-value");'
                   'if(!el||!el.dataset.target)return;'
                   'var target=parseFloat(el.dataset.target);if(isNaN(target))return;'
                   'var o={n:0};'
                   f'tl.to(o,{{n:target,duration:{dur:.3f},ease:"power1.out",'
                   'onUpdate:function(){el.textContent=String(Math.round(o.n));}},0);'
                   '})();']}


EFFECT_BUILDERS = {
    "stutter-12fps": _fx_stutter, "stepped-ease": _fx_stepped,
    SIGNATURE_EFFECT: _fx_highlighter, "map-draw": _fx_map_draw,
    "chromatic-aberration": _fx_chromatic, "push-in": _fx_push_in,
    "parallax": _fx_parallax, "count-up": _fx_count_up,
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
    "right:0;bottom:140px;padding:32px 8%;}"
    ".full-bleed-image .scene-title,.lower-third .scene-title{text-align:left;font-size:64px;}"
    # Text-over-image legibility: a SOLID dark scrim plate behind the title (a
    # banded Vox-style lower-third), opaque enough (0.82) to guarantee >=4.5:1
    # contrast over ANY underlying photo — replaces the old bottom-only gradient
    # that left the top of the title over the raw image. White text + shadow.
    ".bleed-scrim{display:inline-block;max-width:100%;padding:14px 28px;"
    "border-radius:12px;color:#ffffff;background:rgba(0,0,0,0.82);"
    "box-shadow:0 6px 28px rgba(0,0,0,0.45);"
    "text-shadow:0 2px 10px #000d,0 0 2px #000d;}"
    # Captions (C4): burned-in narration must stay legible over dark imagery — a
    # readable scrim panel behind the text + a text-shadow + adequate size/weight.
    ".caption{position:absolute;left:6%;right:6%;bottom:40px;text-align:center;"
    "font-size:42px;line-height:1.25;font-weight:700;color:#ffffff;"
    "text-shadow:0 2px 10px #000d,0 0 2px #000d;opacity:0;}"
    ".caption .caption-scrim{display:inline-block;max-width:100%;padding:18px 34px;"
    "border-radius:14px;background:rgba(0,0,0,0.72);"
    "box-shadow:0 6px 28px rgba(0,0,0,0.45);}"
    ".caption.clip{opacity:1;}"
    ".cmp{position:absolute;top:0;bottom:0;width:50%;display:flex;align-items:center;"
    "justify-content:center;}.cmp.myth{left:0;background:#1a1a1a;color:#777;}"
    ".cmp.fact{right:0;}"
    # Native bar chart (C5): a build-time inline-SVG chart so a data-chart scene is
    # never bare text. Deterministic under frame-seek (static SVG, no JS/animation).
    ".data-chart{flex-direction:column;gap:40px;}"
    ".data-chart .chart-frame{width:100%;flex:1;display:flex;align-items:center;"
    "justify-content:center;min-height:0;}"
    ".bar-chart{width:100%;height:100%;max-height:74%;}"
    ".bar-chart .axis{stroke:#ffffff44;stroke-width:2;}"
    ".bar-chart .bar-val{fill:#fff;font-size:34px;font-weight:800;}"
    ".bar-chart .bar-lbl{fill:#cfcfcf;font-size:30px;font-weight:600;}"
    # Brand chips (issue #2, Direction A): a clean logo card — the real inline SVG mark
    # over the model name, framed by the brand color. A row of them when several models
    # appear (the matchup); dim cards de-emphasize a scene's non-winners. Static —
    # deterministic under frame-seek; the only motion is a build-time GSAP entrance.
    ".brand-chips{display:flex;flex-wrap:wrap;gap:36px;align-items:stretch;"
    "justify-content:center;max-width:94%;}"
    # Brand chips sit in a full-bleed media slot OVER imagery. The chip card is a
    # SOLID dark plate with a brand-colored border + brand-tinted logo + a
    # near-white label. Dark card + light label is the one pattern the WCAG
    # contrast checker reliably resolves over arbitrary frames (it composites a
    # dark label against the page bg even when the card is light, so dark-on-light
    # chips fail 1.04:1 over a dark frame); white-on-dark guarantees >=4.5:1.
    ".brand-chip{display:flex;flex-direction:column;align-items:center;justify-content:center;"
    "gap:22px;padding:40px 52px;border-radius:28px;border:3px solid var(--brand);"
    "background:#141414;box-shadow:0 10px 34px #00000040;min-width:240px;}"
    ".brand-chip.dim{opacity:.34;filter:grayscale(.4);transform:scale(.86);}"
    ".brand-chip-logo{display:flex;align-items:center;justify-content:center;"
    "color:var(--brand);}"
    ".brand-chip-logo svg{height:132px;width:132px;display:block;}"
    ".brand-chip-name{font-size:44px;font-weight:700;color:#ffffff;letter-spacing:-.5px;"
    "line-height:1;white-space:nowrap;}"
    ".brand-media{display:flex;align-items:center;justify-content:center;width:100%;"
    "height:100%;padding:6%;}"
    # has-brand stacks two content blocks (a brand-chip row + the layout's own
    # content) in one scene. A flex column with centering let them OVERLAP when the
    # combined height exceeded the frame (inspect 'text_occluded' — the opaque chip
    # drawn over the hero number). A GRID with content-sized auto-rows guarantees each
    # block occupies its OWN row and can never overlap, at ANY content length; the
    # whole stack is clipped to the frame and centered.
    ".layout.has-brand{display:grid;grid-auto-flow:row;grid-auto-rows:max-content;"
    "align-content:center;justify-items:center;gap:40px;max-height:88%;max-width:92%;"
    "overflow:hidden;}"
    ".layout.has-brand>*{min-height:0;max-width:100%;}"
    ".title-card.has-brand .scene-title{font-size:92px;}"
    # comparison-2up's .cmp panels are position:absolute opaque half-plates that fall
    # OUTSIDE the has-brand grid, so the grid's "own row" guarantee can't reach them and
    # the opaque .cmp.myth painted OVER the injected chips (inspect 'text_occluded'). Under
    # has-brand, reflow the panels as in-flow grid rows (static, transparent) and drop the
    # literal 'myth' stub so the chips + title each keep their own row, never overlapping.
    ".layout.has-brand .cmp{position:static;width:auto;height:auto;background:transparent;}"
    ".layout.has-brand .cmp.myth{display:none;}"
    # big-number (Job 2): one dominant stat at HERO scale, a short label, optional unit.
    ".big-number{flex-direction:column;gap:24px;text-align:center;max-width:94%;"
    "overflow:hidden;}"
    ".big-number-label{font-size:44px;font-weight:600;color:#e8e8e8;letter-spacing:1px;"
    "text-transform:uppercase;max-width:80%;line-height:1.1;}"
    # Value is sized by the viewport (deterministic: the render frame is a fixed
    # 1920x1080) and CAPPED so multi-digit numbers can't overflow the frame and
    # overlap the label (inspect 'content_overlap'/'text_occluded'). nowrap keeps the
    # digits on one line; the parent clips.
    ".big-number-stat{display:flex;align-items:baseline;justify-content:center;gap:18px;"
    "max-width:94%;white-space:nowrap;}"
    ".big-number-value{font-size:min(300px,15vw);line-height:1;font-weight:800;"
    "letter-spacing:-4px;font-variant-numeric:tabular-nums;}"
    ".big-number.sig .big-number-value{color:#FFD000;}"
    ".big-number-unit{font-size:min(110px,6vw);font-weight:700;color:#cfcfcf;}"
    # When a big-number scene ALSO carries a brand chip (has-brand), the hero number
    # shares the frame with the chip row — shrink it so the two stack without
    # overlapping (inspect 'content_overlap'/'text_occluded').
    ".has-brand .big-number-value{font-size:min(190px,11vw);}"
    ".has-brand .big-number-label{font-size:34px;}"
    ".has-brand .big-number-unit{font-size:min(70px,4vw);}"
    # timeline (Job 2): a horizontal SVG baseline with evenly-spaced labelled nodes.
    ".timeline{flex-direction:column;gap:48px;}"
    ".timeline .tl-title{font-size:64px;}"
    ".timeline-svg{width:100%;height:auto;max-height:60%;}"
    ".timeline-svg .tl-base{stroke:#ffffff55;stroke-width:4;}"
    ".timeline-svg .tl-node{fill:#FFD000;}"
    ".timeline-svg .tl-date{fill:#fff;font-size:34px;font-weight:800;}"
    ".timeline-svg .tl-label{fill:#cfcfcf;font-size:26px;font-weight:600;}"
)


def compose_scene_html(ctx: dict) -> str:
    """Assemble ONE scene's deterministic standalone HyperFrames composition.

    `ctx` (built by _scene_ctx): scene_no, comp_id, duration, fps, title, layout,
    transition, effects [{name,params}], textures [{name,params}], signature,
    highlight, captions [{start,duration,text}], assets [resolved descriptors].
    """
    palette = ctx["palette"]
    # Fonts are resolved to bare strings upstream (_scene_ctx/_font_family); guard here
    # too so a raw dict shape can never leak into font-family (C1). Snap each to a
    # BUNDLED OFL family so the @font-face below always points at a local file we ship.
    heading_font = _resolve_bundled_family(
        _font_family(palette.get("font"), DEFAULT_DISPLAY_FAMILY), "display")
    body_font = _resolve_bundled_family(
        _font_family(palette.get("body_font"), DEFAULT_BODY_FAMILY), "body")
    # @font-face blocks point at the LOCALLY-bundled .ttf (assets/fonts/...) — never http.
    # _scene_ctx records the real localized paths; when ctx omits them (unit-test ctx),
    # synthesize the deterministic default local path so the HTML still bundles locally.
    faces = ctx.get("font_faces")
    if not faces:
        faces = [(fam, f"assets/fonts/{BUNDLED_FONTS[fam]['file']}")
                 for fam in dict.fromkeys([heading_font, body_font])
                 if fam in BUNDLED_FONTS]
    font_face_css = "".join(_font_face_css(fam, rel) for fam, rel in faces)
    dyn_css = (font_face_css
               + f"html,body{{background:{palette.get('bg', '#0d0d0d')};"
               f"color:{palette.get('text', '#f5f5f5')};"
               f"font-family:'{body_font}',system-ui,sans-serif;}}"
               f".scene-title{{font-family:'{heading_font}',system-ui,sans-serif;}}")
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
        chips = render_brand_chips(ctx.get("brand_specs") or brand_keys)
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
    # Suppressed on layouts whose title is itself a text lower-third OVER the image
    # (full-bleed-image, lower-third): a burned narration caption would sit on top of
    # the title plate (inspect 'text_occluded' error) and clutter the frame. On those
    # layouts the on-screen title carries the words; captions stay on the text-led layouts.
    _CAPTION_SUPPRESS = {"full-bleed-image", "lower-third"}
    caption_html: list[str] = []
    for c in ([] if ctx.get("layout") in _CAPTION_SUPPRESS else ctx["captions"]):
        ls = max(0.0, min(float(c["start"]), ctx["duration"]))
        dur = max(0.1, min(float(c["duration"]), ctx["duration"] - ls))
        caption_html.append(
            f'<div class="caption clip" data-start="{ls:.3f}" data-duration="{dur:.3f}">'
            f'<span class="caption-scrim">{_esc(c["text"])}</span></div>')

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
    # Iris emits typography as nested dicts: display/body/caption = {family, weight}
    # (no "heading" key). Resolve each slot to a bare STRING — never inject the dict.
    # Accept "heading" as a legacy alias for "display". Then snap each to a BUNDLED OFL
    # family (Fraunces/Inter by default; an unbundled name falls back to a guaranteed
    # Noto face) so the render is never fontless and never fetches a font at render time.
    heading_font = _resolve_bundled_family(
        _font_family(typ.get("display") or typ.get("heading"), DEFAULT_DISPLAY_FAMILY),
        "display")
    body_font = _resolve_bundled_family(
        _font_family(typ.get("body"), DEFAULT_BODY_FAMILY), "body")
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

    # Bundle the OFL fonts LOCALLY into this scene project (assets/fonts/) and record
    # (family, rel_path) for the @font-face blocks — no render-time font fetch.
    font_faces = []
    for fam in dict.fromkeys([heading_font, body_font]):  # dedupe, keep order
        rel = _copy_font_local(scene_dir, fam)
        if rel:
            font_faces.append((fam, rel))

    highlight = palette.get("signature_highlight", SIGNATURE_HIGHLIGHT)
    shots = board_scene.get("shots") or []
    # Data for a native bar chart (C5): parse label/value pairs from the scene's
    # on-screen line (the curated, clean source) first; fall to shot prose only if it
    # yields nothing. Keeps a data-chart scene a real visual even when the generated
    # data-viz asset has no file.
    ost = str(script_scene.get("on_screen_text") or "")
    shot_text = " ".join(str(s.get("content", "")) for s in shots if isinstance(s, dict))
    chart_data = parse_chart_data(ost)
    if not chart_data:
        chart_data = parse_chart_data(shot_text)
    # big-number: the single dominant stat (on-screen line first, then shot prose).
    hero_stat = parse_hero_stat(ost) or parse_hero_stat(shot_text)
    # timeline: chronological/process entries parsed from the same sources.
    timeline_data = parse_timeline_data(ost) or parse_timeline_data(shot_text)
    return {
        "scene_no": n, "comp_id": f"scene-{n:02d}", "duration": duration,
        "fps": clamp_fps((style_guide or {}).get("fps", DEFAULT_FPS)),
        "title": script_scene.get("on_screen_text") or script_scene.get("point") or "",
        "layout": board_scene.get("layout") or "centered-statement",
        "transition": transition, "effects": effects, "textures": textures,
        "signature": signature,
        "palette": {**palette, "font": heading_font, "body_font": body_font},
        "highlight": highlight,
        "captions": scene_captions(segments, n), "assets": resolved,
        "font_faces": font_faces,
        "shots": shots, "brand_keys": scene_brand_keys(shots),
        "brand_specs": scene_brand_specs(shots),
        "chart_data": chart_data,
        "hero_stat": hero_stat, "timeline_data": timeline_data,
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
            # C2 (calibrated): the DETERMINISTIC auto-gate guarantees STRUCTURE — self-scan,
            # lint errors, console errors, layout overflow (inspect). Contrast is an
            # aesthetic/legibility QUALITY signal, and LLM-chosen palettes routinely miss
            # strict 4.5:1 on a label or two — a zero-tolerance hard-block would stop almost
            # every video. The original bug was that contrast was SILENTLY SWALLOWED (gate
            # PASSED while reporting failures); the fix is to RECORD it on the scene and
            # SURFACE the total in the manifest summary so the human render gate can judge —
            # not to ignore it, and not to let aesthetics block the deterministic plane.

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

    # Contrast is SURFACED on the scene + summary (above) but does NOT gate the
    # deterministic plane — per the C2 calibration note above, a zero-tolerance
    # contrast hard-block would stop almost every LLM-palette video. Structure
    # (self-scan, lint, validate console-errors, inspect) still blocks; the human
    # final-render gate judges the surfaced contrast count against the draft.
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


# Layouts whose primary text element is NOT `.scene-title`. The motion sidecar (and the
# highlighter sweep, injected behind a `.scene-title`) assume one exists; these carry the
# beat on a different hero element instead — asserting `.scene-title`/`.hl-sweep` on them
# makes `inspect --strict` fail with motion_selector_missing and blocks the whole video.
_HERO_SELECTOR = {"big-number": ".big-number-value", "timeline": ".tl-title"}


def _hero_selector(ctx: dict) -> str:
    """The scene's primary on-screen text element — `.scene-title` for most layouts,
    the layout's own hero element for the ones that have no `.scene-title`."""
    return _HERO_SELECTOR.get(ctx.get("layout"), ".scene-title")


def _emit_motion_sidecar(scene_dir: pathlib.Path, ctx: dict) -> None:
    """Emit a *.motion.json sidecar asserting the signature motion endpoints so
    `inspect` can machine-verify them. Only for scenes worth asserting."""
    if not _motion_strict(ctx):
        return
    dur = round(ctx["duration"], 3)
    names = {e["name"] for e in ctx["effects"]}
    # Assertion kinds verified against the HyperFrames inspect motion-spec surface:
    # appearsBy {selector, bySec}, staysInFrame {selector}. Assert ONLY selectors that
    # actually render: the hero text for staysInFrame, and the sweep only where it was
    # injected (a layout with a `.scene-title`; big-number/timeline carry the signature
    # via their gold hero, not a text sweep).
    assertions = [{"kind": "staysInFrame", "selector": _hero_selector(ctx)}]
    if SIGNATURE_EFFECT in names and ctx.get("layout") not in _HERO_SELECTOR:
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
