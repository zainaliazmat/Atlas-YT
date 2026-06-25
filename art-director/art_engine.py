"""Iris's engine: script.json -> a restrained style_guide + a scene-by-scene storyboard.

Iris SPECIFIES the look; she never implements it. This engine emits two plain dicts —
a `style_guide` (the global look) and a `storyboard` (one planned scene per script
scene) — in the frozen contract shapes (minus the schema envelope). The Composition
Engineer (#6) builds the HTML/CSS/GSAP from these specs; Iris writes no markup.

THE SPLIT (mirrors the siblings): the BRAIN makes the *taste* calls — the palette
hues, the type system, which layout each scene wants, which technique each scene
earns, which single scene carries the signature beat. PURE CODE then enforces every
hard invariant and shapes the two contract dicts, so the invariants are unit-testable
with the brain mocked:

  1. palette always carries signature_highlight == "#FFD000"
  2. the base palette is bounded (no rainbow): accents capped
  3. EXACTLY ONE scene has signature_beat: true
  4. every scene respects the motion budget: (non-cut transition + effects) <= max_per_scene
  5. the `highlighter-FFD000` EFFECT appears on EXACTLY the signature-beat scene — the
     one animated flourish Iris won't cut (distinct from the #FFD000 accent COLOR,
     which may recur statically)
  6. the budget trim never strips the mandatory highlighter off the signature beat
  7. every shot carries a non-null asset_ref (Iris references; she does NOT resolve
     URIs/licenses — that's the Asset Sourcer #5)
  8. storyboard scene count == script scene count
  9. effects/textures/layout/transition are drawn ONLY from the allowed vocabulary
  10. fps is set and in range

Three axes are kept strictly separate so the Composition Engineer never has to guess:
  - LAYOUTS  = composition (where things sit)
  - TEXTURES = the always-on, global hand-made overlay layer (style_guide.textures)
  - EFFECTS  = per-scene, VARYING techniques (storyboard scene.effects)
A token never spans two axes (e.g. `map-focus` is a layout; `map-draw` is an effect).

Decoupling boundary: this engine emits plain dicts and NEVER imports atlas. Atlas
stamps `schema_version` and validates against the frozen contracts at the boundary.
`design_style(script)` and `build_storyboard(script, style_guide)` are the pure seams
the adapter uses; `run_*` are the CLI/chat conveniences that load, save, and log.
"""
from __future__ import annotations

import json
import pathlib
import re
import time

from dotenv import load_dotenv

import chat_state  # atomic_write_json / load_json — corruption-safe file helpers
import llm

load_dotenv()

HERE = pathlib.Path(__file__).parent
# Identity lives in soul/SOUL.md (the persona bundle); the engine reads ONLY the
# SOUL (who Iris is) — never STYLE.md or examples/, which are chat-voice and would
# make the structured spec chatty. SKILL.md stays the engine method.
SOUL = (HERE / "soul" / "SOUL.md").read_text()
SKILL = (HERE / "SKILL.md").read_text()
# CRAFT is the distilled HyperFrames creative-craft digest, fed ONLY to the creative
# treatment (prompt-expansion) call — the knowledge that turns a thin brief into intentional
# direction. It is NOT used by the structured style/storyboard calls (those stay terse).
CRAFT = (HERE / "craft" / "CRAFT.md").read_text()
MEMORY = HERE / "memory.json"
DESIGNS_DIR = HERE / "designs"

# The schema_version Iris's REAL output carries. Atlas is the authority (it stamps
# via contracts.version_for at the boundary); this local copy is used only by the
# standalone CLI so a `run.py` save is independently contract-shaped. Keep in sync
# with atlas/contracts CONTRACT_VERSIONS — both say "1.1" for these two contracts.
SCHEMA_VERSION = "1.1"

# ----------------------------------------------------------------------
# THE HOUSE SIGNATURE — the one flourish Iris will not cut
# ----------------------------------------------------------------------
SIGNATURE_HIGHLIGHT = "#FFD000"          # the accent COLOR (may recur statically)
SIGNATURE_EFFECT = "highlighter-FFD000"  # the animated BEAT (exactly one scene)

# ----------------------------------------------------------------------
# THE FINITE VOCABULARIES — three separate axes, no token spans two
# (structure is fixed by the schema; these VALUES are grounded in the Vox look and
#  may be refined by the Composition Engineer's Phase-0 render check WITHOUT a bump.)
# ----------------------------------------------------------------------
LAYOUTS = (
    "centered-statement",   # one line, dead center — the default restraint
    "split-screen",         # two-up comparison / before-after
    "full-bleed-image",     # one image edge to edge, text as lower-third
    "lower-third",          # talking-head / b-roll with a name strip
    "data-chart",           # a single chart owns the frame
    "quote-card",           # a pulled quote, attributed
    "map-focus",            # a map composition (the COMPOSITION; map-draw is the effect)
    "list-stack",           # a short stacked list, one item revealed at a time
    "comparison-2up",       # myth vs. fact, then vs. now
    "title-card",           # the cold-open / chapter card
    "big-number",           # a single dominant statistic, hero scale (the Vox big number)
    "timeline",             # a horizontal SVG baseline of chronological / process nodes
    "diagram",              # a conceptual illustration (boxes/arrows/glyphs) from a DiagramPlan
)
TRANSITIONS = (
    "cut",            # the default — zero motion
    "dip-to-black",
    "push",
    "wipe",
    "match-cut",
)
EFFECTS = (
    "stutter-12fps",        # the Vox stepped/stuttered motion feel
    "stepped-ease",         # stepped easing instead of smooth tweens
    SIGNATURE_EFFECT,       # the animated #FFD000 highlighter sweep (signature only)
    "map-draw",             # an animated drawn route/line (the EFFECT; map-focus is the layout)
    "chromatic-aberration", # a restrained RGB-split accent
    "push-in",              # a slow scale-in on a still
    "parallax",             # layered depth on a still
    "count-up",             # the hero number tweens 0->target on the paused timeline
    "breathe",              # ambient sine scale pulse on the title (the "breathe" phase)
    "bars-grow",            # the data-chart bars rise + stagger in
    "drift",                # slow Ken-Burns pan-zoom on a still
    "word-reveal",          # kinetic typography: the title reveals one word at a time
    "pop-in",               # overshoot (back.out) scale entrance on the title — a lively pop
    "underline-grow",       # an accent keyline draws in under the title (editorial design object)
)
# data-chart sub-kinds Mason can render natively (chosen ONLY on a data-chart scene).
# Kept in lock-step with composition_engine.CHART_KINDS (cross-engine parity test).
CHART_KINDS = ("bar", "line", "pie")
# Signature WebGL transitions Mason can render at the boundary INTO the signature beat
# (a deliberate, rare production-value moment). Chosen ONLY for the signature scene; a
# missing/unknown value falls back to Mason's taste default. Kept in lock-step with
# shader_transition.SHADER_TRANSITIONS (cross-engine parity test).
SHADER_TRANSITIONS = ("whip-pan", "sdf-iris", "glitch", "domain-warp")
TEXTURES = (
    "paper",
    "grain",
    "halftone",
    "vignette",
    "scanlines",
)

# ----------------------------------------------------------------------
# Bounds (enforced in code; documented in SKILL)
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# BRAND auto-tagging (issue #2, Direction A) — model/brand shots are rendered as
# HTML/SVG brand-chips by the Composition Engineer (the logos are trademarked and
# un-sourceable from the CC0/PD/CC allowlist). A shot whose content/asset_ref names a
# registry model is retagged kind:'brand' so Magpie SKIPS it and Mason renders a chip.
# The canonical registry (display name, color, optional inline SVG) lives in the
# Composition Engineer (composition-engineer/composition_engine.py BRAND_CHIPS); a
# cross-engine test guards that these aliases stay a subset of what Mason can render.
# ----------------------------------------------------------------------
BRAND_ALIASES = frozenset({
    "gpt-4o", "gpt4o", "gpt-4", "gpt", "chatgpt", "openai",   # -> OpenAI / GPT-4o
    "claude", "anthropic",                                    # -> Anthropic / Claude
    "gemini", "google gemini",                                # -> Google / Gemini
    "deepseek", "deep seek",                                  # -> DeepSeek
})
# Pure-typography kinds are never retagged (a title that merely mentions a model is text,
# not a logo). Mirrors the Asset Sourcer's _TYPOGRAPHY_KINDS.
_TEXT_KINDS = {"title", "text", "quote", "headline", "caption", "label",
               "lower-third", "subtitle", "kicker"}


def _names_a_brand(text: str) -> bool:
    """True if `text` mentions any registry model as a delimited unit (not inside a word)."""
    low = (text or "").lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", low)
               for a in BRAND_ALIASES)


DEFAULT_FPS = 30
FPS_MIN, FPS_MAX = 12, 60
DEFAULT_MAX_PER_SCENE = 2          # Iris's restrained motion budget
MAX_PER_SCENE_CEIL = 4             # she will not allow a fireworks show
MAX_ACCENTS = 3                    # base palette is bounded — no rainbow


# ======================================================================
# Memory — a log of past design runs (provider-agnostic, on our disk)
# ======================================================================
def load_memory():
    return chat_state.load_json(MEMORY, {"runs": []})


def save_memory(mem):
    chat_state.atomic_write_json(MEMORY, mem)


# ======================================================================
# Input validation — don't spend an API call on something we can't art-direct
# ======================================================================
def validate_script(script) -> tuple[bool, str]:
    """Return (ok, reason). A script is usable only if it carries scenes to design."""
    if not isinstance(script, dict):
        return False, "That's not a script — I need the script JSON object."
    scenes = script.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return False, ("This script has no scenes — there's nothing to lay out. Send "
                       "it back to the writer before I design.")
    for s in scenes:
        if not isinstance(s, dict):
            return False, "A scene in this script isn't an object — the script is malformed."
    return True, ""


