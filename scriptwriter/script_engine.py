"""Marlow's engine: brief -> through-line -> hook + one-point scenes + CTA.

One real decision is made by the brain (the arc: what the hook is, what order the
points go in, where the one scenic detour lands); the rest is disciplined method and
deterministic bookkeeping. The output is a structured `script.json` — the artifact
the Fact-Checker (Sage pass 2) then interrogates against the same brief.

THE CRUX — claim traceability (deterministic, never LLM-trusted): the brain never
writes a citation index. For each factual line it points at the brief fact the line
rests on, by a tag (`F0`, `F1`, …). This engine resolves that tag to a real source
INDEX into `research_brief.sources`, and **a claim whose tag resolves to nothing does
not ship** — the line is dropped, and a point/detour scene that loses all its claims
is dropped with it. The same resolution the Fact-Checker runs downstream is
re-asserted here, so what Marlow emits is guaranteed to resolve.

Decoupling boundary: this engine emits a plain dict and NEVER imports atlas. Atlas
stamps `schema_version` and validates against the frozen contract at the boundary.
`write_script(brief)` is the pure seam the adapter uses; `run(path)` is the CLI/chat
convenience that loads a brief, writes to scripts/, and logs the run.
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
# SOUL (who Marlow is) — never STYLE.md or examples/, which are chat-voice and would
# make the structured script chatty. SKILL.md stays the engine method.
SOUL = (HERE / "soul" / "SOUL.md").read_text()
SKILL = (HERE / "SKILL.md").read_text()
MEMORY = HERE / "memory.json"
SCRIPTS_DIR = HERE / "scripts"

# Default beat length when the brain omits an estimate (a calm narrator pace).
DEFAULT_SCENE_SEC = 7.0
WORDS_PER_SEC = 2.5  # rough read-aloud pace, used to sanity-fill a missing estimate

# How much brief material to surface to the brain (kept bounded for the prompt).
MAX_FACTS = 20
MAX_STATS = 10
MAX_QUOTES = 6

# Hook throat-clearing — openings that waste the first five seconds. The hook must
# not START with any of these (matched at the very front, case-insensitively).
_THROAT_CLEARING = (
    "in this video", "in this episode", "in today's video", "today we",
    "today i", "today, we", "welcome back", "welcome to", "hey guys",
    "hey everyone", "what's up", "whats up", "have you ever", "did you know",
    "let's talk about", "lets talk about", "by the end of this video",
    "in this one", "so today", "alright so", "okay so", "in this article",
)


# ----------------------------------------------------------------------
# Memory — a log of past script runs (provider-agnostic, on our disk)
# ----------------------------------------------------------------------
def load_memory():
    return chat_state.load_json(MEMORY, {"runs": []})


def save_memory(mem):
    chat_state.atomic_write_json(MEMORY, mem)


# ----------------------------------------------------------------------
# Brief validation — don't spend an API call on something we can't build on
# ----------------------------------------------------------------------
def validate_brief(brief) -> tuple[bool, str]:
    """Return (ok, reason). A brief is usable only if it carries facts to assert
    AND sources to ground them against."""
    if not isinstance(brief, dict):
        return False, "That's not a research brief — I need the brief JSON object."
    facts = brief.get("verified_facts") or []
    sources = brief.get("sources") or []
    if not facts:
        return False, ("This brief has no verified facts — there's nothing I can "
                       "assert. Send it back to research before I write.")
    if not sources:
        return False, ("This brief has no sources — I can't ground a single claim, "
                       "so I won't write a script that can't be fact-checked.")
    return True, ""


# ----------------------------------------------------------------------
# Robust JSON parsing from an LLM reply (models add prose / fences)
# ----------------------------------------------------------------------
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


# ----------------------------------------------------------------------
# Source resolution — IDENTICAL semantics to the Fact-Checker's resolver, so what
# Marlow emits is exactly what Sage's pass-2 will resolve. (Re-implemented here, not
# imported, to keep this engine self-contained and atlas/sibling-free.)
# ----------------------------------------------------------------------
def resolve_source_ref(source_ref, sources: list) -> tuple[bool, dict | None]:
    """Resolve `source_ref` to a brief source. Returns (resolved, source_or_None).

    Accepts a 0-based integer index, a digit-string index, or a URL string. None /
    empty is "no citation" (not a resolution). Mirrors topic-researcher/factcheck.py.
    """
    sources = sources or []
    if source_ref is None or (isinstance(source_ref, str) and not source_ref.strip()):
        return False, None
    idx = None
    if isinstance(source_ref, bool):       # guard: bool is an int subclass
        idx = None
    elif isinstance(source_ref, int):
        idx = source_ref
    elif isinstance(source_ref, str) and source_ref.strip().lstrip("-").isdigit():
        idx = int(source_ref.strip())
    if idx is not None:
        if 0 <= idx < len(sources):
            return True, sources[idx]
        return False, None
    ref = str(source_ref).strip()
    for s in sources:
        if isinstance(s, dict) and s.get("url") == ref:
            return True, s
    return False, None


def _source_url_index(brief: dict) -> dict[str, int]:
    """Map each brief source URL -> its 0-based index in brief.sources."""
    out: dict[str, int] = {}
    for i, s in enumerate(brief.get("sources") or []):
        url = s.get("url") if isinstance(s, dict) else None
        if url and url not in out:
            out[url] = i
    return out


def resolve_support(support, verified_facts: list, url_to_idx: dict[str, int]):
    """Turn the brain's `support` tag into a concrete source_ref (int) or None.

    A tag is `F<index>` / `f<index>` / a bare integer pointing at verified_facts.
    The fact's first source URL that exists in the brief's top-level sources wins,
    and the returned ref is THAT source's index. A URL passed directly is honored as
    a fallback. None means "couldn't ground it" -> the claim must not ship.
    """
    if support is None:
        return None
    s = str(support).strip()
    if not s:
        return None
    m = re.match(r"^[Ff]?(\d+)$", s)
    if m:
        fi = int(m.group(1))
        if 0 <= fi < len(verified_facts):
            fact = verified_facts[fi]
            for url in (fact.get("sources") or []):
                if url in url_to_idx:
                    return url_to_idx[url]
        return None
    # Direct URL fallback (the brain disobeyed and gave a URL): honor it iff real.
    return url_to_idx.get(s)


# ----------------------------------------------------------------------
# Render the brief for the brain — facts TAGGED so the brain can point at them
# ----------------------------------------------------------------------
def _facts_block(brief: dict) -> str:
    facts = (brief.get("verified_facts") or [])[:MAX_FACTS]
    lines = []
    for i, f in enumerate(facts):
        srcs = ", ".join(f.get("sources", []) or []) or "(no source!)"
        conf = f.get("confidence", "?")
        lines.append(f"[F{i}] ({conf}) {f.get('claim','')}\n        sources: {srcs}")
    return "\n".join(lines) or "(no verified facts)"


def _supporting_block(brief: dict) -> str:
    out = []
    stats = (brief.get("key_statistics") or [])[:MAX_STATS]
    if stats:
        out.append("KEY STATISTICS (assert via the verified fact they belong to):")
        for s in stats:
            out.append(f"  - {s.get('stat','')}: {s.get('value','')} "
                       f"({s.get('date','n.d.')}) — {s.get('source','')}")
    myths = (brief.get("myths_and_corrections") or [])
    if myths:
        out.append("\nMYTHS -> CORRECTIONS (great for the hook; assert the CORRECTION, "
                   "never the myth):")
        for m in myths:
            out.append(f"  - MYTH: {m.get('myth','')}  ->  TRUTH: {m.get('correction','')}")
    contested = (brief.get("contested_or_uncertain") or [])
    if contested:
        out.append("\nCONTESTED (use ONLY if you soften/attribute it — never as a hard "
                   "claim, never tag it):")
        for c in contested:
            out.append(f"  - {c.get('claim','')}  (why: {c.get('why','')})")
    quotes = (brief.get("notable_quotes") or [])[:MAX_QUOTES]
    if quotes:
        out.append("\nNOTABLE QUOTES (short, attributed):")
        for q in quotes:
            out.append(f"  - \"{q.get('quote','')}\" — {q.get('who','')}")
    return "\n".join(out)


def _build_prompt(brief: dict) -> str:
    angle = brief.get("angle") or ""
    audience = brief.get("target_audience") or "a curious general audience"
    overview = brief.get("overview") or ""
    title = brief.get("working_title") or ""
    angle_note = f"\nThe angle to take: {angle}" if angle else ""
    title_note = f"\nA working title to consider (improve it if you can): {title}" if title else ""
    return (
        f"=== METHOD ===\n{SKILL}\n\n"
        f"=== THE RESEARCH BRIEF (your raw material AND your fence — assert nothing "
        f"it doesn't contain) ===\n"
        f"TOPIC: {brief.get('topic','')}\n"
        f"AUDIENCE: {audience}{angle_note}{title_note}\n\n"
        f"OVERVIEW:\n{overview}\n\n"
        f"VERIFIED FACTS — the ONLY facts you may assert. Tag each claim to one of "
        f"these by its [F#]:\n{_facts_block(brief)}\n\n"
        f"{_supporting_block(brief)}\n\n"
        "Apply the METHOD. Find the through-line, open on a hook that earns the first "
        "five seconds (no throat-clearing), order the verified facts into one-point "
        "scenes, earn ONE vivid sourced detour, and close on a clean CTA (never 'in "
        "conclusion'). For EVERY factual line, emit a claim whose `support` is the "
        "[F#] tag of the verified fact it rests on — do NOT write a URL or an index. "
        "A line you can't tag to a verified fact must be cut. Hook and CTA scenes "
        "usually assert no fact and carry \"claims\": []. Return ONLY the JSON object "
        "from 'Your output contract'."
    )


# ----------------------------------------------------------------------
# Hook discipline — a pure, testable heuristic
# ----------------------------------------------------------------------
def hook_opens_with_throat_clearing(hook: str) -> bool:
    """True if the hook STARTS with a throat-clearing opener (wastes the first 5s)."""
    h = (hook or "").strip().lower()
    # normalize leading punctuation/quotes
    h = h.lstrip("\"'“”‘’ \t-—")
    return any(h.startswith(p) for p in _THROAT_CLEARING)


# ----------------------------------------------------------------------
# Assemble: resolve every claim's support, drop what can't ground, number it all
# ----------------------------------------------------------------------
def _coerce_duration(scene: dict) -> float:
    d = scene.get("duration_est_sec")
    try:
        d = float(d)
        if d > 0:
            return round(d, 1)
    except (TypeError, ValueError):
        pass
    # Fall back to a pace estimate from the narration length.
    words = len((scene.get("narration") or "").split())
    return round(max(DEFAULT_SCENE_SEC, words / WORDS_PER_SEC), 1) if words else DEFAULT_SCENE_SEC


def assemble_script(brief: dict, llm_out: dict) -> dict:
    """Turn the brain's arc into the frozen script shape, enforcing traceability.

    Every claim's `support` tag is resolved to a real source_ref; a claim that can't
    be grounded is dropped, and a point/detour scene that loses all its claims is
    dropped too. scene_no and claim_id are assigned deterministically. Raises
    ValueError if nothing groundable survives (an honest engine failure).
    """
    facts = brief.get("verified_facts") or []
    url_to_idx = _source_url_index(brief)

    assembled: list[dict] = []
    for raw in (llm_out.get("scenes") or []):
        if not isinstance(raw, dict):
            continue
        beat = (raw.get("beat") or "point").strip().lower()
        # Resolve this scene's claims; keep only the ones that ground.
        kept_claims = []
        for c in (raw.get("claims") or []):
            if not isinstance(c, dict):
                continue
            text = (c.get("text") or "").strip()
            if not text:
                continue
            ref = resolve_support(c.get("support"), facts, url_to_idx)
            if ref is None:
                continue  # can't tag it to a brief source -> it does not ship
            kept_claims.append({"text": text, "source_ref": ref})

        # A point/detour scene whose only claims were dropped is asserting an
        # ungrounded fact in its narration — drop the whole scene. Hook/CTA/transition
        # scenes are allowed to carry no claims.
        had_claims = bool(raw.get("claims"))
        if beat in ("point", "detour") and had_claims and not kept_claims:
            continue

        assembled.append({
            "beat": beat,
            "point": (raw.get("point") or "").strip(),
            "narration": (raw.get("narration") or "").strip(),
            "on_screen_text": (raw.get("on_screen_text") or "").strip(),
            "visual_note": (raw.get("visual_note") or "").strip(),
            "duration_est_sec": _coerce_duration(raw),
            "_claims": kept_claims,
        })

    if not assembled:
        raise ValueError(
            "Couldn't ground a single scene against the brief — every claim's source "
            "failed to resolve. The brief and the draft don't line up; nothing ships.")

    # Number scenes + claims now that the set is final and stable.
    scenes = []
    for n, s in enumerate(assembled, start=1):
        claims = [{"claim_id": f"s{n}c{j}", "text": c["text"], "source_ref": c["source_ref"]}
                  for j, c in enumerate(s.pop("_claims"), start=1)]
        scene = {"scene_no": n, **s, "claims": claims}
        scenes.append(scene)

    return {
        "working_title": (llm_out.get("working_title") or brief.get("working_title")
                          or f"The truth about {brief.get('topic','this')}").strip(),
        "hook": (llm_out.get("hook") or (scenes[0]["narration"] if scenes else "")).strip(),
        "cta": (llm_out.get("cta") or "").strip(),
        "total_scenes": len(scenes),
        "est_runtime_sec": round(sum(s["duration_est_sec"] for s in scenes), 1),
        "scenes": scenes,
    }


def assert_traceable(script: dict, brief: dict) -> None:
    """Final guard: every shipped claim's source_ref resolves to a brief source.

    Re-runs the Fact-Checker's own resolver over what we produced. A failure here is
    a bug in the engine, not in the script — so it raises loudly rather than shipping.
    """
    sources = brief.get("sources") or []
    for scene in script.get("scenes", []):
        for c in scene.get("claims", []):
            ok, _ = resolve_source_ref(c.get("source_ref"), sources)
            if not ok:
                raise AssertionError(
                    f"traceability guard failed: claim {c.get('claim_id')} "
                    f"(scene {scene.get('scene_no')}) has source_ref "
                    f"{c.get('source_ref')!r} that doesn't resolve.")


# ----------------------------------------------------------------------
# write_script — the pure seam the adapter uses (no file I/O, no schema envelope)
# ----------------------------------------------------------------------
def write_script(brief: dict, *, chat_fn=llm.chat) -> dict:
    """Turn a research brief into a script dict (frozen shape, minus schema_version).

    Validates the brief, makes ONE arc call to the brain (with a single retry if the
    hook throat-clears), resolves every claim's support deterministically, and
    asserts traceability before returning. Atlas stamps schema_version + validates.
    """
    ok, reason = validate_brief(brief)
    if not ok:
        raise ValueError(reason)

    prompt = _build_prompt(brief)
    llm_out = _chat_json(SOUL, prompt, chat_fn=chat_fn)
    script = assemble_script(brief, llm_out)

    # One bounded retry purely to fix a throat-clearing hook (soft quality gate).
    if hook_opens_with_throat_clearing(script.get("hook", "")):
        retry = prompt + (
            "\n\nThe hook you wrote opens with throat-clearing — rewrite it to open "
            "on the single sharpest true thing, with zero preamble. Keep everything "
            "else as strong.")
        try:
            llm_out2 = _chat_json(SOUL, retry, chat_fn=chat_fn)
            script2 = assemble_script(brief, llm_out2)
            if not hook_opens_with_throat_clearing(script2.get("hook", "")):
                script = script2
        except Exception:
            pass  # keep the first draft; a soft heuristic never fails the whole job

    assert_traceable(script, brief)
    return script


# ----------------------------------------------------------------------
# Saving (standalone / chat convenience) + the full run
# ----------------------------------------------------------------------
def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "script").lower()).strip("-")
    return (s or "script")[:50]


def save_script(script: dict, quiet: bool = True) -> pathlib.Path:
    """Write the script as JSON under scripts/, keyed by title + timestamp."""
    SCRIPTS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = SCRIPTS_DIR / f"{_slug(script.get('working_title',''))}-{stamp}.json"
    chat_state.atomic_write_json(path, script)
    if not quiet:
        print(f"\n💾 Saved script:\n   {path}")
    return path


def load_brief(path: str | pathlib.Path) -> dict:
    """Resolve `path` to a brief dict.

    `path` may be a research_brief.json file, or a project directory holding one.
    Returns {} when nothing usable is there (caller reports a clean error).
    """
    p = pathlib.Path(path).expanduser()
    if p.is_dir():
        p = p / "research_brief.json"
    return chat_state.load_json(p, {})


def run(brief_or_path, *, chat_fn=llm.chat, quiet: bool = False) -> tuple[dict, pathlib.Path]:
    """Full standalone run: load (if a path), write the script, save it, log the run.

    `brief_or_path` may be a brief dict or a path to a brief / project dir. Returns
    (script, json_path). Raises ValueError with a plain message on an unusable brief.
    """
    def log(m):
        if not quiet:
            print(m)

    if isinstance(brief_or_path, dict):
        brief = brief_or_path
    else:
        brief = load_brief(brief_or_path)

    ok, reason = validate_brief(brief)
    if not ok:
        raise ValueError(reason)

    log(f"\n📝 Writing the script for: {brief.get('topic','(untitled)')}")
    script = write_script(brief, chat_fn=chat_fn)
    log(f"  · {script['total_scenes']} scenes, ~{script['est_runtime_sec']}s, "
        f"{sum(len(s['claims']) for s in script['scenes'])} tagged claims")

    json_path = save_script(script, quiet=quiet)

    mem = load_memory()
    mem["runs"].append({"topic": brief.get("topic", ""),
                        "working_title": script.get("working_title", ""),
                        "scenes": script["total_scenes"],
                        "generated": time.strftime("%Y-%m-%d %H:%M:%S")})
    save_memory(mem)
    return script, json_path
