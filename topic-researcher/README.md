# Sage — The Researcher

The **second agent** in the YouTube pipeline. Scout finds a viral *topic*; Sage
investigates it. He's a rigorous, skeptical fact-checker: he gathers sources,
validates claims by independent corroboration, and separates **verified truth**
from **current news** from **myths** — then hands the next agent (a future
script-writer) a structured **research pack**.

Sage is a **self-contained project**. He has his own persona, his own state, his
own memory, and his own search seam. He does not import from or share runtime
state with Scout (`youtube-topic-agent/`). He runs standalone.

> Status: **Phase 1 (research engine) and Phase 2 (co-worker chat) are both
> complete and verified.** See [Roadmap](#roadmap) and [Changelog](#changelog).

---

## Who he is (the persona)

Sage (`SOUL.md`) is calm, precise, and source-obsessed. He cares about getting it
**right**, not about being exciting — that's the script writer's job later.

- One source is a *lead*, not a *fact*. He needs independent corroboration.
- He ranks evidence: primary/authoritative (gov, academic, established outlets,
  encyclopedic baselines) over forums and SEO content.
- He's comfortable saying "this is a myth," "the evidence is thin," "sources
  disagree," or "I couldn't verify this." He never inflates confidence.
- He never fabricates a source, statistic, quote, or date; never presents a
  contested claim as settled; paraphrases and keeps quotes short and attributed.

## His method (`SKILL.md`)

1. **Decompose** the topic into the key sub-questions / load-bearing claims (his
   planning + decision step).
2. **Gather** — search *multiple* sources per sub-question; prefer primary and
   authoritative ones; actually fetch and read them.
3. **Validate** each claim by how many independent credible sources support it,
   and classify it:
   - **VERIFIED** — multiple independent credible sources agree
   - **CONTESTED / UNCERTAIN** — sources disagree or the evidence is weak
   - **MYTH / FALSE** — widely circulated but debunked by credible sources
   - **DEVELOPING** — recent, not yet settled
4. **Separate** fact from opinion from speculation; note recency.
5. **Capture** — quotes, statistics, a timeline, myths + corrections, open
   questions, suggested angles.
6. **Rules** — never a fact without a source; flag the unverified; never resolve
   on a single weak source.

## The research pack (the handoff interface)

Every run saves two files in `research_packs/`, keyed by topic + timestamp:

- `…-<timestamp>.json` — the structured handoff for the next agent.
- `…-<timestamp>.md` — a readable version for you.

The engine classifies each claim and **routes** it into the pack buckets
(VERIFIED → `verified_facts`, MYTH → `myths_and_corrections`, CONTESTED &
DEVELOPING → `contested_or_uncertain`). Final JSON shape:

```json
{
  "topic": "...", "angle": "...", "generated": "<ts>",
  "overview": "2-3 sentence neutral summary",
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

## Setup

```bash
cd topic-researcher
python -m venv venv && source venv/bin/activate   # or reuse the repo venv
pip install -r requirements.txt
cp .env.example .env        # then add your LLM key (see below)
```

### Keys: almost none needed

The **search sources are 100% free and keyless** by default:

| Seam | Default backend | Key? |
|------|-----------------|------|
| web  | DuckDuckGo (`ddgs`) | none |
| wiki | Wikipedia REST API | none |
| news | GDELT 2.0 (self-throttled, degrades gracefully) | none |
| fetch | `requests` + stdlib HTML→text extractor | none |

The **default setup needs no keys at all** — the brain runs on your Claude
subscription (see below).

## The seams (swap each in ONE place)

- **LLM seam — `llm.py`.** The default brain is **Claude on your Claude Code
  subscription** — no env var, **no API key** (do *not* set `ANTHROPIC_API_KEY`;
  if set, the SDK bills the metered API instead and `llm.py` warns you). Switch
  brains with the `SAGE_LLM` env var:
  - *(unset)* / `"claude"` (default) — Claude via the Agent SDK on your
    subscription. Research runs on `CLAUDE_MODEL = "opus"`; chat on
    `CHAT_MODEL = "claude-sonnet-4-6"`.
  - `"gemini"` — Google Gemini (free tier). `SAGE_LLM=gemini` + `GEMINI_API_KEY`.
  - `"deepseek"` — DeepSeek (OpenAI-compatible). `SAGE_LLM=deepseek` +
    `DEEPSEEK_API_KEY`. Wired but untested (no key on hand).
- **Search seam — `search.py`.** Change the single `WEB_BACKEND` constant (or set
  `SAGE_SEARCH`) to swap web search: `"ddgs"` (default, free) → `"tavily"` or
  `"brave"` (higher quality, free tier, needs a key). Wikipedia + GDELT + page
  fetch sit behind the same module and are each wrapped so a flaky source returns
  empty rather than crashing a run.
- **Search seam — `search.py`.** Change the single `WEB_BACKEND` constant (or set
  `SAGE_SEARCH`) to swap web search: `"ddgs"` (default, free) → `"tavily"` or
  `"brave"` (higher quality, free tier, needs a key). Wikipedia + GDELT + page
  fetch sit behind the same module and are each wrapped so a flaky source returns
  empty rather than crashing a run.

---

## Usage

```bash
# Research a topic (prints progress, saves JSON + Markdown):
python run.py research "James Webb Space Telescope discoveries"

# With an angle handed off from Scout:
python run.py research "ozempic" --angle "is it safe long-term?"

# From a handoff file {"topic": "...", "angle": "..."}:
python run.py research --handoff handoff.json

# Talk to Sage (co-worker mode):
python run.py chat
```

### Co-worker chat (`python run.py chat`)

Talk to Sage like a colleague: discuss a topic, push back, ask him to dig deeper.
He speaks in his own voice (persona from `SOUL.md` only — *not* the research-pack
format), and he can run a **real research job mid-conversation**.

- **Mid-chat research with your approval.** When Sage decides real data would help,
  he calls his `research_topic` tool — and you're asked first:
  `🔍 Sage wants to research '<topic>'. Run it? [y/N]`. Approve and it runs the full
  engine (saving a pack to `research_packs/`), then he discusses the findings in
  character. Decline and he keeps talking.
- **Commands:**
  - `/research <topic>` — run a research job now and talk it through
  - `/summary` — distill the session so far and show what Sage remembers
  - `/new` — distill, then start a fresh thread (keeps what he knows about you)
  - `/help`, `/exit`
- **Graceful exit:** `/exit`, `Ctrl+D`, or `Ctrl+C` all save before quitting; a
  second `Ctrl+C` during the save force-quits but still parks your chat safely.

---

## Memory model

- **Engine memory — `memory.json`:** a simple log of past research runs (topic,
  angle, timestamp, source count). Written atomically; tolerant of corruption.
- **Chat memory — `chat_state.json`:** across sessions, Sage's only long-term
  memory is a single **distilled summary** — what topics you work on, your
  standards, decisions, useful findings — *not* the word-for-word transcript. The
  live transcript stays in RAM during a session and is distilled into the summary
  at every session boundary (`/exit`, `Ctrl+C`, `/new`, `/summary`); if a distill
  fails, the raw transcript is parked under `pending` so nothing is lost and folded
  in on the next launch.

All state lives in **Sage's own files** — never a provider session id — so the
brain can be swapped without losing memory.

---

## Tests

Pure-unit, no network, no API keys (search + LLM are mocked):

```bash
# Phase 1 — the engine
python tests/test_researcher.py     # validation, claim routing, assembly, full run
python tests/test_chat_state.py     # atomic writes, corruption recovery, state loading
python tests/test_search.py         # search seam degrades gracefully; credibility; extractor
# Phase 2 — the chat co-worker
python tests/test_distill_memory.py # distill on /exit·/new·/summary·SIGINT; pending fallback; load-summary-only
python tests/test_chat_trigger.py   # strict marker; pack digest; approval gate (y/N)
python tests/test_system_prompt.py  # persona present, research output contract absent
python tests/test_compaction.py     # in-session budget guard folds/fits correctly
```

42 unit tests, all green. **Honestly manual / integration** (not unit-tested): real
search *quality*, real LLM *classification accuracy*, the live native-tool round
trip, and real cross-session recall. Verify those with a real `run.py research …`
and a real `run.py chat`.

---

## Roadmap

- **Phase 1 — core engine ✅** persona, method, LLM + search seams, the pack
  schema, JSON+Markdown output, `run.py research`, unit tests, a verified real run.
- **Phase 2 — co-worker chat ✅** `python run.py chat`: a persona REPL with the
  summary-only memory model, distill on `/exit`·`Ctrl+C`·`/new`·`/summary`, and a
  mid-chat research trigger gated by your approval.
- **Future (not built):** a script-writer agent that consumes the pack, and a
  "more on X" feedback loop. The pack is structured to support these later.

## Project layout

```
topic-researcher/
├── SOUL.md            # Sage's identity / voice
├── SKILL.md           # the fact-validation method + output contract
├── llm.py             # LLM seam: chat() (Claude subscription default) + converse() (chat)
├── search.py          # search/fetch seam: web + wiki + news + fetch + credibility
├── researcher.py      # the engine: decompose → gather → classify → assemble → save
├── chat.py            # the co-worker REPL: persona chat, distill memory, research tool
├── compaction.py      # in-session prompt-budget guard for chat
├── run.py             # CLI: research / chat
├── chat_state.py      # atomic, corruption-tolerant state (engine + chat memory)
├── memory.json        # engine run log (generated)
├── chat_state.json    # distilled chat memory (generated)
├── research_packs/    # saved packs: <slug>-<timestamp>.{json,md} (generated)
├── requirements.txt
├── .env.example
└── tests/             # 42 unit tests (no network/API)
```

## Changelog

- **Phase 2 — co-worker chat.** Added `chat.py` (persona REPL built from `SOUL.md`
  only; summary-only cross-session memory; distill on every session boundary with a
  no-data-loss `pending` fallback; a `research_topic` SDK tool gated by a `[y/N]`
  approval callback, with a strict `SAGE_REQUEST:` marker fallback; `/research`,
  `/summary`, `/new`, `/help`, `/exit`; graceful SIGINT) and `compaction.py`
  (in-session budget guard). Added 21 unit tests. Wired `run.py chat`.
- **Phase 1 — research engine.** `SOUL.md`, `SKILL.md`, the LLM seam (`llm.py`,
  Claude-subscription default with Gemini/DeepSeek swaps), the new search/fetch
  seam (`search.py`: ddgs + Wikipedia + GDELT + fetch + credibility), the engine
  (`researcher.py`: decompose → gather → classify → route → assemble → save
  JSON+Markdown), `run.py research`, and 21 unit tests. Verified with a real run on
  "James Webb Space Telescope discoveries".
