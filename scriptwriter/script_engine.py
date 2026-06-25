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
# STYLE is Marlow's VOICE — read ONLY for the Creative Roundtable's Craftsman (the
# rewrite happens in Marlow's voice). The first-pass arc still uses SOUL only, so the
# structured draft stays disciplined; STYLE never enters the draft prompt.
STYLE = (HERE / "soul" / "STYLE.md").read_text()
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


def resolve_support(support, verified_facts: list, url_to_idx: dict[str, int],
                    key_statistics: list | None = None):
    """Turn the brain's `support` tag into a concrete source_ref (int) or None.

    Two tag families, both resolving to a 0-based index into the brief's top-level
    `sources[]` (what the contract — and the Fact-Checker — expect):
    - `F<index>` / `f<index>` / a bare integer -> a `verified_facts` entry. Its first
      source URL that exists in the brief's sources wins; the ref is THAT source's index.
    - `S<index>` / `s<index>` -> a `key_statistics` entry. The STAT'S OWN `source` URL
      (the evidence carrying that figure) is resolved to its index — so a statistic is
      cited to the source that actually supports it, not to a borrowed fact's source.

    A URL passed directly is honored as a fallback. None means "couldn't ground it" ->
    the claim must not ship.
    """
    if support is None:
        return None
    s = str(support).strip()
    if not s:
        return None
    # S<index> -> a key_statistics entry, cited to its OWN source.
    sm = re.match(r"^[Ss](\d+)$", s)
    if sm:
        stats = key_statistics or []
        si = int(sm.group(1))
        if 0 <= si < len(stats):
            url = (stats[si] or {}).get("source")
            if url and url in url_to_idx:
                return url_to_idx[url]
        return None
    # F<index> (or a bare integer) -> a verified_facts entry.
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
# Numeric-citation correctness — a deterministic safety net so a statistic is
# cited to the source that actually carries its figure (not a borrowed fact's
# source). This catches the live failure class — a stat asserted with a constant /
# wrong source_ref — in the engine, not only downstream at the fact-check gate.
# ----------------------------------------------------------------------
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _norm_num(tok: str) -> str:
    """Canonicalize a numeric token: drop leading zeros + trailing fractional zeros.

    "076" -> "76"; "90.20" -> "90.2"; "2000000" -> "2000000" (NEVER scientific
    notation, which would collide distinct large figures). Keeps exact magnitude.
    """
    if "." in tok:
        ip, fp = tok.split(".", 1)
        fp = fp.rstrip("0")
        ip = str(int(ip)) if ip else "0"
        return f"{ip}.{fp}" if fp else ip
    return str(int(tok))


def _figures(text: str) -> set[str]:
    """Normalized numeric tokens in `text` (commas stripped, zeros canonicalized).

    "$1,200" -> {"1200"}; "76.8%" -> {"76.8"}; "2,000,000" -> {"2000000"}. Used to
    match a claim's figures against the brief's key_statistics values.

    Digits GLUED to letters are model/version identifiers, not statistics, and are
    excluded: "GPT-4o" / "GPT-5.x" / "V3" carry no figure to match — so a qualitative
    claim about model versions isn't misread as asserting a key-statistic number
    (which would route it to the numeric path and starve the qualitative repair).
    """
    s = (text or "").replace(",", "")
    out: set[str] = set()
    for m in _NUM_RE.finditer(s):
        a, b = m.start(), m.end()
        if a > 0 and s[a - 1].isalpha():            # "o4", "gpt4" — identity, not figure
            continue
        if b < len(s) and s[b].isalpha():           # "4o" — identity, not figure
            continue
        if b + 1 < len(s) and s[b] == "." and s[b + 1].isalpha():  # "5.x" — version
            continue
        try:
            out.add(_norm_num(m.group(0)))
        except ValueError:
            continue
    return out


# Generic connector / measurement words that carry no model/benchmark identity. Kept
# out of label matching so two stats can't "agree" just by sharing "score"/"on"/"the".
_LABEL_STOPWORDS = frozenset({
    "the", "a", "an", "of", "on", "in", "to", "and", "or", "is", "was", "were", "at",
    "by", "for", "with", "it", "its", "that", "this", "as", "than", "from", "per",
    "about", "around", "near", "over", "under", "up", "out", "one", "two", "scored",
    "score", "scores", "scoring", "hit", "hits", "reached", "reaches", "reportedly",
    "roughly", "approximately", "approx", "circa", "value", "values", "number",
})
_TOKEN_RE = re.compile(r"[a-z0-9.]+")


def _label_tokens(text: str) -> set[str]:
    """Distinctive model/benchmark/identity tokens in `text` (lowercased).

    "Claude Sonnet 4.6 — SWE-bench Verified" -> {claude, sonnet, 4.6, swe, bench,
    verified}. Generic connectors and measurement verbs are dropped so a label match
    means a real model/benchmark agreement, not a shared "score on the".
    """
    out: set[str] = set()
    for t in _TOKEN_RE.findall((text or "").lower()):
        t = t.strip(".")
        if len(t) >= 2 and t not in _LABEL_STOPWORDS:
            out.add(t)
    return out


