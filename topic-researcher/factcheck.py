"""Sage's pass-2 engine: fact-check a drafted script against the research brief.

This is Sage's second hat. Pass 1 (researcher.py) is generative — it builds the
brief from the open web. Pass 2 here is adversarial — it interrogates a written
script *against that very brief*, treating the script as guilty until sourced.

THE BRIEF IS THE GROUND TRUTH. For every claim the script makes we cross-check it
against the brief's pass-1 buckets and re-verify ONLY what the brief didn't already
settle. Per script claim, in order:

1. Resolve `source_ref` -> the cited entry in research_brief.sources. If it doesn't
   resolve (or the cited source doesn't actually carry the claim) -> FLAGGED
   (mis-sourced) — a claim can be *true* but mis-sourced and still gets flagged.
2. Map the claim against the brief's buckets:
   - matches verified_facts (and the source supports it)      -> VERIFIED
   - matches contested_or_uncertain                           -> FLAGGED (uncertainty as fact)
   - matches the myth side of myths_and_corrections           -> FLAGGED (repeated a myth)
   - no correspondence in the brief (a NEW claim)             -> re-verify via the search seam
3. Catch drift: "true-ish" but overstated vs its source (a hedge stated as a hard
   number, a range stated as a point)                         -> FLAGGED.

Output is a factcheck-report DICT in the frozen shape (verdict / summary / claims).
Decoupling boundary: this engine emits the dict and never imports atlas; ATLAS owns
schema validation and stamps `schema_version` at the adapter/pipeline boundary.

Re-search is BOUNDED: we verify specific new claims only — never re-research the
whole topic or expand the brief.
"""
from __future__ import annotations

import json

import llm
import researcher  # pass-1 engine: reuse SOUL, SKILL, the JSON parser, and the search seam

search = researcher.search
SOUL = researcher.SOUL
SKILL = researcher.SKILL

# Bounded re-verification budget for a single NEW claim (never a whole re-research).
REVERIFY_WEB = 3
REVERIFY_WIKI = 2
REVERIFY_READ = 2          # pages fetched in full when judging a new claim
REVERIFY_CHARS = 2000

# The terminal per-claim states (the frozen contract's enum).
VERIFIED = "verified"
FLAGGED = "flagged"
UNVERIFIABLE = "unverifiable"

# Default one-line fixes per flag reason (used when the brain doesn't supply a note).
_FLAG_NOTES = {
    "contested": "The brief lists this as contested/uncertain — soften it or attribute it.",
    "myth": "The brief flags this as a myth — replace it with the correction.",
    "overstated": "Overstated vs the source — match the claim's strength to the evidence.",
    "mis_sourced": "The cited source doesn't support this claim — cite one that does.",
    "unknown": "Couldn't map this to the brief — needs a real source or a cut.",
}


# ----------------------------------------------------------------------
# LLM JSON seam (chat_fn injectable so tests run with no network)
# ----------------------------------------------------------------------
def _chat_json(system: str, user: str, chat_fn=llm.chat):
    """Call the brain and parse a JSON value, retrying once with a blunt reminder.

    Mirrors researcher._chat_json but takes an injectable chat_fn so pass-2 stays
    unit-testable offline.
    """
    reply = ""
    for attempt in range(2):
        prompt = user if attempt == 0 else (
            user + "\n\nREMINDER: Output ONLY raw JSON — no greeting, no explanation, "
            "no markdown fences. Your entire reply must start with '[' or '{'.")
        reply = chat_fn(system, prompt)
        try:
            return json.loads(researcher._strip_json(reply))
        except Exception:
            continue
    raise ValueError(
        "Model did not return valid JSON after a retry. First 200 chars:\n"
        + reply.strip()[:200])


# ----------------------------------------------------------------------
# Deterministic: resolve a claim's source_ref against the brief's sources
# ----------------------------------------------------------------------
def resolve_source_ref(source_ref, sources: list) -> tuple[bool, dict | None]:
    """Resolve `source_ref` to a brief source. Returns (resolved, source_or_None).

    A source_ref may be an integer index (0-based, as the script writer emits), a
    digit string, or a URL string. `None`/empty means "no citation given" — not a
    resolution failure on its own (the brain still maps the claim), but a non-null
    ref that points nowhere IS a failure (mis-sourced).
    """
    sources = sources or []
    if source_ref is None or (isinstance(source_ref, str) and not source_ref.strip()):
        return False, None
    # integer (or digit-string) index into the sources list
    idx = None
    if isinstance(source_ref, bool):  # guard: bool is an int subclass
        idx = None
    elif isinstance(source_ref, int):
        idx = source_ref
    elif isinstance(source_ref, str) and source_ref.strip().lstrip("-").isdigit():
        idx = int(source_ref.strip())
    if idx is not None:
        if 0 <= idx < len(sources):
            return True, sources[idx]
        return False, None
    # otherwise treat it as a URL and look for a matching source
    ref = str(source_ref).strip()
    for s in sources:
        if isinstance(s, dict) and s.get("url") == ref:
            return True, s
    return False, None