# ======================================================================
# Robust JSON parsing from an LLM reply (models add prose / fences)
# ======================================================================
def _strip_json(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    openers = [i for i in (text.find("["), text.find("{")) if i != -1]
    if not openers:
        return text
    start = min(openers)
    open_ch = text[start]
    close_ch = "]" if open_ch == "[" else "}"
    depth = 0
    in_str = escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def _chat_json(system: str, user: str, chat_fn=llm.chat):
    """Call the brain and parse a JSON value, retrying once with a blunt reminder."""
    reply = ""
    for attempt in range(2):
        prompt = user if attempt == 0 else (
            user + "\n\nREMINDER: Output ONLY raw JSON — no greeting, no explanation, "
            "no markdown fences. Your entire reply must start with '{'.")
        reply = chat_fn(system, prompt)
        try:
            return json.loads(_strip_json(reply))
        except Exception:
            continue
    raise ValueError(
        "Model did not return valid JSON after a retry. First 200 chars:\n"
        + reply.strip()[:200])


# ======================================================================
# Small pure helpers (clamps + vocabulary normalization) — unit-tested
# ======================================================================
def _is_hex(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"#[0-9A-Fa-f]{3,8}", value.strip()))


def clamp_fps(value) -> int:
    """An int fps clamped to [FPS_MIN, FPS_MAX]; DEFAULT_FPS when unusable."""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return DEFAULT_FPS
    return max(FPS_MIN, min(FPS_MAX, v))


def clamp_max_per_scene(value) -> int:
    """A motion budget clamped to [1, MAX_PER_SCENE_CEIL]; default when unusable."""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_PER_SCENE
    return max(1, min(MAX_PER_SCENE_CEIL, v))