# Content-word matching — the qualitative analog of label matching. Where labels
# isolate model/benchmark identity for a NUMBER's citation, content tokens capture a
# qualitative CLAIM's subject so it can be matched to the verified_fact it actually
# rests on. The stopword set is _LABEL_STOPWORDS widened with common prose
# connectors/auxiliaries, so overlap means a real topical agreement (gpt/legacy/
# superseded) and not a shared "now/more/which/has".
_CONTENT_STOPWORDS = _LABEL_STOPWORDS | frozenset({
    "now", "new", "newer", "newest", "old", "older", "more", "most", "less", "least",
    "very", "much", "many", "few", "some", "all", "any", "no", "not", "but", "so",
    "then", "also", "just", "only", "like", "such", "into", "onto", "between",
    "across", "their", "them", "they", "these", "those", "what", "which", "where",
    "when", "why", "how", "who", "whom", "whose", "has", "have", "had", "having",
    "are", "be", "been", "being", "am", "will", "would", "shall", "can", "could",
    "should", "may", "might", "must", "do", "does", "did", "doing", "done", "you",
    "your", "yours", "we", "us", "our", "ours", "i", "me", "my", "he", "she", "his",
    "her", "him", "there", "here", "still", "yet", "every", "each", "both", "either",
    "neither", "while", "because", "since", "though", "although", "however", "thus",
    "hence", "now", "today", "currently", "become", "becomes", "became", "make",
    "makes", "made", "get", "gets", "got", "use", "uses", "used", "using", "lot",
    "lots", "thing", "things", "real", "really", "actual", "actually", "now",
})


def _content_tokens(text: str) -> set[str]:
    """Topical content tokens in `text` (lowercased), prose connectors dropped.

    "GPT-4o is now a legacy model, superseded by GPT-5.x" -> {gpt, 4o, legacy, model,
    superseded, 5.x}. Used to measure how strongly a qualitative claim overlaps a
    verified_fact's text, so a claim can be re-pointed to the fact it really rests on.
    """
    out: set[str] = set()
    for t in _TOKEN_RE.findall((text or "").lower()):
        t = t.strip(".")
        if len(t) >= 2 and t not in _CONTENT_STOPWORDS:
            out.add(t)
    return out


def _is_stat_tag(support) -> bool:
    """True if the brain's `support` tag is an S# (key_statistics) tag.

    Qualitative citation repair applies only to verified_fact (F#) claims; a number's
    citation is the numeric reconciler's job, so an S#-tagged claim is left alone here.
    """
    return bool(re.match(r"^\s*[Ss]\d+\s*$", str(support or "")))


def _stat_records(brief: dict, url_to_idx: dict[str, int]) -> list[dict]:
    """Citable key_statistics as match records: {idx, figs, label}.

    Only stats whose `source` resolves to a real brief source are included (others
    can't be cited). `figs` = the value's normalized figures; `label` = the identity
    tokens from the stat's descriptor (its model/benchmark), used to disambiguate two
    stats that happen to share a figure (the 72.7% collision).
    """
    recs: list[dict] = []
    for stat in (brief.get("key_statistics") or []):
        if not isinstance(stat, dict):
            continue
        idx = url_to_idx.get(stat.get("source"))
        if idx is None:
            continue
        figs = _figures(str(stat.get("value", "")))
        if not figs:
            continue
        recs.append({"idx": idx, "figs": figs,
                     "label": _label_tokens(str(stat.get("stat", "")))})
    return recs


def _correct_stat_sources(text: str, recs: list[dict]) -> tuple[set[int], bool]:
    """Resolve which source indices may back a claim's STATISTIC figure.

    Returns (correct_idxs, figure_seen). `figure_seen` is True when the claim asserts a
    figure that is some citable key_statistic's value (i.e. it IS a key-stat number).
    Among the stats sharing that figure, the ones whose identity tokens best overlap the
    claim win — so a 72.7% claim about Sonnet/SWE-bench resolves to that entry, not the
    Opus/OSWorld entry that shares the number. If a figure matches but NO entry shares
    any identity token (a number-only match), `correct` is empty -> the citation can't
    be trusted (caller flags/drops it rather than silently number-matching).
    """
    figs = _figures(text)
    cands = [r for r in recs if r["figs"] & figs]
    if not cands:
        return set(), False
    ctoks = _label_tokens(text)
    scored = [(len(r["label"] & ctoks), r["idx"]) for r in cands]
    best = max(score for score, _ in scored)
    if best == 0:
        return set(), True  # figure matches a stat value but no model/benchmark agrees
    return {idx for score, idx in scored if score == best}, True


def _reconcile_numeric_ref(text: str, ref: int, recs: list[dict]) -> int | None:
    """Resolve a numeric claim's source_ref by figure AND label. None == DROP it.

    - figure isn't a key-stat value (e.g. it lives inside a verified_fact) -> ref kept.
    - figure matches stat(s) and a label agrees -> the label-correct source (repairing
      a borrowed/colliding ref).
    - figure matches a stat value but NO entry agrees on the model/benchmark -> None: a
      number cited to the wrong thing must not ship (also surfaced by the guard).
    """
    correct, figure_seen = _correct_stat_sources(text, recs)
    if not figure_seen:
        return ref
    if not correct:
        return None
    return ref if ref in correct else min(correct)


def find_numeric_citation_problems(script: dict, brief: dict) -> list[dict]:
    """Deterministic guard: numeric claims cited to a source that doesn't carry them.

    For every claim asserting a figure that is a key_statistics value, the claim's
    `source_ref` must resolve to the entry that matches on BOTH figure AND
    model/benchmark label. A bare-number match to the wrong entry — or a number that
    matches a stat value but no entry's label — is a problem (empty `expected_source_idx`
    marks the number-only case). Pure + offline: the engine self-checks before the gate.
    """
    sources = brief.get("sources") or []
    url_to_idx = _source_url_index(brief)
    recs = _stat_records(brief, url_to_idx)
    problems: list[dict] = []
    for scene in script.get("scenes", []):
        for c in scene.get("claims", []):
            correct, figure_seen = _correct_stat_sources(c.get("text", ""), recs)
            if not figure_seen:
                continue  # no key-statistic figure in this claim -> nothing to check
            resolved, src = resolve_source_ref(c.get("source_ref"), sources)
            idx = sources.index(src) if (resolved and src in sources) else None
            if (not correct) or (idx not in correct):
                problems.append({
                    "claim_id": c.get("claim_id"),
                    "scene_no": scene.get("scene_no"),
                    "source_ref": c.get("source_ref"),
                    "expected_source_idx": sorted(correct),
                    "text": c.get("text", ""),
                })
    return problems


