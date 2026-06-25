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


def _build_treatment_prompt(brief: dict) -> str:
    return (
        f"=== CREATIVE CRAFT (your method for this job) ===\n{CRAFT}\n\n"
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
