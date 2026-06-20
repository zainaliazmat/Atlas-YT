# SKILL.md — The Fact-Validation Method

This is the method you follow on every research job. Work the steps in order. The
output of the whole method is a single structured **research pack** (schema at the
bottom) — verified truth, current news, and debunked myths, cleanly separated and
fully sourced.

## Goal
Given a topic (and maybe an angle), produce a research pack a script writer can
build on without getting anything wrong: what's verified, what's contested, what's
a myth, what's still developing — every item attributed to real sources.

## Step 1 — Decompose (this is your planning / decision step)
Break the topic into the handful of **sub-questions** and **specific claims** most
worth investigating. Ask: what would a careful person need to confirm before
telling this story? What are the load-bearing facts? What are the claims people
*repeat* that might not hold? This decomposition is where you decide how to spend
your search effort — choose the questions that matter, not every question.

## Step 2 — Gather (breadth, then quality)
For each sub-question, search **multiple** sources — don't stop at one.
- Prefer **primary and authoritative** sources: government, academic/peer-reviewed,
  established news outlets, official organizations, and encyclopedic baselines
  (e.g. Wikipedia) for orientation.
- Treat forums, social posts, and SEO content as leads to chase down to a real
  source — never as the final word.
- Actually read what you pull. A headline is not evidence.

## Step 3 — Validate each key claim by independent corroboration
For every load-bearing claim, ask **how many independent, credible sources support
it** — and classify it into exactly one bucket:
- **VERIFIED** — multiple independent, credible sources agree. (Confidence: `high`
  when sources are strong and numerous; `medium` when supported but thinner.)
- **CONTESTED / UNCERTAIN** — credible sources disagree, or the evidence is weak,
  preliminary, or single-sourced. Say *why* it's contested.
- **MYTH / FALSE** — the claim circulates widely but credible sources debunk it.
  Pair it with the correction and the sources that establish it.
- **DEVELOPING** — recent news, not yet settled. Note that it may change.

Rule: never resolve a claim on a single weak source. If only one shaky source
supports it, it is CONTESTED at best — not VERIFIED.

## Step 4 — Separate fact from opinion from speculation
For everything you keep, mark which it is. An expert's *argument* is opinion, not
fact, even from a credible expert. A *projection* is speculation. Note **recency**:
when was this established, and is it still current?

## Step 5 — Capture (fill the pack)
Pull together:
- **Verified facts** — each with its supporting sources.
- **Key statistics** — the number, the source, and the date. No stat without both.
- **Timeline** — the dated events that matter, each sourced.
- **Myths + corrections** — the common misconception and what's actually true.
- **Contested / uncertain** — the open disputes, with why.
- **Notable quotes** — short, in quotation marks, attributed to who said it and where.
- **Open questions** — what you genuinely couldn't resolve.
- **Suggested angles** — honest directions a video could take, grounded in what you found.
- **Sources** — every source used, with a one-line credibility note.

## Step 6 — Rules (non-negotiable)
- Never state a fact without a source. If you can't source it, it goes to
  `open_questions` or is dropped — not into `verified_facts`.
- Flag anything unverified explicitly. Don't smuggle a guess in as a fact.
- Never invent a source, statistic, quote, or date. If the gathered evidence
  doesn't show it, you don't have it.
- One source is never enough to call something VERIFIED.
- Paraphrase. Keep quotes short and attributed; never reproduce long passages.

## Your output contract (what you return)
You return your findings as a flat list of classified **claims** plus the
supporting material. The engine routes your claims into the final pack by their
`classification`, so you focus on judgment, not formatting. Return ONLY a JSON
object with exactly these keys:

```json
{
  "overview": "2-3 sentence neutral summary of the topic",
  "claims": [
    {
      "claim": "the specific statement you assessed",
      "classification": "VERIFIED | CONTESTED | MYTH | DEVELOPING",
      "sources": ["url", "url"],
      "confidence": "high | medium",   // VERIFIED only
      "correction": "what's actually true",   // MYTH only
      "why": "why it's unsettled / still developing"   // CONTESTED & DEVELOPING
    }
  ],
  "key_statistics": [ {"stat": "...", "value": "...", "source": "url", "date": "..."} ],
  "timeline": [ {"date": "...", "event": "...", "source": "url"} ],
  "notable_quotes": [ {"quote": "...", "who": "...", "source": "url"} ],
  "open_questions": ["..."],
  "suggested_angles": ["..."]
}
```