# ----------------------------------------------------------------------
# Qualitative-citation correctness — the qualitative analog of the numeric
# reconciler. A TRUE claim must not hard-block at the gate just because the brain
# mis-tagged it to the wrong fact's source. For each verified_fact claim we measure
# content-token overlap between the claim text and every verified_fact; on a clearly
# better, unique match the source_ref is repaired to that fact's source. Ambiguous or
# weak matches are left alone and FLAGGED (never silently re-pointed) — the
# conservative dual of the numeric guard's "drop a number cited to the wrong thing".
# ----------------------------------------------------------------------
MIN_QUALITATIVE_OVERLAP = 2  # shared content tokens needed to trust a fact match


def _fact_records(brief: dict, url_to_idx: dict[str, int]) -> list[dict]:
    """Citable verified_facts as match records: {idx, src_idxs, first_src, content}.

    Only facts whose `sources` resolve to a real brief source are included (others
    can't back anything). `first_src` mirrors resolve_support's "first resolvable
    source URL wins" so a repair lands on the same index an honest F# tag would have;
    `content` = the fact's topical tokens, used to score a claim's overlap.
    """
    recs: list[dict] = []
    for i, f in enumerate(brief.get("verified_facts") or []):
        if not isinstance(f, dict):
            continue
        ordered = [url_to_idx[u] for u in (f.get("sources") or []) if u in url_to_idx]
        if not ordered:
            continue
        recs.append({"idx": i, "src_idxs": set(ordered), "first_src": ordered[0],
                     "content": _content_tokens(str(f.get("claim", "")))})
    return recs


def _rank_fact_matches(text: str, fact_recs: list[dict]) -> tuple[int, list[dict]]:
    """(top_score, records tied at top_score) by content-token overlap with `text`.

    (0, []) when the claim has no content tokens or there are no citable facts.
    """
    ctoks = _content_tokens(text)
    if not ctoks or not fact_recs:
        return 0, []
    scored = [(len(r["content"] & ctoks), r) for r in fact_recs]
    top = max(score for score, _ in scored)
    return top, [r for score, r in scored if score == top]


def _reconcile_qualitative_ref(text: str, ref: int, fact_recs: list[dict]) -> int:
    """Repair a qualitative claim's source_ref to the fact it actually rests on.

    - top overlap below threshold -> ref kept (weak match; flagged separately, never
      silently re-pointed).
    - ref already in a best-matching fact's sources -> ref kept (correctly cited).
    - the top score is a tie across facts -> ref kept (ambiguous; flagged separately).
    - a single fact wins clearly and ref isn't one of its sources -> re-point to that
      fact's first resolvable source (the qualitative analog of the numeric repair).
    """
    top, tops = _rank_fact_matches(text, fact_recs)
    if top < MIN_QUALITATIVE_OVERLAP:
        return ref
    if any(ref in r["src_idxs"] for r in tops):
        return ref
    if len(tops) > 1:
        return ref
    return tops[0]["first_src"]


def find_qualitative_citation_problems(script: dict, brief: dict) -> list[dict]:
    """Deterministic guard: qualitative claims cited to a source no fact supports.

    The sibling of find_numeric_citation_problems for non-numeric claims. For every
    claim that asserts NO key_statistic figure but IS grounded on a verified_fact
    source, the cited source must belong to a best-content-matching fact. Reports:
    - `reason: mismatch`  — a single fact wins clearly but the claim is cited elsewhere
      (`expected_source_idx` names that fact's sources);
    - `reason: ambiguous` — the top match is tied across facts (can't re-point safely);
    - `reason: low_confidence` — no fact clears the overlap threshold.
    Pure + offline: inspectable before the gate (auto-repaired mismatches won't appear).
    """
    sources = brief.get("sources") or []
    url_to_idx = _source_url_index(brief)
    fact_recs = _fact_records(brief, url_to_idx)
    stat_recs = _stat_records(brief, url_to_idx)
    fact_src_space: set[int] = set().union(*[r["src_idxs"] for r in fact_recs]) \
        if fact_recs else set()
    problems: list[dict] = []
    for scene in script.get("scenes", []):
        for c in scene.get("claims", []):
            text = c.get("text", "")
            _, figure_seen = _correct_stat_sources(text, stat_recs)
            if figure_seen:
                continue  # a key-statistic number -> the numeric guard's domain
            resolved, src = resolve_source_ref(c.get("source_ref"), sources)
            idx = sources.index(src) if (resolved and src in sources) else None
            if idx is None or idx not in fact_src_space:
                continue  # not a verified-fact-grounded claim -> nothing to check
            top, tops = _rank_fact_matches(text, fact_recs)
            if top >= MIN_QUALITATIVE_OVERLAP and any(idx in r["src_idxs"] for r in tops):
                continue  # cited to a best-matching fact -> fine
            if top < MIN_QUALITATIVE_OVERLAP:
                reason, expected = "low_confidence", []
            elif len(tops) > 1:
                reason, expected = "ambiguous", []
            else:
                reason, expected = "mismatch", sorted(tops[0]["src_idxs"])
            problems.append({
                "claim_id": c.get("claim_id"),
                "scene_no": scene.get("scene_no"),
                "source_ref": c.get("source_ref"),
                "expected_source_idx": expected,
                "reason": reason,
                "text": text,
            })
    return problems


