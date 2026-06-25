"""Magpie's conceptual-diagram PLANNER — a kind:diagram shot -> a DiagramPlan.

The PLAN half of the diagram pipeline (diagram-generator spec §3.5/D16): off the render
critical path, an LLM turns a plain-English conceptual shot into a tiny closed-vocab
DiagramPlan (components + labels + a coarse layout hint — NEVER coordinates or SVG).
Mason (composition-engineer/diagram_render.py) composes that plan into animated flat SVG
at render time. This module is pure + injectable (the LLM lives behind a `chat_fn` seam),
so it is fully unit-testable offline with a canned chat_fn.

The closed vocabularies below are the CONTRACT with Mason's renderer; they are duplicated
here (the engines stay decoupled — each has its own llm.py too) and a cross-engine parity
test asserts they never drift from diagram_render.py. An unknown token is a hard error
(mirrors every other closed vocabulary in the pipeline): the plan is re-prompted once, then
the caller falls back to stock sourcing.

Config (spec §8): the plan call wants Haiku-class speed, max_tokens≈2000 (a truncation
guard, not a target), NO extended thinking, a ~20s wall-clock timeout + one retry. Those
live in the `chat_fn` implementation (swappable per call); this module owns the prompt,
the JSON+vocab validation, and the retry-on-invalid loop.
"""
from __future__ import annotations

import json
import re

import llm

# ---- closed vocabularies (CONTRACT with composition-engineer/diagram_render.py) ----
LAYOUT_HINTS = ("left-to-right", "stacked", "grid", "radial", "freeform")
COMPONENTS = (
    "node", "labeled-box", "container", "speech-bubble", "thought-bubble",
    "layer-stack", "before-after", "cycle", "grid", "glyph",
)
GLYPHS = (
    "person", "gear", "document", "robot-arm", "button", "brain",
    "cloud", "database", "lock",
)
EMPHASIS = ("underline", "box", "circle", "highlight", "strike", "cross-off", "bracket")
ANIM = ("draw-on", "pop-in", "count-up", "cross-fade")

MAX_COMPONENTS = 7          # a clean explainer diagram stays legible; cap busy plans


# ======================================================================
# Prompt
# ======================================================================
_SYSTEM = (
    "You are a diagram planner for an explainer-video studio. You turn a short, plain-"
    "English description of a CONCEPTUAL visual into a tiny structured plan that a "
    "deterministic renderer will draw. You NEVER draw SVG, never give coordinates, never "
    "choose colors — only the logical structure: which labeled components exist, which "
    "connect to which, and a coarse layout hint. Keep it minimal and legible."
)


def _vocab_block() -> str:
    return (
        f"layout_hint (pick ONE): {', '.join(LAYOUT_HINTS)}\n"
        f"component type (closed set): {', '.join(COMPONENTS)}\n"
        f"of/glyph (closed icon set, optional): {', '.join(GLYPHS)}\n"
        f"emphasis (optional, one): {', '.join(EMPHASIS)}\n"
        f"anim (optional, one): {', '.join(ANIM)}"
    )


def _build_prompt(content: str, extra: str = "") -> str:
    return (
        f"Describe this conceptual shot as a DiagramPlan.\n\nSHOT: {content!r}\n\n"
        f"=== CLOSED VOCABULARY (use ONLY these tokens) ===\n{_vocab_block()}\n\n"
        "Rules:\n"
        f"- At most {MAX_COMPONENTS} components; fewer is better. Each needs a unique short "
        "`id` and a SHORT `label` (1-2 words).\n"
        "- Use `of` to give a component a glyph (icon); use `to` (a list of component ids) "
        "for arrows/flow between components.\n"
        "- Choose the layout_hint that matches the idea: a process/pipeline -> "
        "'left-to-right'; a hierarchy/layers -> 'stacked'; a feedback loop -> 'radial'; "
        "a set/matrix -> 'grid'.\n"
        "- No coordinates, no SVG, no styling, no prose.\n\n"
        'Output ONLY this JSON shape:\n'
        '{"layout_hint":"left-to-right","components":['
        '{"id":"a","type":"speech-bubble","label":"AI","of":"brain","to":["b"]},'
        '{"id":"b","type":"glyph","label":"Action","of":"button","emphasis":"circle"}]}'
        + (f"\n\n{extra}" if extra else "")
    )