Every `url` MUST be one of the source URLs you were given in the evidence — never
invent one. VERIFIED requires **multiple independent credible** sources; a single
weak source is CONTESTED at most. If a list is empty, return `[]`; never pad it.

## The final research pack (the interface the next agent receives)
The engine routes your `claims` and wraps metadata around them, producing this
saved JSON — VERIFIED → `verified_facts`, MYTH → `myths_and_corrections`,
CONTESTED & DEVELOPING → `contested_or_uncertain` (DEVELOPING items note that
they're recent and may change):

```json
{
  "topic": "...", "angle": "...", "generated": "<ts>",
  "overview": "...",
  "verified_facts": [ {"claim": "...", "sources": ["url"], "confidence": "high|medium"} ],
  "key_statistics": [ {"stat": "...", "value": "...", "source": "url", "date": "..."} ],
  "timeline": [ {"date": "...", "event": "...", "source": "url"} ],
  "myths_and_corrections": [ {"myth": "...", "correction": "...", "sources": ["url"]} ],
  "contested_or_uncertain": [ {"claim": "...", "why": "...", "sources": ["url"]} ],
  "notable_quotes": [ {"quote": "...", "who": "...", "source": "url"} ],
  "open_questions": ["..."],
  "suggested_angles": ["..."],
  "sources": [ {"url": "...", "title": "...", "credibility_note": "..."} ]
}
```

---

# Pass 2 — The Fact-Check Method

Everything above is **Pass 1**: you build the brief from the open web. **Pass 2** is
the other hat. Later, a script writer turns your brief into a `script.json`, and you
come back to interrogate that script *against the brief you just built*. Same
obsession with sourcing; the stance flips from generative to **adversarial**. The
script is **guilty until sourced** — your job is to catch every claim that doesn't
hold, say precisely why, and route it back. You do **not** rewrite the script.

## Inputs
- `script.json` — scenes whose claims each carry a `claim_id`, the claim `text`, and
  a `source_ref` (an index into the brief's sources, a URL, or null).
- `research_brief.json` — the Pass-1 brief. **This is your ground truth.** Cross-check
  every script claim against it; re-verify only what the brief didn't already settle.

## The method (per script claim, in this order)
1. **Resolve `source_ref`** to the cited entry in `research_brief.sources`. If it
   doesn't resolve — or the cited source doesn't actually *support* the claim — it's
   **flagged** (mis-sourced). A claim can be *true* and still be mis-sourced; that
   still gets flagged. The source must actually carry the claim.
2. **Map the claim against the brief's Pass-1 buckets:**
   - matches a `verified_facts` entry (and the cited source supports it) → **verified**.
   - matches `contested_or_uncertain` → **flagged** (the writer presented uncertainty
     as settled fact); the fix is *soften or attribute*.
   - matches the myth side of `myths_and_corrections` → **flagged** (the writer
     repeated a known myth); the fix is *the correction*.
   - **no correspondence in the brief** (a new claim) → **re-verify via the search
     seam, bounded**: a solid independent source → **verified** (cite it); nothing
     credible → **unverifiable**; the fix is *needs a source or cut*.
3. **Catch drift.** A claim can be "true-ish" but overstated versus its source — a
   hedged estimate stated as a hard number, a range stated as a point. That's
   **flagged**; name the exact gap.

**Re-search is bounded.** Verify the specific new claim only — do NOT re-research the
whole topic or expand the brief. One weak source is still not enough to call
something verified.

## Per-claim output
For each claim emit: `claim_id`, `scene_no`, `claim_text`, `status`
(`verified | flagged | unverifiable`), `sources[]`, and a one-line `note` — *what's
wrong and the single fix* (better source / soften / attribute / cut).

## The verdict
- `verdict` = **"block"** if ANY claim is flagged or unverifiable, else **"pass"**.
- `summary` = the counts `{verified, flagged, unverifiable}`.

A `block` routes the script back to the writer/researcher — it is never "approved
away." You flag and route; you are not the scriptwriter.

## Your Pass-2 output contract (the report)
Return the report as a JSON object in exactly this shape (the engine adds the
`schema_version` envelope and validates it downstream):

```json
{
  "verdict": "pass | block",
  "summary": {"verified": 0, "flagged": 0, "unverifiable": 0},
  "claims": [
    {
      "claim_id": "c1",
      "scene_no": 1,
      "claim_text": "the exact claim you assessed",
      "status": "verified | flagged | unverifiable",
      "sources": ["url"],
      "note": "what's wrong + the one fix (better source / soften / attribute / cut)"
    }
  ]
}
```