# ----------------------------------------------------------------------
# Magnitude/ratio comparatives are QUANTITATIVE. "an order of magnitude", "10x",
# "10× cheaper", "twice as fast", "half the price", "N-fold" each assert a MAGNITUDE,
# not just a direction — so they need the brief to establish that magnitude, exactly
# like an explicit number does. This cheap guard flags a claim that asserts a magnitude
# no verified_fact / stat text in THIS run's brief carries, so it's caught before the
# gate. (The rule itself is prompt-driven; this is the advisory safety net.)
# ----------------------------------------------------------------------
_OOM_RE = re.compile(r"orders?\s+of\s+magnitude")
_MAG_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:x\b|×|-?\s*fold\b)")
# Word multipliers that carry a magnitude on their own (ratio sense).
_MAG_WORDS = {"twice": 2, "double": 2, "doubled": 2, "doubles": 2, "triple": 3,
              "tripled": 3, "treble": 3, "quadruple": 4, "tenfold": 10, "half": 2}


def _magnitude_values(text: str) -> set[int]:
    """Magnitudes a phrase asserts as a ratio (rounded ints). Empty -> no magnitude.

    "an order of magnitude cheaper" -> {10}; "10× faster" -> {10}; "twice as fast" ->
    {2}; "half the price" -> {2}. "dramatically cheaper" -> set() (directional only, no
    magnitude). Used to test a claim's asserted magnitude against the brief's facts.
    """
    low = (text or "").lower()
    vals: set[int] = set()
    if _OOM_RE.search(low):
        vals.add(10)
    for m in _MAG_NUM_RE.finditer(low):
        try:
            vals.add(int(round(float(m.group(1)))))
        except ValueError:
            continue
    for w, v in _MAG_WORDS.items():
        if re.search(rf"\b{w}\b", low):
            vals.add(v)
    return vals


def _brief_magnitudes(brief: dict) -> set[int]:
    """Every magnitude any verified_fact / key_statistic text in the brief establishes."""
    mags: set[int] = set()
    for f in (brief.get("verified_facts") or []):
        if isinstance(f, dict):
            mags |= _magnitude_values(str(f.get("claim", "")))
    for s in (brief.get("key_statistics") or []):
        if isinstance(s, dict):
            mags |= _magnitude_values(f"{s.get('stat','')} {s.get('value','')}")
    return mags


def find_magnitude_comparative_problems(script: dict, brief: dict) -> list[dict]:
    """Deterministic guard: a claim asserting a magnitude the brief doesn't establish.

    A claim that uses a magnitude/ratio comparative ("order of magnitude", "10x",
    "twice as fast", "half the price") is flagged when that magnitude appears in NO
    verified_fact / key_statistic text in this run's brief — i.e. the brief carries the
    DIRECTION but not the MULTIPLE. Purely directional claims ("dramatically cheaper")
    carry no magnitude and are never flagged. Pure + offline; sibling of the citation
    guards, surfaced before the gate. The fix is to soften to directional language.
    """
    brief_mags = _brief_magnitudes(brief)
    problems: list[dict] = []
    for scene in script.get("scenes", []):
        for c in scene.get("claims", []):
            claim_mags = _magnitude_values(c.get("text", ""))
            if not claim_mags:
                continue  # no magnitude asserted -> directional, nothing to check
            unsupported = sorted(claim_mags - brief_mags)
            if unsupported:
                problems.append({
                    "claim_id": c.get("claim_id"),
                    "scene_no": scene.get("scene_no"),
                    "magnitudes": sorted(claim_mags),
                    "unsupported_magnitudes": unsupported,
                    "text": c.get("text", ""),
                })
    return problems


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


def _stat_is_dated(stat: dict) -> bool:
    """True if a key_statistic carries a real date/snapshot qualifier (not 'n.d.')."""
    d = str(stat.get("date", "") or "").strip().lower()
    return bool(d) and d not in ("n.d.", "nd", "n/a", "na", "unknown", "—", "-")


def _supporting_block(brief: dict) -> str:
    out = []
    stats = (brief.get("key_statistics") or [])[:MAX_STATS]
    if stats:
        out.append(
            "KEY STATISTICS — these are SINGLE-SOURCED by construction (one `source` "
            "each), so they are NOT consensus. Never state one as a bare fact. For each "
            "you use, choose: (a) ATTRIBUTE-AND-SOFTEN to its one source explicitly ('one "
            "June-2026 pricing tracker lists…') — only when it adds real value AND its "
            "category isn't flagged below — or (b) OMIT it. For volatile benchmark "
            "percentages, prefer OMIT; lead with the multi-source VERIFIED FACTS instead. "
            "STRONGEST RULE — OMIT, do not attribute: if a figure's CATEGORY is called "
            "out as conflicting/uncertain in OPEN QUESTIONS or CONTESTED below (e.g. "
            "pricing comparability across providers/versions, benchmark numbers that mix "
            "harnesses/versions), the number is uncorroborated — attribution will NOT "
            "satisfy the fact-checker, so drop the figure entirely and make the "
            "multi-source qualitative point from VERIFIED FACTS instead — stated "
            "DIRECTIONALLY ('far cheaper per token') rather than a single tracker's exact "
            "$/token. Use a MULTIPLE ('≈10×', 'an order of magnitude') ONLY if a verified "
            "fact carries that magnitude; otherwise keep it directional (see below). "
            "Two hard rules also apply: keep any date (a dated stat is a snapshot, never "
            "the current standing); and NEVER combine a figure from one entry with a "
            "model/benchmark from another — if you state a figure, use that ONE entry's "
            "exact model + benchmark, and tag it [S#] so the engine cites its own source:")
        for i, s in enumerate(stats):
            flag = "  ⟨dated snapshot — keep the qualifier⟩" if _stat_is_dated(s) else ""
            out.append(f"  [S{i}] {s.get('stat','')}: {s.get('value','')} "
                       f"({s.get('date','n.d.')}) — {s.get('source','')}{flag}")
    myths = (brief.get("myths_and_corrections") or [])
    if myths:
        out.append("\nMYTHS -> CORRECTIONS (great for the hook; assert the CORRECTION, "
                   "never the myth):")
        for m in myths:
            out.append(f"  - MYTH: {m.get('myth','')}  ->  TRUTH: {m.get('correction','')}")
    contested = (brief.get("contested_or_uncertain") or [])
    if contested:
        out.append("\nCONTESTED / UNCERTAIN (use ONLY softened/attributed — never as a "
                   "hard claim, never tag it; a figure here is NOT a settled fact):")
        for c in contested:
            out.append(f"  - {c.get('claim','')}  (why: {c.get('why','')})")
    open_qs = (brief.get("open_questions") or [])
    if open_qs:
        out.append("\nOPEN QUESTIONS (the brief flags these as unsettled — do NOT assert "
                   "anything here as a flat fact; if you raise it, frame it as open):")
        for q in open_qs:
            out.append(f"  - {q}")
    quotes = (brief.get("notable_quotes") or [])[:MAX_QUOTES]
    if quotes:
        out.append("\nNOTABLE QUOTES (short, attributed):")
        for q in quotes:
            out.append(f"  - \"{q.get('quote','')}\" — {q.get('who','')}")
    return "\n".join(out)