def _normalize_named(raw, allowed: tuple[str, ...]) -> list[dict]:
    """Normalize a list of bare strings / {name,params} into vocab-filtered {name,params}.

    Drops anything whose name isn't in `allowed` (vocabulary membership, in code).
    De-dupes by name, preserving first-seen order. Always returns {name, params}.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for item in (raw or []):
        if isinstance(item, str):
            name, params = item.strip(), {}
        elif isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            params = item.get("params") if isinstance(item.get("params"), dict) else {}
        else:
            continue
        if name not in allowed or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "params": params})
    return out


def normalize_textures(raw) -> list[dict]:
    """Global texture set, filtered to the TEXTURES vocabulary."""
    return _normalize_named(raw, TEXTURES)


def normalize_effects(raw) -> list[dict]:
    """Per-scene effects, filtered to the EFFECTS vocabulary."""
    return _normalize_named(raw, EFFECTS)


# ======================================================================
# Invariant enforcers (pure, deterministic, unit-tested)
# ======================================================================
def enforce_palette(palette) -> dict:
    """Guarantee the signature highlight + a bounded base palette (no rainbow)."""
    p = dict(palette) if isinstance(palette, dict) else {}
    # The one non-negotiable: the house accent color.
    p["signature_highlight"] = SIGNATURE_HIGHLIGHT
    # Sensible restrained defaults so the spec is always complete.
    if not _is_hex(p.get("primary")):
        p["primary"] = "#111111"
    if not _is_hex(p.get("bg")):
        p["bg"] = "#FFFFFF"
    if not _is_hex(p.get("text")):
        p["text"] = "#111111"
    # Bound the accents: hex only, de-duped, capped — and never count the signature
    # color against the cap (it's reserved, not an accent).
    accents, seen = [], set()
    for c in (p.get("accents") or []):
        if _is_hex(c) and c not in seen and c.upper() != SIGNATURE_HIGHLIGHT.upper():
            seen.add(c)
            accents.append(c)
    p["accents"] = accents[:MAX_ACCENTS]
    return p


def choose_signature_scene(scenes: list[dict]) -> int:
    """Pick EXACTLY one scene index to carry the signature beat — deterministically.

    Preference order: the (first) scene the brain flagged signature_beat -> the
    (first) scene the brain already gave the highlighter effect -> the middle scene.
    Empty scenes -> -1 (caller guarantees non-empty by construction).
    """
    if not scenes:
        return -1
    for i, s in enumerate(scenes):
        if bool(s.get("signature_beat")):
            return i
    for i, s in enumerate(scenes):
        names = [e.get("name") if isinstance(e, dict) else e for e in (s.get("effects") or [])]
        if SIGNATURE_EFFECT in names:
            return i
    return len(scenes) // 2


def apply_motion_budget(transition: str, effects: list[dict], max_per_scene: int,
                        mandatory: set[str]) -> tuple[str, list[dict]]:
    """Trim a scene to its motion budget, NEVER dropping a mandatory effect.

    Budget = (non-cut transition counts 1) + len(effects) <= max_per_scene. Mandatory
    effects (e.g. the signature highlighter on the signature beat) are kept first; the
    transition is kept only if there's room after the mandatory effects; remaining
    room is filled with the other effects in order. If a non-cut transition + the
    mandatory effect already fill the budget, the rest is cut — restraint even on the
    flourish.
    """
    mandatory_fx = [e for e in effects if e["name"] in mandatory]
    other_fx = [e for e in effects if e["name"] not in mandatory]

    used = len(mandatory_fx)                      # mandatory always fits (budget >= 1)
    keep_transition = transition != "cut" and (used + 1) <= max_per_scene
    if keep_transition:
        used += 1
    room = max(0, max_per_scene - used)
    final_fx = mandatory_fx + other_fx[:room]
    return (transition if keep_transition else "cut"), final_fx


def ensure_shots(raw_shots, scene_no: int, on_screen_text: str) -> list[dict]:
    """Every scene has >=1 shot, and EVERY shot carries a non-null asset_ref.

    Iris specifies each asset by a stable asset_ref + a content description; she does
    NOT resolve a URI or a license (that's the Asset Sourcer #5). A missing/blank
    asset_ref is filled with the deterministic scheme `s{scene_no}-{i}`.
    """
    shots: list[dict] = []
    raw = raw_shots if isinstance(raw_shots, list) and raw_shots else None
    if raw is None:
        # No shots from the brain -> one honest default carrying the on-screen line.
        raw = [{"kind": "title", "content": on_screen_text or ""}]
    for i, sh in enumerate(raw, start=1):
        sh = sh if isinstance(sh, dict) else {}
        ref = sh.get("asset_ref")
        if not (isinstance(ref, str) and ref.strip()):
            ref = f"s{scene_no}-{i}"
        kind = str(sh.get("kind", "image")).strip() or "image"
        content = str(sh.get("content", "")).strip()
        # Auto-tag model/brand shots so Magpie skips them and Mason renders a brand-chip.
        # Pure-typography shots are left alone (a title mentioning a model is still text).
        if kind not in _TEXT_KINDS and kind not in ("brand", "chip") \
                and _names_a_brand(f"{content} {ref}"):
            kind = "brand"
        shots.append({"kind": kind, "content": content, "asset_ref": ref})
    return shots


# ======================================================================
# Layout selection heuristics — Iris's deterministic fallback when the brain omits or
# botches a layout. The brain still chooses freely (within LAYOUTS) from the prompt; this
# guarantees the high-value data layouts fire on the obvious signals even offline.
#   single dominant statistic        -> big-number
#   chronological / ordered process  -> timeline
#   else                             -> centered-statement (the restrained default)
# ======================================================================
_YEAR_HINT = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
_CHRONO_HINT = re.compile(
    r"\b(timeline|history|chronolog|over the years|step\s*\d|phase\s*\d|stage\s*\d|"
    r"first.*then|evolution|decade|century|era)\b", re.IGNORECASE)
_STAT_HINT = re.compile(
    r"(\$?\s*\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*(?:%|×|x|mg|m|k|b|bn|million|billion|"
    r"percent|fps|x\b))", re.IGNORECASE)
_BARE_NUM = re.compile(r"\d")


def _scene_blob(scene: dict) -> str:
    """The text Iris reads to pick a layout: the line, the point, the visual note."""
    return " ".join(str(scene.get(k) or "") for k in
                    ("on_screen_text", "point", "visual_note"))


def _layout_hint_for_scene(scene: dict) -> str:
    """Pick a layout from a script scene's signals (deterministic). big-number for a
    single dominant stat; timeline for a chronological/process scene; else the default."""
    blob = _scene_blob(scene)
    # two or more year markers, or an explicit chronology/process cue -> timeline
    years = _YEAR_HINT.findall(blob)
    if len(years) >= 2 or _CHRONO_HINT.search(blob):
        return "timeline"
    # a short line dominated by a single magnitude stat -> big-number. Require a stat and
    # a SHORT line (the Vox big-number is one number, not a sentence full of figures).
    ost = str(scene.get("on_screen_text") or "")
    if _STAT_HINT.search(blob) and len(ost.split()) <= 6 and _BARE_NUM.search(ost):
        # not a multi-number comparison (that's data-chart) — at most one number on screen
        if len(re.findall(r"\d+(?:\.\d+)?", ost)) <= 1:
            return "big-number"
    return "centered-statement"


# ======================================================================
# Prompt builders — surface the script's signals to the brain
# ======================================================================
def _scene_signals(script: dict, limit: int = 40) -> str:
    lines = []
    for i, s in enumerate(script.get("scenes", [])[:limit], start=1):
        n = s.get("scene_no", i)
        beat = s.get("beat", "point")
        ost = (s.get("on_screen_text") or "").strip()
        vn = (s.get("visual_note") or "").strip()
        nclaims = len(s.get("claims") or [])
        kind = "data/quote" if nclaims else "talk"
        lines.append(
            f"[scene {n}] beat={beat} claims={nclaims} ({kind})\n"
            f"    point: {(s.get('point') or '').strip()}\n"
            f"    on_screen_text: {ost or '(none)'}\n"
            f"    visual_note: {vn or '(none)'}")
    return "\n".join(lines) or "(no scenes)"


def _vocab_block() -> str:
    return (
        "LAYOUTS (pick ONE per scene — composition only):\n  "
        + ", ".join(LAYOUTS) + "\n"
        "TRANSITIONS (pick ONE per scene; 'cut' is the default, zero motion):\n  "
        + ", ".join(TRANSITIONS) + "\n"
        "EFFECTS (per-scene, varying techniques — choose sparingly within the budget):\n  "
        + ", ".join(EFFECTS) + "\n"
        "TEXTURES (global, always-on hand-made layer — set once in the style guide):\n  "
        + ", ".join(TEXTURES) + "\n"
        "CHART_KINDS (ONLY on a 'data-chart' scene — set chart_kind to one of these):\n  "
        + ", ".join(CHART_KINDS) + "\n"
        "SHADER_TRANSITIONS (ONLY on the signature beat — set signature_transition to one):\n  "
        + ", ".join(SHADER_TRANSITIONS))


def _build_style_prompt(script: dict, treatment: dict | None = None) -> str:
    title = script.get("working_title") or "(untitled)"
    n = len(script.get("scenes") or [])
    return (
        f"=== METHOD ===\n{SKILL}\n\n"
        f"{_treatment_block(treatment)}"
        f"=== THE SCRIPT YOU ARE DESIGNING FOR ===\n"
        f"WORKING TITLE: {title}\n"
        f"SCENES: {n}\n\n"
        f"SCENE SIGNALS (read these to pick a topic-appropriate, restrained look):\n"
        f"{_scene_signals(script)}\n\n"
        f"=== THE VOCABULARY (use ONLY these names) ===\n{_vocab_block()}\n\n"
        "Design the GLOBAL style guide only (the per-scene storyboard is a separate "
        "job). Choose a restrained base palette appropriate to this topic, an "
        "editorial type system (display/body/caption roles + weights), a motion "
        "budget and a stepped-ease philosophy, a small global texture set drawn from "
        "TEXTURES, and a base render fps. Do NOT choose per-scene layouts or effects "
        "here. Do NOT pick the signature highlight — it is the fixed house color and "
        "is added for you. Return ONLY the JSON object from 'Your style output "
        "contract'."
    )


def _build_storyboard_prompt(script: dict, style_guide: dict,
                             treatment: dict | None = None) -> str:
    motion = (style_guide or {}).get("motion") or {}
    budget = clamp_max_per_scene(motion.get("max_per_scene", DEFAULT_MAX_PER_SCENE))
    return (
        f"=== METHOD ===\n{SKILL}\n\n"
        f"{_treatment_block(treatment)}"
        f"=== THE APPROVED STYLE GUIDE (design within it) ===\n"
        f"motion budget (max techniques per scene, incl. a non-cut transition): {budget}\n"
        f"palette + type are set; the signature highlight is {SIGNATURE_HIGHLIGHT}.\n\n"
        f"=== THE SCRIPT, SCENE BY SCENE ===\n{_scene_signals(script)}\n\n"
        f"=== THE VOCABULARY (use ONLY these names) ===\n{_vocab_block()}\n\n"
        "Build the storyboard: EXACTLY one storyboard scene per script scene, in "
        "order. For each scene choose ONE layout (from LAYOUTS) using the visual_note "
        "/ on_screen_text density / claims. Layout heuristics: a single dominant "
        "statistic -> 'big-number'; a chronological history or an ordered process "
        "-> 'timeline'; a magnitude comparison of several numbers -> 'data-chart'; a "
        "head-to-head -> 'comparison-2up'; a conceptual relationship/process/how-it-works "
        "illustration with NO numbers (boxes, arrows, glyphs) -> 'diagram'. On a 'data-chart' scene set chart_kind from "
        "CHART_KINDS: 'bar' for category magnitude (default), 'line' for a trend over an "
        "ordered sequence (>=2 points), 'pie' for parts of a whole. Define its shots (each a `kind` + a "
        "content description — you reference assets, you do NOT resolve URLs or "
        "licenses). For any shot that shows a product/model LOGO or BRAND (e.g. a named "
        "AI model, a company logo), set kind:'brand' and NAME the model(s) in `content` "
        "— these are rendered as typographic brand-chips in HTML, never sourced as "
        "photos (logos are trademarked and not in the license-free archives). Choose "
        "ONE transition within budget, and assign a small effects "
        "array within budget. Flag EXACTLY ONE scene as the signature beat — the one "
        f"moment that earns the animated '{SIGNATURE_EFFECT}' flourish; every other "
        "scene leaves that effect alone. On THAT signature scene only, also set "
        "'signature_transition' to ONE token from SHADER_TRANSITIONS — the cinematic "
        "WebGL transition that carries the viewer INTO the beat, matched to its mood: "
        "'sdf-iris' for a clean reveal/zoom-to-focus, 'glitch' for disruption/'everything "
        "breaks' energy, 'whip-pan' for a fast momentum cut, 'domain-warp' for a dreamy/"
        "melting shift. Omit it on every other scene. Stay ruthless: most scenes want a "
        "plain 'cut' and zero or one effect. Return ONLY the JSON object from 'Your "
        "storyboard output contract'."
    )


# ======================================================================
# Assembly — turn the brain's taste into the frozen contract shapes (code-enforced)
# ======================================================================
def assemble_style(script: dict, llm_out: dict) -> dict:
    """Shape the brain's taste into a contract-valid style_guide dict (envelope-free)."""
    out = llm_out if isinstance(llm_out, dict) else {}

    # Bundled OFL pairing (replaces the proprietary GT Sectra): Fraunces (the wonky
    # editorial display serif) + Inter (neutral body/caption). Both are SIL OFL 1.1 and
    # bundled locally by Mason as @font-face — never a proprietary or render-time-fetched
    # face. (Mason snaps any unbundled family to a guaranteed Noto fallback.)
    typography = out.get("typography") if isinstance(out.get("typography"), dict) else {}
    typography.setdefault("display", {"family": "Fraunces", "weight": 700})
    typography.setdefault("body", {"family": "Inter", "weight": 400})
    typography.setdefault("caption", {"family": "Inter", "weight": 500})
    typography.setdefault("scale", 1.25)

    motion_in = out.get("motion") if isinstance(out.get("motion"), dict) else {}
    motion = {
        "max_per_scene": clamp_max_per_scene(motion_in.get("max_per_scene",
                                                            DEFAULT_MAX_PER_SCENE)),
        "easing": str(motion_in.get("easing", "stepped")).strip() or "stepped",
        "transition_rules": str(motion_in.get("transition_rules",
                                              "cut by default")).strip() or "cut by default",
        "philosophy": str(motion_in.get("philosophy",
                                        "stepped, deliberate motion — never smooth for "
                                        "its own sake")).strip(),
    }

    layout_in = out.get("layout") if isinstance(out.get("layout"), dict) else {}
    layout = {
        "grid": str(layout_in.get("grid", "12-col")).strip() or "12-col",
        "safe_margins": str(layout_in.get("safe_margins", "6%")).strip() or "6%",
        "vocabulary": list(LAYOUTS),     # the finite menu the storyboard draws from
    }

    dos = [str(x).strip() for x in (out.get("dos") or []) if str(x).strip()]
    donts = [str(x).strip() for x in (out.get("donts") or []) if str(x).strip()]
    # Iris's house rules are always present, brain additions appended.
    base_dos = ["one point per scene", f"one {SIGNATURE_EFFECT} beat per video",
                "restraint: a smart magazine, not a fireworks show"]
    base_donts = ["rainbow palettes", "gratuitous shader transitions",
                  "motion for motion's sake"]
    dos = base_dos + [d for d in dos if d not in base_dos]
    donts = base_donts + [d for d in donts if d not in base_donts]

    return {
        "palette": enforce_palette(out.get("palette")),
        "typography": typography,
        "motion": motion,
        "layout": layout,
        "fps": clamp_fps(out.get("fps", DEFAULT_FPS)),
        "textures": normalize_textures(out.get("textures")),
        "dos": dos,
        "donts": donts,
        "reference_note": str(out.get("reference_note",
                                      "editorial explainer / Vox-style restraint")).strip(),
    }


def assemble_storyboard(script: dict, style_guide: dict, llm_out: dict) -> dict:
    """Shape the brain's taste into a contract-valid storyboard dict (envelope-free).

    Enforces, in code: scene-count parity with the script, vocabulary membership,
    the motion budget, exactly-one signature beat, the signature effect on exactly
    that beat (and nowhere else), and a non-null asset_ref on every shot.
    """
    sg = style_guide or {}
    max_per_scene = clamp_max_per_scene((sg.get("motion") or {}).get("max_per_scene",
                                                                     DEFAULT_MAX_PER_SCENE))

    script_scenes = script.get("scenes") or []
    raw_by_no = {}
    for r in (llm_out.get("scenes") if isinstance(llm_out, dict) else []) or []:
        if isinstance(r, dict) and r.get("scene_no") is not None:
            raw_by_no[r.get("scene_no")] = r

    # Build EXACTLY one storyboard scene per script scene, in script order.
    scenes: list[dict] = []
    for i, ss in enumerate(script_scenes, start=1):
        n = ss.get("scene_no", i)
        raw = raw_by_no.get(n) or raw_by_no.get(i) or {}

        layout = str(raw.get("layout", "")).strip()
        if layout not in LAYOUTS:
            # the brain omitted/botched it -> deterministic signal-based fallback
            # (single dominant stat -> big-number; chronological -> timeline; else default)
            layout = _layout_hint_for_scene(ss)
        transition = str(raw.get("transition", "")).strip()
        if transition not in TRANSITIONS:
            transition = "cut"
        on_screen_text = (str(raw.get("on_screen_text", "")).strip()
                          or (ss.get("on_screen_text") or "").strip())
        # data-chart sub-kind (bar|line|pie) — only meaningful on a data-chart scene;
        # unknown/absent snaps to "bar" (Mason's default). Dropped on non-chart layouts.
        chart_kind = str(raw.get("chart_kind", "")).strip()
        if chart_kind not in CHART_KINDS:
            chart_kind = "bar"

        scene = {
            "scene_no": n,
            "layout": layout,
            "shots": ensure_shots(raw.get("shots"), n, on_screen_text),
            "on_screen_text": on_screen_text,
            "transition": transition,
            "effects": normalize_effects(raw.get("effects")),
            "signature_beat": bool(raw.get("signature_beat")),
        }
        if layout == "data-chart":
            scene["chart_kind"] = chart_kind
        scenes.append(scene)

    # EXACTLY ONE signature beat — and the signature EFFECT lives on exactly it.
    sig_idx = choose_signature_scene(scenes)
    for i, sc in enumerate(scenes):
        is_sig = (i == sig_idx)
        sc["signature_beat"] = is_sig
        # Strip the signature effect from every scene first (it's the beat's alone)...
        sc["effects"] = [e for e in sc["effects"] if e["name"] != SIGNATURE_EFFECT]
        sc.pop("signature_transition", None)   # the shader belongs to the beat alone
        if is_sig:
            # ...then guarantee it on the beat (prepended so the budget keeps it).
            sc["effects"] = [{"name": SIGNATURE_EFFECT, "params": {}}] + sc["effects"]
            # Iris's chosen WebGL transition INTO the beat (Mason renders it at assembly).
            # Honor her pick when it's in the closed vocab; otherwise leave it off and let
            # Mason apply its taste default — never invent an unknown token.
            raw_sig = raw_by_no.get(sc["scene_no"]) or raw_by_no.get(i + 1) or {}
            shader = str(raw_sig.get("signature_transition", "")).strip()
            if shader in SHADER_TRANSITIONS:
                sc["signature_transition"] = shader
        mandatory = {SIGNATURE_EFFECT} if is_sig else set()
        sc["transition"], sc["effects"] = apply_motion_budget(
            sc["transition"], sc["effects"], max_per_scene, mandatory)

    return {"total_scenes": len(scenes), "scenes": scenes}


# ======================================================================
# The pure seams the adapter uses (no file I/O, no schema envelope)
# ======================================================================
def _treatment_block(treatment: dict | None) -> str:
    """The director's creative treatment as a prompt section for Iris (empty when absent).
    She MAPS its mood/intent into her closed vocabularies — picking layouts/effects/
    transitions deliberately and landing the ONE signature beat at the peak — instead of
    defaulting to restraint. Direction only; it never overrides the closed vocabulary."""
    if not isinstance(treatment, dict) or not treatment:
        return ""
    lines = ["=== THE DIRECTOR'S CREATIVE TREATMENT (design TO this — map it into the "
             "vocabulary, don't default to plain) ==="]
    if treatment.get("rhythm"):
        lines.append(f"RHYTHM: {treatment['rhythm']} — vary layout/effect/transition energy "
                     "across the arc; the peak earns the signature beat, the breathe stays calm.")
    if treatment.get("emphasis"):
        lines.append(f"THE ONE IDEA TO LAND (let the design serve it): {treatment['emphasis']}")
    if treatment.get("visual_world"):
        lines.append(f"VISUAL WORLD: {treatment['visual_world']}")
    if treatment.get("mood_refs"):
        lines.append("MOOD REFS (translate into palette intensity + motion feel, not literally): "
                     + "; ".join(treatment["mood_refs"]))
    if treatment.get("motifs"):
        lines.append("RECURRING MOTIFS: " + "; ".join(treatment["motifs"]))
    beats = treatment.get("beats") or []
    if beats:
        lines.append("BEATS (use each beat's mood/intent to choose this scene's layout, "
                     "effects, and transition deliberately):")
        for b in beats[:12]:
            lines.append(f"  · {b.get('beat', '?')}: {b.get('mood', '')} — "
                         f"{b.get('intent', '')}")
    if treatment.get("negative"):
        lines.append("AVOID: " + "; ".join(treatment["negative"]))
    return "\n".join(lines) + "\n\n"


def design_style(script: dict, *, chat_fn=llm.chat, treatment: dict | None = None) -> dict:
    """Turn a script into a style_guide dict (frozen shape, minus schema_version).

    Validates the script, makes ONE taste call to the brain, then enforces every
    style invariant in code. Atlas stamps schema_version + validates at the boundary.
    `treatment` (optional): the director's creative direction, folded into the prompt.
    """
    ok, reason = validate_script(script)
    if not ok:
        raise ValueError(reason)
    llm_out = _chat_json(SOUL, _build_style_prompt(script, treatment), chat_fn=chat_fn)
    return assemble_style(script, llm_out)


def build_storyboard(script: dict, style_guide: dict | None = None, *,
                     chat_fn=llm.chat, treatment: dict | None = None) -> dict:
    """Turn a script (+ the approved style guide) into a storyboard dict.

    Validates the script, makes ONE taste call to the brain, then enforces every
    storyboard invariant in code. A missing style_guide falls back to Iris's defaults
    so the engine stays independently runnable. Envelope-free; atlas stamps + validates.
    `treatment` (optional): the director's creative direction, folded into the prompt.
    """
    ok, reason = validate_script(script)
    if not ok:
        raise ValueError(reason)
    sg = style_guide or {"motion": {"max_per_scene": DEFAULT_MAX_PER_SCENE},
                         "palette": {"signature_highlight": SIGNATURE_HIGHLIGHT}}
    llm_out = _chat_json(SOUL, _build_storyboard_prompt(script, sg, treatment), chat_fn=chat_fn)
    return assemble_storyboard(script, sg, llm_out)


# ======================================================================
# Creative treatment (prompt-expansion) — the director's direction layer.
# Runs AFTER research, BEFORE the script, on the strong creative model, grounded in the
# distilled CRAFT digest. Marlow (script) and Iris (style/storyboard) both consume it so
# they use the closed vocabularies intentionally instead of defaulting to restraint.
# ======================================================================
_TREATMENT_MAX_BEATS = 12


def validate_brief_for_treatment(brief) -> tuple[bool, str]:
    """A brief is treatable if it carries an overview or facts to direct a story around."""
    if not isinstance(brief, dict):
        return False, "That's not a research brief — I need the brief JSON object."
    if not (brief.get("overview") or brief.get("verified_facts") or brief.get("items")):
        return False, "This brief has no overview or facts — nothing to build direction from."
    return True, ""


def _brief_digest_for_treatment(brief: dict) -> str:
    topic = brief.get("topic") or brief.get("working_title") or "(untitled)"
    overview = (brief.get("overview") or "").strip()
    facts = brief.get("verified_facts") or []
    fact_lines = []
    for f in facts[:10]:
        c = f.get("claim") if isinstance(f, dict) else str(f)
        if c:
            fact_lines.append(f"- {str(c).strip()}")
    audience = brief.get("target_audience") or "a curious general audience"
    angle = brief.get("angle") or ""
    angle_note = f"\nANGLE: {angle}" if angle else ""
    return (f"TOPIC: {topic}\nAUDIENCE: {audience}{angle_note}\n\n"
            f"OVERVIEW:\n{overview}\n\n"
            f"VERIFIED FACTS (the spine of the story — direct around these, invent nothing):\n"
            + ("\n".join(fact_lines) or "(none listed)"))


def _thematic_anchor_block(brief: dict) -> str:
    """The research's thematic anchor as a prompt section (empty when absent).

    If Sage found a thesis, Iris's ENTIRE treatment orbits it — the thesis is the sun,
    every creative decision is in its gravity. When there's no anchor the block is empty
    and Iris runs her standard creative process (backward-compatible)."""
    anchor = brief.get("thematic_anchor")
    if not isinstance(anchor, dict) or not anchor.get("thesis_statement"):
        return ""
    thesis = str(anchor.get("thesis_statement", "")).strip()
    payload = str(anchor.get("emotional_payload", "")).strip()
    lines = [
        "=== THE THEMATIC ANCHOR (this is the SUN — everything orbits it) ===",
        "Your entire creative treatment must be built in service of this single thesis. "
        "This is not optional.",
        f"THESIS: {thesis}",
        f"EMOTIONAL PAYLOAD: {payload}",
    ]
    if anchor.get("counter_intuitive_angle"):
        lines.append(f"WHY IT SURPRISES: {str(anchor['counter_intuitive_angle']).strip()}")
    lines += [
        "",
        "Your creative treatment must answer these questions:",
        "1. What visual world makes this thesis feel undeniable, not just stated?",
        "2. What rhythm best delivers the emotional payload? If the payload is "
        "\"vertigo of realizing an assumption is wrong,\" your rhythm should feel "
        "disorienting at first, then settle into clarity.",
        "3. What mood refs align with this payload? Find film scenes, paintings, or "
        "photographic styles that evoke the SAME feeling, not just the same topic.",
        "4. How does each beat serve the thesis? The hook plants the question the thesis "
        "answers. The build presents the evidence. The peak IS the thesis landing. The "
        "breathe lets the implication sink in. The CTA channels the emotional payload "
        "into action. The ONE idea you name (emphasis) must BE this thesis.",
    ]
    return "\n".join(lines) + "\n\n"


def _build_treatment_prompt(brief: dict) -> str:
    return (
        f"=== CREATIVE CRAFT (your method for this job) ===\n{CRAFT}\n\n"
        f"{_thematic_anchor_block(brief)}"
        f"=== THE RESEARCH BRIEF (your raw material AND your fence) ===\n"
        f"{_brief_digest_for_treatment(brief)}\n\n"
        "Produce the CREATIVE TREATMENT for this video: the director's direction the "
        "scriptwriter and the art director will both build from. Apply the CRAFT. Name the "
        "rhythm arc, the visual world + mood references (cultural, NOT hex), the ONE idea the "
        "video must land, the recurring motifs, and a short negative list. Then break the "
        "story into ordered BEATS (hook → … → cta); for each beat give a concept, a mood, the "
        "single emphasis word that must land, and the felt pacing intent. Direction ONLY — no "
        "coordinates, no hex, no per-scene decoratives; every note must be actionable inside "
        "closed layout/effect/transition vocabularies.\n\n"
        "Output ONLY this JSON object (no prose, no fences):\n"
        '{"rhythm":"hook-BUILD-PEAK-breathe-CTA","visual_world":"…","mood_refs":["…"],'
        '"emphasis":"…","motifs":["…"],"negative":["…"],'
        '"beats":[{"beat":"hook","concept":"…","mood":"…","emphasis_word":"…","intent":"…"}]}'
    )


def _as_str_list(v) -> list[str]:
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def assemble_treatment(brief: dict, llm_out: dict) -> dict:
    """Normalize the brain's reply into the creative_treatment shape (minus schema_version).
    Keeps only contract fields, coerces types, caps the beat count. Pure + deterministic."""
    out = llm_out if isinstance(llm_out, dict) else {}
    beats = []
    for b in (out.get("beats") or [])[:_TREATMENT_MAX_BEATS]:
        if not isinstance(b, dict):
            continue
        beats.append({
            "beat": str(b.get("beat", "")).strip()[:40],
            "concept": str(b.get("concept", "")).strip()[:400],
            "mood": str(b.get("mood", "")).strip()[:200],
            "emphasis_word": str(b.get("emphasis_word", "")).strip()[:80],
            "intent": str(b.get("intent", "")).strip()[:300],
        })
    return {
        "rhythm": str(out.get("rhythm", "")).strip()[:120],
        "visual_world": str(out.get("visual_world", "")).strip()[:600],
        "mood_refs": _as_str_list(out.get("mood_refs"))[:8],
        "emphasis": str(out.get("emphasis", "")).strip()[:300],
        "motifs": _as_str_list(out.get("motifs"))[:8],
        "negative": _as_str_list(out.get("negative"))[:8],
        "beats": beats,
    }


def design_treatment(brief: dict, *, chat_fn=llm.chat) -> dict:
    """Turn a research brief into a creative_treatment dict (frozen shape, minus
    schema_version). Validates the brief, makes ONE direction call to the brain grounded in
    the CRAFT digest, normalizes in code. Atlas stamps schema_version + validates."""
    ok, reason = validate_brief_for_treatment(brief)
    if not ok:
        raise ValueError(reason)
    llm_out = _chat_json(SOUL, _build_treatment_prompt(brief), chat_fn=chat_fn)
    return assemble_treatment(brief, llm_out)


# ======================================================================
# Narrative Intent (the emotional score) — the machine-actionable bridge.
# Runs AFTER the creative_treatment, BEFORE the script, on the strong creative model.
# The treatment is poetic ("awe-inspiring"); the downstream engines only read structural
# keywords, so the EMOTIONAL objective used to evaporate at every handoff. This stage
# translates Iris's poetry into a parameterized blueprint in CLOSED vocabularies that
# Marlow (word choice / sentence length) and Cadence (TTS pacing, EQ, music, SFX) can
# both ACT on without re-interpreting the prose. Advisory + optional: a missing intent
# leaves every downstream stage on its prior behavior (backward-compatible).
#
# The closed vocabularies below are kept in lock-step with the enums in
# atlas/contracts/narrative_intent.schema.json (the schema is the authority; these are
# the engine-side mirror the prompt advertises and the assembler enforces).
# ======================================================================
TONE_PROFILES = (
    "urgent_reveal", "thoughtful_unpacking", "dark_warning",
    "optimistic_march", "curious_exploration",
)
ARC_PHASES = ("hook", "build", "peak", "breathe", "cta")
EMOTIONS = (
    "curiosity", "surprise", "awe", "satisfaction", "determination",
    "tension", "dread", "hope", "nostalgia", "clarity",
    "urgency", "wonder", "unease", "triumph", "melancholy", "empathy",
)
PACING_DIRECTIVES = (
    "punchy_staccato", "driving", "measured", "flowing",
    "contemplative", "building", "breathless", "deliberate_pause",
)
TEXTURE_DIRECTIVES = (
    "clean_high_contrast", "warm_grain", "dark_moody", "bright_airy",
    "gritty_raw", "cinematic_widescreen", "soft_focus", "stark_minimal",
)
# Sensible per-phase defaults so the arc is always complete even when the brain omits one.
_ARC_DEFAULTS = {
    "hook":    {"dominant_emotion": "curiosity", "intensity": 9, "duration_goal_sec": 8.0},
    "build":   {"dominant_emotion": "surprise", "intensity": 7, "duration_goal_sec": 25.0},
    "peak":    {"dominant_emotion": "awe", "intensity": 10, "duration_goal_sec": 15.0},
    "breathe": {"dominant_emotion": "satisfaction", "intensity": 4, "duration_goal_sec": 10.0},
    "cta":     {"dominant_emotion": "determination", "intensity": 8, "duration_goal_sec": 12.0},
}
_INTENT_MAX_SCENES = 60


def validate_treatment_for_intent(treatment) -> tuple[bool, str]:
    """An intent is buildable from a treatment that carries any felt direction to score."""
    if not isinstance(treatment, dict):
        return False, "That's not a creative treatment — I need the treatment JSON object."
    if not (treatment.get("rhythm") or treatment.get("emphasis")
            or treatment.get("visual_world") or treatment.get("beats")):
        return False, ("This treatment has no rhythm/emphasis/world/beats — nothing to "
                       "translate into an emotional score.")
    return True, ""


def _pick(value, allowed: tuple[str, ...], default: str) -> str:
    """Snap a brain-supplied token to the closed vocabulary; `default` when unknown."""
    v = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return v if v in allowed else default


def _clamp_intensity(value, default: int = 5) -> int:
    """An intensity coerced to the inclusive 1..10 band; `default` when unusable."""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(1, min(10, v))


def _clamp_duration(value, default: float = 8.0) -> float:
    """A non-negative duration-goal in seconds; `default` when unusable."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return round(max(0.0, v), 1)


def _treatment_digest_for_intent(treatment: dict) -> str:
    lines = [f"RHYTHM: {treatment.get('rhythm') or '(none)'}",
             f"THE ONE IDEA TO LAND: {treatment.get('emphasis') or '(none)'}",
             f"VISUAL WORLD: {treatment.get('visual_world') or '(none)'}"]
    if treatment.get("mood_refs"):
        lines.append("MOOD REFS: " + "; ".join(treatment["mood_refs"]))
    if treatment.get("motifs"):
        lines.append("MOTIFS: " + "; ".join(treatment["motifs"]))
    beats = treatment.get("beats") or []
    if beats:
        lines.append("BEATS (each carries the felt mood + intent you must score):")
        for b in beats[:_TREATMENT_MAX_BEATS]:
            lines.append(f"  · {b.get('beat', '?')}: concept={b.get('concept', '')} | "
                         f"mood={b.get('mood', '')} | emphasis={b.get('emphasis_word', '')} | "
                         f"intent={b.get('intent', '')}")
    if treatment.get("negative"):
        lines.append("AVOID: " + "; ".join(treatment["negative"]))
    return "\n".join(lines)


def _intent_vocab_block() -> str:
    return (
        "TONE_PROFILE (pick ONE for the whole video):\n  " + ", ".join(TONE_PROFILES) + "\n"
        "EMOTIONS (pick from these ONLY, per phase + per scene):\n  " + ", ".join(EMOTIONS) + "\n"
        "ARC_PHASES (each scene belongs to exactly one):\n  " + ", ".join(ARC_PHASES) + "\n"
        "PACING_DIRECTIVES (how the narration is voiced — pick ONE per scene):\n  "
        + ", ".join(PACING_DIRECTIVES) + "\n"
        "TEXTURE_DIRECTIVES (the visual+sonic surface — pick ONE per scene):\n  "
        + ", ".join(TEXTURE_DIRECTIVES))


_INTENT_SYSTEM = (
    "You are translating a director's creative vision into a technical brief that other "
    "AI agents can execute faithfully. Be specific, be emotional, be precise. This is not "
    "cold documentation; it is an emotional score that must survive translation. Every "
    "value you choose will be read by a machine and turned into word choice, sentence "
    "length, vocal pacing, EQ, music and sound — so a vague or off-vocabulary value is a "
    "lost instruction. Honor the closed vocabularies exactly.")


def _brief_thesis_hint(brief: dict) -> str:
    """A short grounding line from the research so the thesis stays true to the facts."""
    if not isinstance(brief, dict):
        return ""
    topic = brief.get("topic") or brief.get("working_title") or ""
    overview = (brief.get("overview") or "").strip()
    return (f"TOPIC: {topic}\nOVERVIEW (ground the thesis in this — invent nothing):\n"
            f"{overview[:800]}" if (topic or overview) else "")


def _build_intent_prompt(treatment: dict, brief: dict) -> str:
    return (
        f"=== THE RESEARCH (your fence — the thesis must be TRUE to it) ===\n"
        f"{_brief_thesis_hint(brief) or '(no brief supplied)'}\n\n"
        f"=== THE DIRECTOR'S CREATIVE TREATMENT (translate THIS into the score) ===\n"
        f"{_treatment_digest_for_intent(treatment)}\n\n"
        f"=== THE CLOSED VOCABULARY (use ONLY these tokens) ===\n{_intent_vocab_block()}\n\n"
        "Produce the NARRATIVE INTENT — the emotional score the scriptwriter and the "
        "audio designer will both execute. Translate the treatment's poetry into precise, "
        "machine-actionable parameters; do not flatten the feeling, sharpen it.\n\n"
        "1. video_level: a one-sentence core_thesis (the single argument the video exists "
        "to make), an emotional_journey (what the viewer feels at the START vs. the END — "
        "be specific, name both ends), and ONE tone_profile.\n"
        "2. emotional_arc: for EACH of hook, build, peak, breathe, cta give a "
        "dominant_emotion (from EMOTIONS), an intensity 1-10, and a duration_goal_sec.\n"
        "3. per_scene_intent: an ordered list, ONE entry per anticipated scene (hook first, "
        "cta last). For each give: scene_index (0-based, contiguous), its arc_phase, a "
        "primary_emotion, an intensity 1-10, a pacing_directive, a texture_directive, and a "
        "delivery_note — a concrete, human instruction (e.g. 'Deliver this like you just "
        "learned a secret and have 30 seconds to tell someone before it goes public.').\n\n"
        "Output ONLY this JSON object (no prose, no fences):\n"
        '{"video_level":{"core_thesis":"…","emotional_journey":"…","tone_profile":"…"},'
        '"emotional_arc":{"hook":{"dominant_emotion":"…","intensity":9,"duration_goal_sec":8},'
        '"build":{…},"peak":{…},"breathe":{…},"cta":{…}},'
        '"per_scene_intent":[{"scene_index":0,"arc_phase":"hook","primary_emotion":"curiosity",'
        '"intensity":9,"pacing_directive":"punchy_staccato","texture_directive":"clean_high_contrast",'
        '"delivery_note":"…"}]}'
    )


def assemble_narrative_intent(treatment: dict, llm_out: dict) -> dict:
    """Normalize the brain's reply into the narrative_intent shape (minus schema_version).

    Pure + deterministic: snaps every token to a closed vocabulary, clamps every
    intensity to 1..10 and every duration to >= 0, guarantees all five arc phases, and
    RE-INDEXES per_scene_intent to a contiguous 0-based order (the brain's scene_index is
    advisory; scene order is the source of truth). Off-vocabulary or missing values fall
    back to a sensible default so the artifact is always complete and contract-valid.
    """
    out = llm_out if isinstance(llm_out, dict) else {}

    vl = out.get("video_level") if isinstance(out.get("video_level"), dict) else {}
    video_level = {
        "core_thesis": str(vl.get("core_thesis", "")).strip()[:400],
        "emotional_journey": str(vl.get("emotional_journey", "")).strip()[:400],
        "tone_profile": _pick(vl.get("tone_profile"), TONE_PROFILES, "curious_exploration"),
    }

    arc_in = out.get("emotional_arc") if isinstance(out.get("emotional_arc"), dict) else {}
    emotional_arc = {}
    for phase in ARC_PHASES:
        node = arc_in.get(phase) if isinstance(arc_in.get(phase), dict) else {}
        d = _ARC_DEFAULTS[phase]
        emotional_arc[phase] = {
            "dominant_emotion": _pick(node.get("dominant_emotion"), EMOTIONS,
                                      d["dominant_emotion"]),
            "intensity": _clamp_intensity(node.get("intensity"), d["intensity"]),
            "duration_goal_sec": _clamp_duration(node.get("duration_goal_sec"),
                                                 d["duration_goal_sec"]),
        }

    per_scene = []
    raw_scenes = out.get("per_scene_intent")
    raw_scenes = raw_scenes if isinstance(raw_scenes, list) else []
    for idx, sc in enumerate(raw_scenes[:_INTENT_MAX_SCENES]):
        sc = sc if isinstance(sc, dict) else {}
        phase = _pick(sc.get("arc_phase"), ARC_PHASES, "build")
        per_scene.append({
            "scene_index": idx,  # re-indexed: contiguous 0-based, scene order is authority
            "arc_phase": phase,
            "primary_emotion": _pick(sc.get("primary_emotion"), EMOTIONS,
                                     emotional_arc[phase]["dominant_emotion"]),
            "intensity": _clamp_intensity(sc.get("intensity"),
                                          emotional_arc[phase]["intensity"]),
            "pacing_directive": _pick(sc.get("pacing_directive"), PACING_DIRECTIVES, "measured"),
            "texture_directive": _pick(sc.get("texture_directive"), TEXTURE_DIRECTIVES,
                                       "clean_high_contrast"),
            "delivery_note": str(sc.get("delivery_note", "")).strip()[:500],
        })

    return {
        "video_level": video_level,
        "emotional_arc": emotional_arc,
        "per_scene_intent": per_scene,
    }


def design_narrative_intent(creative_treatment: dict, research_brief: dict, *,
                            chat_fn=llm.chat) -> dict:
    """Turn a creative_treatment (+ the research brief) into a narrative_intent dict.

    The emotional bridge: Iris's poetic direction becomes a parameterized, closed-vocabulary
    score the script + audio engines can ACT on without re-interpreting prose. Validates the
    treatment, makes ONE call to the strong creative model (Opus, via llm.chat) under a
    crafted "emotional score" system prompt, then enforces every vocabulary/clamp invariant
    in code. Envelope-free; Atlas stamps schema_version + validates at the boundary.
    """
    ok, reason = validate_treatment_for_intent(creative_treatment)
    if not ok:
        raise ValueError(reason)
    llm_out = _chat_json(_INTENT_SYSTEM, _build_intent_prompt(creative_treatment,
                                                              research_brief or {}),
                         chat_fn=chat_fn)
    return assemble_narrative_intent(creative_treatment, llm_out)


# ======================================================================
# Motion Mood Board (the design-first visual architecture / the motion score).
# Runs AFTER narrative_intent (the emotional blueprint), BEFORE the script. It inverts
# the pipeline's creative logic: the visual language — pacing, texture, motion — is
# conceived from the emotional arc, then GOVERNS both Marlow's pacing AND Mason's motion
# design. Where narrative_intent says WHAT the viewer feels, this says HOW the frame
# moves to make them feel it, in CONCRETE HyperFrames directives Mason executes without
# interpretation.
#
# The closed vocabularies it snaps to ARE the engine's real HyperFrames axes (EFFECTS /
# LAYOUTS / TRANSITIONS / TEXTURES) — NOT a private subset — so every value maps to a
# token Mason can render. (A cross-engine parity test guards the schema enums in lock-
# step.) Advisory + optional: a missing mood board leaves every downstream stage on its
# prior behavior (backward-compatible).
# ======================================================================
# MMB-specific vocabularies (the new axes this artifact adds on top of the render axes).
TEMPOS = ("methodical", "conversational", "brisk_and_urgent", "slow_and_cinematic")
PACING_PROFILES = ("rapid_staccato", "steady_build", "slow_reveal",
                   "held_stillness", "conversational_flow")
# The render axes the mood board reuses verbatim (mirrored from the schema $defs). "none"
# is the mood board's "no effect" sentinel on top of the real EFFECTS vocabulary.
MOOD_BOARD_EFFECTS = EFFECTS + ("none",)
MOOD_BOARD_LAYOUTS = LAYOUTS
MOOD_BOARD_TRANSITIONS = TRANSITIONS
MOOD_BOARD_TEXTURES = ("clean",) + TEXTURES

_MMB_MAX_BEATS = 12

# Per-phase fallbacks so the architecture is always complete + invariant-holding even
# when the brain omits a beat (the motion analog of narrative_intent's _ARC_DEFAULTS).
# peak's dominant is the signature highlighter by construction (see _enforce_single_*).
_MMB_PHASE_DEFAULTS = {
    "hook":    {"primary_emotion": "curiosity", "intensity": 9,
                "pacing_profile": "rapid_staccato", "dominant_effect": "stutter-12fps",
                "transition_in": "cut", "layout_family": "centered-statement",
                "duration": 8.0},
    "build":   {"primary_emotion": "surprise", "intensity": 7,
                "pacing_profile": "steady_build", "dominant_effect": "stepped-ease",
                "transition_in": "cut", "layout_family": "list-stack", "duration": 25.0},
    "peak":    {"primary_emotion": "awe", "intensity": 10,
                "pacing_profile": "slow_reveal", "dominant_effect": SIGNATURE_EFFECT,
                "transition_in": "dip-to-black", "layout_family": "big-number",
                "duration": 15.0},
    "breathe": {"primary_emotion": "satisfaction", "intensity": 4,
                "pacing_profile": "held_stillness", "dominant_effect": "breathe",
                "transition_in": "dip-to-black", "layout_family": "quote-card",
                "duration": 10.0},
    "cta":     {"primary_emotion": "determination", "intensity": 8,
                "pacing_profile": "conversational_flow", "dominant_effect": "word-reveal",
                "transition_in": "cut", "layout_family": "title-card", "duration": 12.0},
}
_TONE_TO_TEMPO = {
    "urgent_reveal": "brisk_and_urgent", "dark_warning": "slow_and_cinematic",
    "thoughtful_unpacking": "methodical", "optimistic_march": "conversational",
    "curious_exploration": "conversational",
}


def _pick_token(value, allowed: tuple[str, ...], default: str) -> str:
    """Snap a brain-supplied token to a HYPHENATED closed vocabulary (effects/layouts/
    transitions/textures) — exact then case-insensitive. (`_pick` is for the underscore
    vocabularies; it would mangle 'stutter-12fps' -> 'stutter_12fps'.)"""
    v = str(value or "").strip()
    if v in allowed:
        return v
    low = v.lower()
    return next((a for a in allowed if a.lower() == low), default)


def _is_hex6(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"#[0-9A-Fa-f]{6}", value.strip()))


def validate_intent_for_mood_board(intent) -> tuple[bool, str]:
    """A mood board is buildable from a narrative_intent that carries any arc to translate."""
    if not isinstance(intent, dict):
        return False, "That's not a narrative intent — I need the emotional score JSON object."
    if not (intent.get("emotional_arc") or intent.get("per_scene_intent")
            or intent.get("video_level")):
        return False, ("This narrative intent has no arc/scenes/video_level — nothing to "
                       "translate into a motion architecture.")
    return True, ""


def _enforce_single_highlighter(beats: list[dict]) -> tuple[list[dict], str]:
    """Guarantee the #FFD000 signature highlighter appears on EXACTLY one beat (and as a
    dominant_effect), by construction. Returns (beats, signature_beat_id).

    Preference for WHERE it lands: a beat the brain already gave it -> the 'peak' beat ->
    the highest-intensity beat. The signature is the one flourish Iris won't cut; every
    other beat is stripped of it (a stripped dominant is promoted from its secondary)."""
    if not beats:
        return beats, ""

    def holds(b):
        return SIGNATURE_EFFECT in (b["dominant_effect"], b["secondary_effect"])

    sig = next((i for i, b in enumerate(beats) if holds(b)), None)
    if sig is None:
        sig = next((i for i, b in enumerate(beats) if b["arc_phase"] == "peak"), None)
    if sig is None:
        sig = max(range(len(beats)), key=lambda i: beats[i]["intensity"])

    for i, b in enumerate(beats):
        if i == sig:
            continue
        if b["dominant_effect"] == SIGNATURE_EFFECT:
            b["dominant_effect"] = (b["secondary_effect"]
                                    if b["secondary_effect"] not in (SIGNATURE_EFFECT, "none")
                                    else "none")
        if b["secondary_effect"] == SIGNATURE_EFFECT:
            b["secondary_effect"] = "none"

    sb = beats[sig]
    if sb["dominant_effect"] != SIGNATURE_EFFECT:
        if sb["secondary_effect"] == "none" and sb["dominant_effect"] != "none":
            sb["secondary_effect"] = sb["dominant_effect"]
        sb["dominant_effect"] = SIGNATURE_EFFECT
    if sb["secondary_effect"] == SIGNATURE_EFFECT:
        sb["secondary_effect"] = "none"
    return beats, sb["beat_id"]


def assemble_motion_mood_board(narrative_intent: dict, llm_out: dict) -> dict:
    """Normalize the brain's reply into the motion_mood_board shape (minus schema_version).

    Pure + deterministic: snaps every token to a closed vocabulary, clamps intensity to
    1..10 and durations to >= 0, pulls each beat's emotion/intensity/duration default from
    the narrative_intent arc (translating it, not duplicating/inventing it), guarantees a
    complete beat per arc phase when the brain omits the map, and enforces the EXACTLY-ONE
    #FFD000 highlighter invariant by construction. Off-vocabulary or missing values fall
    back so the artifact is always complete + contract-valid.
    """
    out = llm_out if isinstance(llm_out, dict) else {}
    intent = narrative_intent if isinstance(narrative_intent, dict) else {}
    arc = intent.get("emotional_arc") if isinstance(intent.get("emotional_arc"), dict) else {}
    vl_intent = intent.get("video_level") if isinstance(intent.get("video_level"), dict) else {}

    vl_in = out.get("video_level") if isinstance(out.get("video_level"), dict) else {}
    video_level = {
        "global_tempo": _pick(vl_in.get("global_tempo"), TEMPOS,
                              _TONE_TO_TEMPO.get(vl_intent.get("tone_profile"),
                                                 "conversational")),
        "global_texture": _pick_token(vl_in.get("global_texture"), MOOD_BOARD_TEXTURES,
                                      "clean"),
        "global_texture_justification":
            str(vl_in.get("global_texture_justification", "")).strip()[:400],
        "dominant_motion_philosophy":
            str(vl_in.get("dominant_motion_philosophy", "")).strip()[:300],
    }

    raw_beats = out.get("beat_map")
    if not (isinstance(raw_beats, list) and raw_beats):
        # No beats from the brain -> one per arc phase the intent carries (else all five).
        present = [p for p in ARC_PHASES if p in arc]
        raw_beats = [{"arc_phase": p} for p in (present or ARC_PHASES)]

    beats: list[dict] = []
    seen_ids: set[str] = set()
    for i, rb in enumerate(raw_beats[:_MMB_MAX_BEATS]):
        rb = rb if isinstance(rb, dict) else {}
        phase = _pick(rb.get("arc_phase"), ARC_PHASES,
                      ARC_PHASES[min(i, len(ARC_PHASES) - 1)])
        d = _MMB_PHASE_DEFAULTS[phase]
        node = arc.get(phase) if isinstance(arc.get(phase), dict) else {}
        node_emotion = node.get("dominant_emotion")

        beat_id = str(rb.get("beat_id", "")).strip() or f"b-{phase}"
        if beat_id in seen_ids:
            beat_id = f"{beat_id}-{i}"
        seen_ids.add(beat_id)

        dominant = _pick_token(rb.get("dominant_effect"), MOOD_BOARD_EFFECTS,
                               d["dominant_effect"])
        secondary = _pick_token(rb.get("secondary_effect"), MOOD_BOARD_EFFECTS, "none")
        if secondary == dominant and secondary != "none":
            secondary = "none"   # a secondary must not compete with / duplicate the dominant

        beat = {
            "beat_id": beat_id,
            "arc_phase": phase,
            "primary_emotion": _pick(rb.get("primary_emotion"), EMOTIONS,
                                     node_emotion if node_emotion in EMOTIONS
                                     else d["primary_emotion"]),
            "intensity": _clamp_intensity(rb.get("intensity"),
                                          _clamp_intensity(node.get("intensity"),
                                                           d["intensity"])),
            "pacing_profile": _pick(rb.get("pacing_profile"), PACING_PROFILES,
                                    d["pacing_profile"]),
            "dominant_effect": dominant,
            "secondary_effect": secondary,
            "transition_in": _pick_token(rb.get("transition_in"), TRANSITIONS,
                                         d["transition_in"]),
            "layout_family": _pick_token(rb.get("layout_family"), LAYOUTS,
                                         d["layout_family"]),
            "scene_duration_target_sec":
                _clamp_duration(rb.get("scene_duration_target_sec"),
                                _clamp_duration(node.get("duration_goal_sec"),
                                                d["duration"])),
        }
        mpo = rb.get("motion_parameter_overrides")
        if isinstance(mpo, dict) and mpo:
            beat["motion_parameter_overrides"] = mpo
        if str(rb.get("visual_mood_ref", "")).strip():
            beat["visual_mood_ref"] = str(rb["visual_mood_ref"]).strip()[:300]
        beats.append(beat)

    beats, sig_id = _enforce_single_highlighter(beats)
    board = {"video_level": video_level, "beat_map": beats}

    sbp_in = out.get("signature_beat_placement") \
        if isinstance(out.get("signature_beat_placement"), dict) else {}
    board["signature_beat_placement"] = {
        "beat_id": sig_id or str(sbp_in.get("beat_id", "")).strip(),
        "target_element": str(sbp_in.get("target_element", "")).strip()[:200],
        "justification": str(sbp_in.get("justification", "")).strip()[:400],
    }

    overrides = []
    for o in (out.get("palette_emotional_overrides") or [])[:8]:
        if not isinstance(o, dict) or not _is_hex6(o.get("accent_override")):
            continue
        overrides.append({
            "beat_id": str(o.get("beat_id", "")).strip(),
            "accent_override": o["accent_override"].strip(),
            "override_justification": str(o.get("override_justification", "")).strip()[:300],
        })
    if overrides:
        board["palette_emotional_overrides"] = overrides

    return board


_MOOD_BOARD_SYSTEM = (
    "You are Iris — but not the Iris who designs static style guides. You are Iris the "
    "Cinematographer: you think about how the camera moves, how the eye travels the frame "
    "over time, how motion lands a feeling. THE CORE PRINCIPLE: motion is emotional "
    "grammar. Every animation curve, every transition, every effect must be JUSTIFIED by "
    "the emotion of its beat — nothing is decorative, everything is communicative. You are "
    "writing a TECHNICAL document other AI agents (the scriptwriter, the composition "
    "engineer) will EXECUTE — not a creative brief for a human to interpret. Every choice "
    "is a concrete token from the closed vocabularies; every parameter is a number. Honor "
    "the vocabularies exactly — an off-vocabulary value is a lost instruction.")


def _intent_digest_for_mood_board(intent: dict) -> str:
    vl = intent.get("video_level") or {}
    arc = intent.get("emotional_arc") or {}
    lines = [f"CORE THESIS: {vl.get('core_thesis') or '(none)'}",
             f"EMOTIONAL JOURNEY: {vl.get('emotional_journey') or '(none)'}",
             f"TONE: {vl.get('tone_profile') or '(none)'}",
             "THE EMOTIONAL ARC (translate each phase into a motion beat):"]
    for p in ARC_PHASES:
        node = arc.get(p) or {}
        lines.append(f"  · {p}: emotion={node.get('dominant_emotion', '—')} "
                     f"intensity={node.get('intensity', '—')}/10 "
                     f"goal≈{node.get('duration_goal_sec', '—')}s")
    return "\n".join(lines)


def _mood_board_vocab_block() -> str:
    return (
        "GLOBAL_TEMPO (pick ONE for the whole video):\n  " + ", ".join(TEMPOS) + "\n"
        "GLOBAL_TEXTURE (pick ONE, or 'clean' for none):\n  "
        + ", ".join(MOOD_BOARD_TEXTURES) + "\n"
        "PACING_PROFILE (per beat — how time feels; maps to sentence shape + animation "
        "timing):\n  " + ", ".join(PACING_PROFILES) + "\n"
        "DOMINANT_EFFECT / SECONDARY_EFFECT (per beat — ONE dominant; secondary optional, "
        "must not compete; 'none' for none):\n  " + ", ".join(MOOD_BOARD_EFFECTS) + "\n"
        "TRANSITION_IN (per beat — how the beat begins; must feel motivated by the "
        "emotional shift):\n  " + ", ".join(MOOD_BOARD_TRANSITIONS) + "\n"
        "LAYOUT_FAMILY (per beat — the spatial grammar):\n  "
        + ", ".join(MOOD_BOARD_LAYOUTS) + "\n"
        "PRIMARY_EMOTION (per beat — from the shared emotion vocabulary):\n  "
        + ", ".join(EMOTIONS))


def _build_mood_board_prompt(intent: dict, thematic_anchor: dict,
                             style_guide: dict) -> str:
    anchor = thematic_anchor or {}
    thesis = str(anchor.get("thesis_statement", "")).strip()
    payload = str(anchor.get("emotional_payload", "")).strip()
    anchor_block = ""
    if thesis or payload:
        anchor_block = (f"=== THE THEMATIC ANCHOR (the motion must make this UNDENIABLE) ===\n"
                        f"THESIS: {thesis or '(none)'}\n"
                        f"EMOTIONAL PAYLOAD: {payload or '(none)'}\n\n")
    palette = (style_guide or {}).get("palette") or {}
    palette_note = (f"GLOBAL PALETTE (do NOT restate it — only justify a per-beat accent "
                    f"override if one is truly earned): primary {palette.get('primary', '—')}, "
                    f"bg {palette.get('bg', '—')}, signature {SIGNATURE_HIGHLIGHT}.\n\n"
                    if palette else "")
    return (
        f"{anchor_block}"
        f"=== THE NARRATIVE INTENT (the emotional blueprint — translate THIS into motion) ===\n"
        f"{_intent_digest_for_mood_board(intent)}\n\n"
        f"{palette_note}"
        f"=== THE CLOSED VOCABULARY (use ONLY these tokens) ===\n{_mood_board_vocab_block()}\n\n"
        "Design the MOTION MOOD BOARD — the visual architecture that governs BOTH the "
        "scriptwriter's pacing AND the composition engineer's animation. Map every choice "
        "to the emotion of its beat.\n\n"
        "1. video_level: a global_tempo, a global_texture (+ a one-sentence "
        "global_texture_justification tied to the thesis/payload), and a "
        "dominant_motion_philosophy (one sentence guiding every motion decision).\n"
        "2. beat_map: ONE entry per arc phase (hook → build → peak → breathe → cta), each "
        "with a beat_id, its arc_phase, a primary_emotion + intensity (1-10), a "
        "pacing_profile, ONE dominant_effect (+ optional non-competing secondary_effect), a "
        "transition_in, a layout_family, a scene_duration_target_sec (a NUMBER), optional "
        "motion_parameter_overrides keyed by effect name (concrete numbers, e.g. "
        "{\"push-in\":{\"duration_sec\":1.8,\"easing\":\"exponential-out\"}}), and a "
        "visual_mood_ref (a single film scene / photograph / painting that captures the "
        "feeling — NOT a palette).\n"
        "   RULES: the 'highlighter-FFD000' effect appears EXACTLY ONCE across the whole "
        "board — on the beat where the thesis lands hardest (usually the peak). 'breathe' "
        "pairs only with the breathe beat. 'count-up' pairs with a statistic. 'stutter-12fps' "
        "pairs with urgency/technological unease. 'push-in' pairs with realization.\n"
        "3. signature_beat_placement: the beat_id, the exact target_element (word/number) "
        "the #FFD000 highlighter touches, and why it earns the signature.\n\n"
        "Output ONLY this JSON object (no prose, no fences):\n"
        '{"video_level":{"global_tempo":"…","global_texture":"…",'
        '"global_texture_justification":"…","dominant_motion_philosophy":"…"},'
        '"beat_map":[{"beat_id":"b-hook","arc_phase":"hook","primary_emotion":"curiosity",'
        '"intensity":9,"pacing_profile":"rapid_staccato","dominant_effect":"stutter-12fps",'
        '"secondary_effect":"none","transition_in":"cut","layout_family":"centered-statement",'
        '"scene_duration_target_sec":8,"motion_parameter_overrides":{},"visual_mood_ref":"…"}],'
        '"signature_beat_placement":{"beat_id":"b-peak","target_element":"…","justification":"…"}}'
    )


def design_motion_mood_board(narrative_intent: dict, thematic_anchor: dict,
                             style_guide: dict, *, chat_fn=llm.chat) -> dict:
    """Turn a narrative_intent (+ the thematic anchor + the global palette) into a
    motion_mood_board dict (frozen shape, minus schema_version).

    The design-first inversion: the emotional blueprint becomes a concrete motion
    architecture that GOVERNS the script's pacing and the render's motion design. Validates
    the intent, makes ONE call to the strong creative model under a crafted "cinematographer"
    system prompt, then enforces every vocabulary/clamp/exactly-one-highlighter invariant in
    code. Envelope-free; Atlas stamps schema_version + validates at the boundary.
    """
    ok, reason = validate_intent_for_mood_board(narrative_intent)
    if not ok:
        raise ValueError(reason)
    llm_out = _chat_json(_MOOD_BOARD_SYSTEM,
                         _build_mood_board_prompt(narrative_intent, thematic_anchor or {},
                                                  style_guide or {}),
                         chat_fn=chat_fn)
    return assemble_motion_mood_board(narrative_intent, llm_out)


# ======================================================================
# Saving (standalone / chat convenience) + the full runs
# ======================================================================
def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "design").lower()).strip("-")
    return (s or "design")[:50]


def load_script(path: str | pathlib.Path) -> dict:
    """Resolve `path` to a script dict.

    `path` may be a script.json file, or a project directory holding one. Returns {}
    when nothing usable is there (caller reports a clean error).
    """
    p = pathlib.Path(path).expanduser()
    if p.is_dir():
        p = p / "script.json"
    return chat_state.load_json(p, {})


def load_style_guide(path: str | pathlib.Path) -> dict:
    """Resolve `path` to a style_guide dict (file or project dir). {} if absent."""
    p = pathlib.Path(path).expanduser()
    if p.is_dir():
        p = p / "style_guide.json"
    return chat_state.load_json(p, {})


def _save(obj: dict, kind: str, title: str, quiet: bool = True) -> pathlib.Path:
    """Write a stamped, independently-valid artifact under designs/ for inspection."""
    DESIGNS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = DESIGNS_DIR / f"{_slug(title)}-{kind}-{stamp}.json"
    chat_state.atomic_write_json(path, {"schema_version": SCHEMA_VERSION, **obj})
    if not quiet:
        print(f"\n💾 Saved {kind}:\n   {path}")
    return path


def run_style(script_or_path, *, chat_fn=llm.chat, quiet: bool = False
              ) -> tuple[dict, pathlib.Path]:
    """Full standalone style run: load (if a path), design, save, log. Raises ValueError."""
    def log(m):
        if not quiet:
            print(m)

    script = script_or_path if isinstance(script_or_path, dict) else load_script(script_or_path)
    ok, reason = validate_script(script)
    if not ok:
        raise ValueError(reason)

    log(f"\n🎨 Designing the style for: {script.get('working_title','(untitled)')}")
    style = design_style(script, chat_fn=chat_fn)
    log(f"  · {len(style['palette'].get('accents', []))} accents + the {SIGNATURE_HIGHLIGHT} "
        f"signature, {len(style['textures'])} textures, {style['fps']}fps, "
        f"budget {style['motion']['max_per_scene']}/scene")

    path = _save(style, "style_guide", script.get("working_title", "design"), quiet=quiet)
    _log_run(script, "style", {"fps": style["fps"], "textures": len(style["textures"])})
    return style, path


def run_storyboard(script_or_path, style_guide=None, *, chat_fn=llm.chat,
                   quiet: bool = False) -> tuple[dict, pathlib.Path]:
    """Full standalone storyboard run: load script (+ style), build, save, log.

    `style_guide` may be a dict, a path, or None (then we look beside the script, and
    fall back to Iris's defaults if there's still none). Raises ValueError on a bad
    script.
    """
    def log(m):
        if not quiet:
            print(m)

    if isinstance(script_or_path, dict):
        script, sg = script_or_path, style_guide
    else:
        script = load_script(script_or_path)
        if style_guide is None:
            sg = load_style_guide(script_or_path) or None
        elif isinstance(style_guide, dict):
            sg = style_guide
        else:
            sg = load_style_guide(style_guide) or None

    if not isinstance(sg, dict) or not sg:
        sg = None  # build_storyboard falls back to defaults
    ok, reason = validate_script(script)
    if not ok:
        raise ValueError(reason)

    log(f"\n🎬 Storyboarding: {script.get('working_title','(untitled)')}")
    board = build_storyboard(script, sg, chat_fn=chat_fn)
    sig = next((s["scene_no"] for s in board["scenes"] if s["signature_beat"]), None)
    log(f"  · {board['total_scenes']} scenes, signature beat on scene {sig}")

    path = _save(board, "storyboard", script.get("working_title", "design"), quiet=quiet)
    _log_run(script, "storyboard", {"scenes": board["total_scenes"], "signature_scene": sig})
    return board, path


def _log_run(script: dict, kind: str, extra: dict) -> None:
    mem = load_memory()
    mem["runs"].append({"working_title": script.get("working_title", ""),
                        "kind": kind, **extra,
                        "generated": time.strftime("%Y-%m-%d %H:%M:%S")})
    save_memory(mem)