# ----------------------------------------------------------------------
# Iterate the script's claims (flat, with their scene number)
# ----------------------------------------------------------------------
def iter_claims(script: dict):
    """Yield (scene_no, claim_dict) for every claim in the script, in order."""
    for scene in (script or {}).get("scenes", []) or []:
        if not isinstance(scene, dict):
            continue
        scene_no = scene.get("scene_no", 0)
        for c in scene.get("claims", []) or []:
            if isinstance(c, dict) and (c.get("claim_id") or "").strip():
                yield scene_no, c


# ----------------------------------------------------------------------
# Brief / claim rendering for the mapping prompt
# ----------------------------------------------------------------------
def _bucket_block(brief: dict) -> str:
    def lines(items, fmt):
        items = items or []
        return "\n".join(f"    - {fmt(x)}" for x in items if isinstance(x, dict)) or "    (none)"

    vf = lines(brief.get("verified_facts"),
               lambda x: f"{x.get('claim','')}  [sources: {', '.join(x.get('sources', []) or [])}]")
    co = lines(brief.get("contested_or_uncertain"),
               lambda x: f"{x.get('claim','')}  (why: {x.get('why','')})")
    my = lines(brief.get("myths_and_corrections"),
               lambda x: f"MYTH: {x.get('myth','')}  ->  {x.get('correction','')}")
    return (f"VERIFIED_FACTS:\n{vf}\n\n"
            f"CONTESTED_OR_UNCERTAIN:\n{co}\n\n"
            f"MYTHS_AND_CORRECTIONS:\n{my}")


def _claims_block(script: dict, brief: dict) -> str:
    rows = []
    for scene_no, c in iter_claims(script):
        resolved, src = resolve_source_ref(c.get("source_ref"), brief.get("sources"))
        cite = (f"cited source -> {src.get('url','')} "
                f"({src.get('title','')})" if resolved and src
                else f"cited source_ref={c.get('source_ref')!r} -> DOES NOT RESOLVE")
        rows.append(f"- claim_id={c.get('claim_id')} (scene {scene_no})\n"
                    f"    text: {c.get('text','')}\n"
                    f"    {cite}")
    return "\n".join(rows) or "(no claims)"