def _treatment_block(treatment: dict | None) -> str:
    """The director's creative treatment as a prompt section (empty when absent). Marlow
    writes the SCRIPT to this rhythm/emphasis; it shapes HOW, never WHAT (the brief fences
    the claims). Direction only — no vocabulary the scriptwriter can't act on."""
    if not isinstance(treatment, dict) or not treatment:
        return ""
    lines = ["=== THE DIRECTOR'S CREATIVE TREATMENT (write the script to this direction) ==="]
    if treatment.get("rhythm"):
        lines.append(f"RHYTHM (pace the script to this arc): {treatment['rhythm']}")
    if treatment.get("emphasis"):
        lines.append(f"THE ONE IDEA TO LAND: {treatment['emphasis']}")
    if treatment.get("visual_world"):
        lines.append(f"TONE/WORLD: {treatment['visual_world']}")
    beats = treatment.get("beats") or []
    if beats:
        lines.append("BEATS (let these shape the hook→scenes→CTA arc; do NOT invent facts "
                     "for them — only order/frame what the brief supports):")
        for b in beats[:12]:
            ew = f" — land the word '{b.get('emphasis_word')}'" if b.get("emphasis_word") else ""
            lines.append(f"  · {b.get('beat', '?')}: {b.get('concept', '')}{ew}")
    if treatment.get("negative"):
        lines.append("AVOID: " + "; ".join(treatment["negative"]))
    return "\n".join(lines) + "\n\n"


def _narrative_intent_block(intent: dict | None) -> str:
    """The emotional score as a per-scene prompt section (empty when absent).

    This is where the emotional objective that used to evaporate at the handoff becomes
    a hard writing instruction: each scene names the felt emotion + intensity + pacing,
    and the closing rules turn pacing/emotion into concrete word-choice + sentence-shape
    constraints (punchy_staccato -> short sentences, max 12 words; awe -> open on a
    written-in ellipsis of silence, then one weighty sentence). It shapes HOW the lines
    land; it never relaxes the brief fence (assert nothing the research doesn't support).
    """
    if not isinstance(intent, dict) or not intent:
        return ""
    vl = intent.get("video_level") or {}
    scenes = intent.get("per_scene_intent") or []
    lines = ["=== THE EMOTIONAL SCORE (write narration that LANDS each emotion) ==="]
    if vl.get("emotional_journey"):
        lines.append(f"THE ARC THE VIEWER FEELS: {vl['emotional_journey']}")
    if vl.get("tone_profile"):
        lines.append(f"OVERALL TONE: {vl['tone_profile']}")
    if scenes:
        lines.append("PER-SCENE EMOTIONAL DIRECTION (write the scenes IN THIS ORDER; "
                     "scene 1 is the hook, the last is the CTA):")
        for sc in scenes[:40]:
            n = (sc.get("scene_index", 0) or 0) + 1   # 0-based score -> 1-based scene_no
            note = sc.get("delivery_note") or ""
            note_str = f" Delivery instruction: {note}" if note else ""
            lines.append(
                f"  SCENE {n}: This scene is in the '{sc.get('arc_phase', 'build')}' phase. "
                f"The viewer must feel: {sc.get('primary_emotion', '—')} at intensity "
                f"{sc.get('intensity', 5)}/10. Pacing directive: "
                f"{sc.get('pacing_directive', 'measured')}.{note_str}")
    lines.append(
        "WRITE NARRATION THAT LANDS THESE EMOTIONS. If the pacing is 'punchy_staccato', "
        "your sentences must be short — max 12 words. If the pacing is 'breathless' or "
        "'driving', keep momentum with short, propulsive clauses. If the emotion is 'awe', "
        "open with a moment of silence written into the text as an ellipsis (…), then a "
        "single, weighty sentence. If the emotion is 'contemplative' or the pacing is "
        "'deliberate_pause', let a sentence breathe and slow the cadence. Match the WORD "
        "CHOICE to the feeling — but never assert a fact the brief doesn't support.")
    return "\n".join(lines) + "\n\n"