# ======================================================================
# Validation (closed-vocab; mirrors diagram_render.validate_plan)
# ======================================================================
def validate_plan(plan: dict) -> list:
    """Return a list of contract violations (empty == valid)."""
    errs: list[str] = []
    if not isinstance(plan, dict):
        return ["plan is not an object"]
    lh = plan.get("layout_hint")
    if lh is not None and lh not in LAYOUT_HINTS:
        errs.append(f"unknown layout_hint {lh!r}")
    comps = plan.get("components")
    if not isinstance(comps, list) or not comps:
        return errs + ["plan has no components"]
    ids = {c.get("id") for c in comps if isinstance(c, dict)}
    for i, c in enumerate(comps):
        if not isinstance(c, dict):
            errs.append(f"component {i} is not an object")
            continue
        if c.get("type") not in COMPONENTS:
            errs.append(f"component {i}: unknown type {c.get('type')!r}")
        g = c.get("of") or (c.get("glyph") if c.get("type") == "glyph" else None)
        if g is not None and g not in GLYPHS:
            errs.append(f"component {i}: unknown glyph {g!r}")
        if c.get("emphasis") is not None and c.get("emphasis") not in EMPHASIS:
            errs.append(f"component {i}: unknown emphasis {c.get('emphasis')!r}")
        if c.get("anim") is not None and c.get("anim") not in ANIM:
            errs.append(f"component {i}: unknown anim {c.get('anim')!r}")
        for tgt in (c.get("to") or []):
            if tgt not in ids:
                errs.append(f"component {i}: edge to unknown id {tgt!r}")
    return errs


def _strip_json(reply: str) -> str:
    """Pull the first {...} object out of a model reply (tolerate fences/prose)."""
    s = (reply or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", s).strip()
    a, b = s.find("{"), s.rfind("}")
    return s[a:b + 1] if a != -1 and b != -1 and b > a else s


def _normalize(plan: dict) -> dict:
    """Keep only contract fields, dedupe/repair ids, cap component count. The plan is the
    cached contract object stored in the manifest — keep it tiny and clean."""
    comps = []
    seen = set()
    for i, c in enumerate(plan.get("components", [])[:MAX_COMPONENTS]):
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or f"c{i}")
        while cid in seen:
            cid += "_"
        seen.add(cid)
        row = {"id": cid, "type": c.get("type"), "label": str(c.get("label", ""))[:40]}
        if c.get("of"):
            row["of"] = c["of"]
        if c.get("emphasis"):
            row["emphasis"] = c["emphasis"]
        if c.get("anim"):
            row["anim"] = c["anim"]
        if isinstance(c.get("to"), list) and c["to"]:
            row["to"] = [str(t) for t in c["to"]]
        comps.append(row)
    # drop edges to ids that survived normalization only
    live = {c["id"] for c in comps}
    for c in comps:
        if "to" in c:
            c["to"] = [t for t in c["to"] if t in live]
            if not c["to"]:
                del c["to"]
    out = {"components": comps}
    lh = plan.get("layout_hint")
    out["layout_hint"] = lh if lh in LAYOUT_HINTS else "left-to-right"
    return out


def plan_diagram(shot: dict, *, chat_fn=llm.chat) -> dict:
    """Plan a conceptual diagram for `shot` -> a validated, normalized DiagramPlan dict.

    Calls the LLM (via `chat_fn`) for a closed-vocab plan, retrying once with the failure
    reason fed back if the first reply is non-JSON or violates the contract. Raises
    ValueError if it can't get a valid plan after the retry — the caller (Magpie) then
    walks the fallback chain (stock image -> placeholder). Pure given `chat_fn`.
    """
    content = str(shot.get("content", "")).strip()
    if not content:
        raise ValueError("diagram shot has no content to plan from")

    last_reason = ""
    for attempt in range(2):
        extra = ("" if attempt == 0 else
                 "REMINDER: Output ONLY raw JSON starting with '{'. Use ONLY the closed "
                 f"vocabulary. Previous attempt failed: {last_reason}")
        reply = chat_fn(_SYSTEM, _build_prompt(content, extra))
        try:
            raw = json.loads(_strip_json(reply))
        except Exception:
            last_reason = "reply was not valid JSON"
            continue
        plan = _normalize(raw if isinstance(raw, dict) else {})
        errs = validate_plan(plan)
        if not errs:
            return plan
        last_reason = "; ".join(errs)[:200]
    raise ValueError(f"could not produce a valid DiagramPlan: {last_reason}")