# ----------------------------------------------------------------------
# Pass 2a — map every script claim against the brief (the brain's judgment)
# ----------------------------------------------------------------------
def map_claims_against_brief(script: dict, brief: dict, *, chat_fn=llm.chat) -> dict:
    """Ask the brain to classify each script claim against the brief's buckets.

    Returns {claim_id: {"match": <kind>, "note": str, "sources": [url, ...]}} where
    `match` is one of: verified_fact | contested | myth | overstated | mis_sourced
    | new. The engine turns these into terminal statuses (see `finalize_claim`).
    Empty when there are no claims.
    """
    if not any(True for _ in iter_claims(script)):
        return {}
    user = (
        f"=== METHOD (Pass 2 — Fact-Check) ===\n{SKILL}\n\n"
        f"=== THE RESEARCH BRIEF (your ground truth — the ONLY thing the script may "
        f"rest on) ===\n{_bucket_block(brief)}\n\n"
        f"=== THE SCRIPT'S CLAIMS (guilty until sourced) ===\n"
        f"{_claims_block(script, brief)}\n\n"
        "THE BRIEF IS YOUR GROUND TRUTH. You already established it in pass 1 — do "
        "NOT re-investigate whether the brief's verified facts are 'really' true, and "
        "do NOT judge the brief's own sources. Your only job here is to check whether "
        "the SCRIPT faithfully represents the brief.\n\n"
        "For EACH claim_id, decide how it relates to the brief and return your "
        "judgment. `match` must be exactly one of:\n"
        "  verified_fact — the claim restates / is supported by a brief "
        "VERIFIED_FACTS entry. (If the claim corresponds to a verified fact, this is "
        "the answer — trust the brief.)\n"
        "  contested     — it matches a CONTESTED_OR_UNCERTAIN entry (the writer "
        "presented uncertainty as settled fact).\n"
        "  myth          — it matches the myth side of MYTHS_AND_CORRECTIONS (the "
        "writer repeated a known myth).\n"
        "  overstated    — there's a true kernel in the brief but the script "
        "overstates it (a hedge stated as a hard number, a range as a point).\n"
        "  mis_sourced   — the claim corresponds to brief material, but the script's "
        "CITED source is the wrong one for it (it points to a source that doesn't "
        "carry this particular claim).\n"
        "  new           — there is genuinely NO correspondence in the brief at all "
        "(the writer introduced a claim the research never covered). Use this ONLY "
        "when nothing in the brief speaks to the claim.\n\n"
        "For each, give a one-line `note` = what's wrong + the single fix (better "
        "source / soften / attribute / cut), and `sources` = the brief source "
        "URL(s) that support a verified_fact (else []). Judge ONLY whether each "
        "claim holds against the brief — do NOT argue about the topic, and do NOT "
        "rewrite the script. Return ONLY a JSON array, one object per claim:\n"
        '[{"claim_id": "...", "match": "...", "note": "...", "sources": ["url"]}]'
    )
    raw = _chat_json(SOUL, user, chat_fn=chat_fn)
    out: dict[str, dict] = {}
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and (item.get("claim_id") or "").strip():
            out[str(item["claim_id"]).strip()] = {
                "match": (item.get("match") or "").strip().lower(),
                "note": (item.get("note") or "").strip(),
                "sources": [s for s in (item.get("sources") or []) if s],
            }
    return out


# ----------------------------------------------------------------------
# Pass 2b — bounded re-verification of a NEW claim via the search seam
# ----------------------------------------------------------------------
def reverify_claim(claim_text: str, *, chat_fn=llm.chat, quiet: bool = True) -> dict:
    """Re-verify ONE new claim against the live web. Returns {status, sources, note}.

    Bounded on purpose: a small search on the specific claim, a couple of pages
    read, then a single judgment. Never expands into a fresh topic research. A solid
    corroborating source -> verified; nothing credible -> unverifiable.
    """
    by_url: dict[str, dict] = {}
    for it in (search.web_search(claim_text, REVERIFY_WEB, quiet=quiet)
               + search.wiki_search(claim_text, REVERIFY_WIKI, quiet=quiet)):
        url = it.get("url")
        if url and url not in by_url:
            by_url[url] = it
    hits = list(by_url.values())
    if not hits:
        return {"status": UNVERIFIABLE, "sources": [],
                "note": "No credible source found on a bounded re-check — needs a "
                        "source or should be cut."}

    read = 0
    for s in hits:
        s["credibility_note"] = search.credibility_note(s["url"])
        if read < REVERIFY_READ:
            body = search.fetch_text(s["url"], REVERIFY_CHARS, quiet=quiet)
            if body:
                s["text"] = body
                read += 1

    evidence = "\n\n".join(
        f"[{i}] {s.get('title') or '(untitled)'}\n    url: {s['url']}\n"
        f"    credibility: {s.get('credibility_note','')}\n"
        f"    extract: {(s.get('text') or s.get('snippet') or '(no extract)')[:REVERIFY_CHARS]}"
        for i, s in enumerate(hits, 1))
    user = (
        f"=== METHOD (Pass 2 — re-verifying ONE new claim) ===\n{SKILL}\n\n"
        f"=== THE CLAIM (introduced by the script; not in the brief) ===\n{claim_text}\n\n"
        f"=== EVIDENCE (the only sources you may cite, by url) ===\n{evidence}\n\n"
        "Decide whether independent, credible evidence above SUPPORTS this exact "
        "claim. 'supported' requires real corroboration (one weak/single source is "
        "NOT enough). Cite ONLY urls from the EVIDENCE — never invent one. Return "
        'ONLY: {"supported": true|false, "sources": ["url"], "note": "one line: '
        'what holds, or what\'s missing + the fix"}'
    )
    try:
        verdict = _chat_json(SOUL, user, chat_fn=chat_fn)
    except Exception as exc:  # a judging failure is an honest "couldn't verify"
        return {"status": UNVERIFIABLE, "sources": [],
                "note": f"Couldn't complete re-verification ({exc}); treat as unsourced."}
    supported = bool(verdict.get("supported"))
    srcs = [s for s in (verdict.get("sources") or []) if s]
    note = (verdict.get("note") or "").strip()
    if supported and srcs:
        return {"status": VERIFIED, "sources": srcs,
                "note": note or "Re-verified against an independent source."}
    return {"status": UNVERIFIABLE, "sources": srcs,
            "note": note or "Re-check didn't find solid corroboration — needs a "
                            "source or a cut."}