# ----------------------------------------------------------------------
# Motion-mood-board pacing governance (Task 4). The design-first artifact maps each
# emotional beat to a pacing_profile; this turns that profile into CONCRETE writing
# rules so the script's rhythm is governed by the visual architecture (a sibling of the
# narrative_intent per-scene block — that one is per-SCENE emotion, this is per-BEAT
# pacing/duration/layout from the mood board). Pure + testable.
# ----------------------------------------------------------------------
_PACING_RULES = {
    "rapid_staccato": (
        "- Keep sentences short — max 12 words. No dependent clauses. Periods, not commas.\n"
        "- Every sentence advances the argument; the viewer should feel slightly out of breath.\n"
        "- Rhythm: \"AI writes code. Forty-one percent. You didn't notice. That's the point.\""),
    "steady_build": (
        "- Sentences lengthen as the beat progresses; open short and arresting.\n"
        "- Build to one complex sentence that lands the key insight; use em-dashes for momentum.\n"
        "- The viewer should feel information accumulating toward a revelation."),
    "slow_reveal": (
        "- Begin with silence written in — an ellipsis (…) or a single word, then a pause.\n"
        "- Each sentence is one step closer to the truth; use concrete, sensory language.\n"
        "- End the beat ON the revelation, not after it. The viewer should lean in."),
    "held_stillness": (
        "- Maximum 8 words per sentence. One idea, one sentence — let it breathe.\n"
        "- No statistics here; this is the human moment. Ask a question; don't answer it.\n"
        "- Rhythm: \"The reviews stopped. Nobody noticed. What else are we missing?\""),
    "conversational_flow": (
        "- Natural speech rhythm; contractions welcome. One idea per sentence, room to breathe.\n"
        "- Write for the ear, not the page — avoid a formal register.\n"
        "- The viewer should feel they're being told a story over coffee."),
}


def get_pacing_rules(profile: str) -> str:
    """Concrete, per-profile writing rules for a motion_mood_board pacing_profile.

    Unknown/missing profiles fall back to 'conversational_flow' — the neutral default,
    so a malformed beat never produces empty guidance (mirrors the fallback path)."""
    return _PACING_RULES.get(profile, _PACING_RULES["conversational_flow"])


def _motion_mood_board_block(board: dict | None) -> str:
    """The motion mood board as a per-beat pacing prompt section (empty when absent).

    This is the design-first inversion in Marlow's prompt: the visual architecture
    GOVERNS the script's pacing. It surfaces the global tempo + motion philosophy, then
    each beat's pacing_profile (expanded into get_pacing_rules), its felt emotion, its
    duration target (Marlow fits the narration within it), and its layout/visual feeling
    so the words are written to the frame they'll live in. Shapes HOW, never WHAT — the
    brief stays the fence."""
    if not isinstance(board, dict) or not board:
        return ""
    vl = board.get("video_level") or {}
    beats = board.get("beat_map") or []
    lines = ["=== THE MOTION MOOD BOARD (write the script TO this visual architecture) ==="]
    if vl.get("global_tempo"):
        lines.append(f"GLOBAL TEMPO (the whole video's rhythm): {vl['global_tempo']}")
    if vl.get("dominant_motion_philosophy"):
        lines.append(f"MOTION PHILOSOPHY: {vl['dominant_motion_philosophy']}")
    if vl.get("global_texture") and vl["global_texture"] != "clean":
        lines.append(f"GLOBAL TEXTURE: {vl['global_texture']}")
    if beats:
        lines.append("PER-BEAT PACING (write each beat's scenes to its profile, in order):")
        for b in beats[:12]:
            mood = f" Visual feeling: {b['visual_mood_ref']}." if b.get("visual_mood_ref") else ""
            lines.append(
                f"\nBEAT {b.get('beat_id', '?')} ({b.get('arc_phase', '?')}): "
                f"emotion {b.get('primary_emotion', '—')} at "
                f"{b.get('intensity', '?')}/10. Pacing profile: "
                f"{b.get('pacing_profile', 'conversational_flow')}. Target duration: "
                f"~{b.get('scene_duration_target_sec', '?')}s. Layout: "
                f"{b.get('layout_family', '—')} — fit what you describe to it.{mood}\n"
                f"WRITING RULES FOR THIS BEAT:\n"
                f"{get_pacing_rules(b.get('pacing_profile'))}")
    lines.append("\nFit each beat's narration within its target duration "
                 "(≈2.5 words/sec). The mood board governs RHYTHM and SHAPE; it never "
                 "relaxes the brief fence — assert nothing the research doesn't support.")
    return "\n".join(lines) + "\n\n"


def _build_prompt(brief: dict, treatment: dict | None = None,
                  narrative_intent: dict | None = None,
                  motion_mood_board: dict | None = None) -> str:
    angle = brief.get("angle") or ""
    audience = brief.get("target_audience") or "a curious general audience"
    overview = brief.get("overview") or ""
    title = brief.get("working_title") or ""
    angle_note = f"\nThe angle to take: {angle}" if angle else ""
    title_note = f"\nA working title to consider (improve it if you can): {title}" if title else ""
    return (
        f"=== METHOD ===\n{SKILL}\n\n"
        f"{_treatment_block(treatment)}"
        f"{_narrative_intent_block(narrative_intent)}"
        f"{_motion_mood_board_block(motion_mood_board)}"
        f"=== THE RESEARCH BRIEF (your raw material AND your fence — assert nothing "
        f"it doesn't contain) ===\n"
        f"TOPIC: {brief.get('topic','')}\n"
        f"AUDIENCE: {audience}{angle_note}{title_note}\n\n"
        f"OVERVIEW:\n{overview}\n\n"
        f"VERIFIED FACTS — the facts you may assert. Each has a (confidence). Tag a "
        f"claim resting on one of these with its [F#]:\n{_facts_block(brief)}\n\n"
        f"{_supporting_block(brief)}\n\n"
        "Apply the METHOD. Find the through-line, open on a hook that earns the first "
        "five seconds (no throat-clearing), order the facts into one-point scenes, earn "
        "ONE vivid sourced detour, and close on a clean CTA (never 'in conclusion').\n\n"
        "CITE EACH CLAIM TO WHAT ACTUALLY SUPPORTS IT:\n"
        "- A claim resting on a verified fact -> `support` = its [F#].\n"
        "- A claim asserting a STATISTIC/number -> `support` = the [S#] of the key "
        "statistic carrying that exact figure (so it's cited to that stat's own source, "
        "not a borrowed one). Never reuse one blanket tag for every claim.\n"
        "- Do NOT write a URL or a raw index; the engine resolves your tag. A line you "
        "can't tag must be cut.\n\n"
        "HONOR THE RELIABILITY BOUNDARY — consensus-forward, keep solid specifics, drop "
        "fragile decimals:\n"
        "- LEAD with the multi-source VERIFIED FACTS — the load-bearing claims and ALL "
        "on_screen_text come from there (they carry several sources + a confidence and "
        "pass fact-check every time). A specific number that lives INSIDE a verified "
        "fact (e.g. a ~1M-token context window) is consensus — state it confidently.\n"
        "- KEY STATISTICS are single-sourced: attribute-and-soften to their one source, "
        "or omit. Prefer OMIT for volatile benchmark percentages. Never a bare fact.\n"
        "- OMIT (don't attribute) a single-source figure whose CATEGORY the brief flags "
        "as conflicting/uncertain in OPEN QUESTIONS or CONTESTED (e.g. pricing "
        "comparability, benchmark version/harness mixing). Attribution does NOT satisfy "
        "the fact-checker for an uncorroborated number — lead instead with the "
        "multi-source qualitative VERIFIED FACT, stated directionally ('far cheaper per "
        "token').\n"
        "- MAGNITUDE/RATIO COMPARATIVES ARE QUANTITATIVE. 'An order of magnitude', "
        "'10x', '10× cheaper', 'twice as fast', 'half the price', 'N-fold' each assert a "
        "MAGNITUDE, not just a direction — so use one ONLY when a VERIFIED FACT (or a "
        "corroborated stat) in THIS brief establishes that magnitude. If the brief "
        "supports only the DIRECTION (e.g. 'cheaper per token' with no multiple), say it "
        "directionally — 'far/dramatically cheaper', 'much faster', 'far more capable' — "
        "with NO implied multiple. The brief is re-researched every run; never carry a "
        "magnitude word ('order of magnitude', '10×') over from a past run or memory.\n"
        "- Never cite a figure to a different model/benchmark than the entry it came "
        "from; if two entries share a number, they are NOT interchangeable.\n"
        "- on_screen_text must never display an unhedged shaky number (a single-source "
        "stat) — and never a MULTIPLE the brief doesn't establish. Put the qualitative "
        "point on screen ('code that ships', 'FAR CHEAPER'), not a bare decimal and not "
        "an unsupported '≈10×'.\n"
        "- PRESERVE dates/qualifiers — never present a snapshot as the present standing.\n"
        "- Stay internally consistent: if the video says rankings change every version / "
        "models are a generation behind, do NOT also assert a specific 'current leader' "
        "number that contradicts it.\n\n"
        "Hook and CTA scenes usually assert no fact and carry \"claims\": []. Return "
        "ONLY the JSON object from 'Your output contract'."
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
    stats = brief.get("key_statistics") or []
    url_to_idx = _source_url_index(brief)
    recs = _stat_records(brief, url_to_idx)
    fact_recs = _fact_records(brief, url_to_idx)

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
            ref = resolve_support(c.get("support"), facts, url_to_idx, stats)
            if ref is None:
                continue  # can't tag it to a brief source -> it does not ship
            # Deterministic safety nets, mirroring each other:
            #  - a claim that asserts a key_statistic figure is cited to the entry that
            #    matches on figure AND model/benchmark (numeric); a number matching a
            #    stat value but no entry's label (None) must not ship.
            #  - a qualitative claim (no key-stat figure) tagged to a verified_fact is
            #    re-pointed to the fact its TEXT actually rests on, so a true claim
            #    can't block on a mis-tagged source. S#-tagged claims are left to the
            #    numeric path; weak/ambiguous matches are left alone (and flagged).
            _, figure_seen = _correct_stat_sources(text, recs)
            if figure_seen:
                ref = _reconcile_numeric_ref(text, ref, recs)
                if ref is None:
                    continue
            elif not _is_stat_tag(c.get("support")):
                ref = _reconcile_qualitative_ref(text, ref, fact_recs)
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
# ----------------------------------------------------------------------
# Creative Roundtable wiring — Marlow's internal Critic→Researcher→Craftsman review
# (Task 5). The roundtable lives in roundtable.py; here we build its config from
# Marlow's own persona files + chat seam, run it, and DEFEND traceability: an enhanced
# script that no longer grounds every claim is rejected and the draft ships instead.
# ----------------------------------------------------------------------
def _roundtable_search():
    """Marlow's Researcher web-search tool, or None when search is unavailable.

    A self-contained DuckDuckGo (`ddgs`) seam wrapped defensively — a missing library
    or a flaky/rate-limited source returns [] and never crashes a script run. Mirrors
    the coaches' search.py contract: web_search(query, max_results=5) -> list[dict]."""
    def web_search(query: str, max_results: int = 5) -> list:
        try:
            from ddgs import DDGS
        except Exception:  # noqa: BLE001 — ddgs not installed -> Researcher uses knowledge
            return []
        try:
            out = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    url = r.get("href") or r.get("url") or ""
                    if url:
                        out.append({"url": url, "title": (r.get("title") or "").strip(),
                                    "snippet": (r.get("body") or "").strip(),
                                    "source_type": "web"})
            return out
        except Exception:  # noqa: BLE001 — a search source must never fail a run
            return []
    return web_search


def _import_roundtable():
    """Import the sibling `roundtable` module by ABSOLUTE FILE PATH (not sys.path).

    Why not a bare `import roundtable`: on the live pipeline this engine is loaded by
    atlas's loader, which puts scriptwriter/ on sys.path only DURING module load and
    restores it afterwards. This roundtable import is lazy (it runs at CALL time, when
    write_script executes), by which point scriptwriter/ is gone from sys.path — so a
    bare import raises ModuleNotFoundError and sinks the whole script stage. Loading by
    file path is independent of sys.path, so it works identically on the live pipeline
    and the unit-test path. Cached under a DIR-UNIQUE key so the future Iris/Cadence/
    Mason copies of roundtable.py (per the replication blueprint) can never cross-wire
    onto Marlow's module in sys.modules."""
    import importlib.util
    import os
    import sys
    sw_dir = os.path.dirname(os.path.abspath(__file__))
    mod_key = "_roundtable_" + os.path.basename(sw_dir)
    cached = sys.modules.get(mod_key)
    if cached is not None:
        return cached
    path = os.path.join(sw_dir, "roundtable.py")
    spec = importlib.util.spec_from_file_location(mod_key, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not locate roundtable.py in {sw_dir}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_key] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_roundtable(draft: dict, brief: dict, *, chat_fn,
                    treatment: dict | None, narrative_intent: dict | None,
                    motion_mood_board: dict | None,
                    project_dir: pathlib.Path | None) -> dict:
    """Run the Creative Roundtable over a traceable draft; return the enhanced script
    iff it STILL grounds every claim, else the draft unchanged.

    The roundtable itself never raises (it degrades to the draft on any sub-agent
    failure). On top of that, we re-assert Marlow's hard traceability guarantee over the
    Craftsman's output: a rewrite that introduces an unresolvable source_ref is rejected
    here — Marlow never ships a claim he can't trace, roundtable or not."""
    rt = _import_roundtable()

    upstream_intent = {
        "thematic_anchor": brief.get("thematic_anchor", {}),
        "creative_treatment": treatment or {},
        "narrative_intent": narrative_intent or {},
        "motion_mood_board": motion_mood_board or {},
    }
    config = rt.RoundtableConfig(
        specialist_name="Marlow", specialist_role="Scriptwriter",
        skill_md=SKILL, style_md=STYLE, soul_md=SOUL,
        llm_chat=chat_fn, search_tool=_roundtable_search(),
    )
    try:
        enhanced, _log = rt.CreativeRoundtable(config).review_and_enhance(
            draft, upstream_intent, project_dir=project_dir)
    except Exception as exc:  # noqa: BLE001 — defense in depth; the review never sinks the job
        print(f"  · (roundtable failed: {exc}; keeping the draft)")
        return draft

    if enhanced is draft or enhanced == draft:
        return draft
    # The Craftsman's rewrite must still pass Marlow's traceability guarantee.
    try:
        assert_traceable(enhanced, brief)
    except AssertionError:
        print("  · (roundtable rewrite broke claim traceability; keeping the draft)")
        return draft
    return enhanced


def write_script(brief: dict, *, chat_fn=llm.chat, treatment: dict | None = None,
                 narrative_intent: dict | None = None,
                 motion_mood_board: dict | None = None,
                 use_roundtable: bool = False,
                 project_dir: pathlib.Path | None = None) -> dict:
    """Turn a research brief into a script dict (frozen shape, minus schema_version).

    Validates the brief, makes ONE arc call to the brain (with a single retry if the
    hook throat-clears), resolves every claim's support deterministically, and
    asserts traceability before returning. Atlas stamps schema_version + validates.

    `treatment` (optional) is the director's creative_treatment — when present, Marlow
    writes to its rhythm + per-beat emphasis. It shapes HOW the story is told; it never
    relaxes the brief fence (assert nothing the research doesn't support).

    `narrative_intent` (optional) is the emotional score — the per-scene emotion +
    intensity + pacing directives that the creative_treatment's poetry was translated
    into. When present, Marlow writes narration that LANDS each scene's emotion, with
    sentence length + word choice constrained by the pacing directive. Like the
    treatment it shapes HOW, never WHAT — the brief stays the fence.

    `motion_mood_board` (optional) is the design-first visual architecture — the per-beat
    pacing_profile / duration target / layout that the visual language imposes on the
    script's rhythm (design-first: the motion governs the words). When present, each beat's
    pacing_profile is expanded into concrete writing rules (get_pacing_rules) and Marlow
    fits the narration to the beat's duration. Same contract as the others: shapes HOW,
    never WHAT — the brief stays the fence. None -> standard pacing (backward-compatible).
    """
    ok, reason = validate_brief(brief)
    if not ok:
        raise ValueError(reason)

    prompt = _build_prompt(brief, treatment, narrative_intent, motion_mood_board)
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

    # The draft is grounded. If the Creative Roundtable is enabled, run Marlow's internal
    # Critic→Researcher→Craftsman review and ship the enhanced script — but only if it
    # still grounds every claim (else the draft ships). Opt-in: off by default keeps the
    # pure seam fast + deterministic for tests; the pipeline turns it on.
    if use_roundtable:
        script = _run_roundtable(
            script, brief, chat_fn=chat_fn, treatment=treatment,
            narrative_intent=narrative_intent, motion_mood_board=motion_mood_board,
            project_dir=project_dir)
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