# ----------------------------------------------------------------------
# Turn one mapped claim into a terminal report claim
# ----------------------------------------------------------------------
def _claim(claim_id, scene_no, text, status, sources, note) -> dict:
    return {"claim_id": str(claim_id), "scene_no": scene_no, "claim_text": text,
            "status": status, "sources": list(sources or []), "note": note}


def finalize_claim(scene_no, claim: dict, mapped: dict, brief: dict,
                   *, chat_fn=llm.chat, quiet: bool = True) -> dict:
    """Resolve one claim to a terminal status, given the brain's mapping.

    Order: a non-resolving cited ref is mis-sourced no matter what; a NEW claim goes
    to bounded re-verification; otherwise the mapped bucket decides verified vs flagged.
    """
    cid = claim.get("claim_id")
    text = claim.get("text", "")
    sref = claim.get("source_ref")
    match = (mapped.get("match") or "new").strip().lower()
    note = (mapped.get("note") or "").strip()
    mapped_sources = mapped.get("sources") or []

    resolved, src = resolve_source_ref(sref, brief.get("sources"))

    # Deterministic guard: a claim that CITES a source which doesn't resolve is
    # mis-sourced regardless of how true it is. (A null ref is handled by the brain.)
    if sref is not None and not (isinstance(sref, str) and not sref.strip()) and not resolved:
        return _claim(cid, scene_no, text, FLAGGED, mapped_sources,
                      note or f"Cited source_ref {sref!r} doesn't resolve to a brief "
                              "source — fix the citation.")

    if match == "new":
        rv = reverify_claim(text, chat_fn=chat_fn, quiet=quiet)
        return _claim(cid, scene_no, text, rv["status"], rv["sources"], rv["note"])

    if match == "verified_fact":
        srcs = mapped_sources or ([src["url"]] if (resolved and src and src.get("url")) else [])
        return _claim(cid, scene_no, text, VERIFIED, srcs,
                      note or "Matches a verified fact in the brief.")

    if match in ("contested", "myth", "overstated", "mis_sourced"):
        return _claim(cid, scene_no, text, FLAGGED, mapped_sources,
                      note or _FLAG_NOTES[match])

    # Unknown / unparseable mapping -> never silently pass; flag conservatively.
    return _claim(cid, scene_no, text, FLAGGED, mapped_sources,
                  note or _FLAG_NOTES["unknown"])


# ----------------------------------------------------------------------
# Verdict aggregation
# ----------------------------------------------------------------------
def summarize(claims: list[dict]) -> dict:
    return {
        "verified": sum(1 for c in claims if c["status"] == VERIFIED),
        "flagged": sum(1 for c in claims if c["status"] == FLAGGED),
        "unverifiable": sum(1 for c in claims if c["status"] == UNVERIFIABLE),
    }


def verdict_for(claims: list[dict]) -> str:
    """`block` if ANY claim is flagged or unverifiable, else `pass`."""
    return "block" if any(c["status"] in (FLAGGED, UNVERIFIABLE) for c in claims) else "pass"


# ----------------------------------------------------------------------
# The entry point — emits the report dict (atlas stamps schema_version + validates)
# ----------------------------------------------------------------------
def factcheck(script: dict, brief: dict, *, chat_fn=llm.chat, quiet: bool = True) -> dict:
    """Fact-check `script` against `brief`; return the report dict (frozen shape).

    Returns {"verdict", "summary", "claims"}. No `schema_version` — that envelope
    field is atlas's to stamp at the boundary (keeps this engine atlas-free).
    """
    mapped = map_claims_against_brief(script, brief, chat_fn=chat_fn)
    claims = [finalize_claim(scene_no, c, mapped.get(c.get("claim_id"), {}), brief,
                             chat_fn=chat_fn, quiet=quiet)
              for scene_no, c in iter_claims(script)]
    return {"verdict": verdict_for(claims), "summary": summarize(claims), "claims": claims}
