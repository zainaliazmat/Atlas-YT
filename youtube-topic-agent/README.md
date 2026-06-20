# Viral Scout — a YouTube viral-topic research agent

Give it a niche; it researches YouTube and returns the ~10 best topics to make next,
ranked, each with title options, an angle, a thumbnail concept, and the data signal
behind it. It learns from what actually works for you over time.

Data comes from the YouTube Data API v3 (free daily quota). The default brain is
**Claude Opus 4.8** via the **Claude Agent SDK**, running on your **Claude
subscription** (no `ANTHROPIC_API_KEY` — calls draw from your Pro/Max plan, not the
metered API). The conversational mode (below) runs on **Claude Sonnet 4.6** to spare
your rate limit. All LLM wiring is in [llm.py](llm.py), with one `SAGE_LLM` switch to
swap in **Gemini** (free tier) or **DeepSeek** as alternatives.

---

## How it maps to what you asked for

- **Its own soul + skill** → `SOUL.md` is the agent's identity; `SKILL.md` is the method
  it follows. Both get loaded into the model's context on every run, so it stays in
  character and on-method.
- **Makes its own decisions** → before burning API quota, the agent decides which search
  queries are most worth investigating (`plan_queries` in `agent.py`).
- **Auto-improves** → `memory.json` stores past runs and your recorded "wins." Each run
  reads your wins and leans toward what's proven *in your niche*. It learns from results —
  it doesn't retrain a model (that part isn't realistically buildable), but this feedback
  loop genuinely sharpens its picks.

---

## Project layout

The code lives in this `youtube-topic-agent/` folder. The Python virtual
environment (`venv/`) and your secret `.env` live one level up, in the
`YT-AGENTS/` parent folder:

```
YT-AGENTS/
├── .env                  # your real keys (git-ignored; never committed)
├── .gitignore
├── venv/                 # virtual environment (git-ignored)
└── youtube-topic-agent/  # ← the project; run commands from here
    ├── SOUL.md  SKILL.md           # Scout's identity + research method
    ├── llm.py                      # the LLM seam: chat() (research) + converse() (chat)
    ├── youtube.py  agent.py        # data gathering + research orchestration
    ├── chat.py                     # Talk-to-Scout REPL (persona, tool+approval, commands)
    ├── chat_state.py  compaction.py# durable summary-only memory + context-budget compaction
    ├── run.py                      # CLI entry: research | chat | win
    ├── requirements.txt  README.md
    └── tests/                      # pure-unit tests (no API): scoring, state,
                                    # compaction, scout-trigger, system-prompt
```

Run all commands from **inside `youtube-topic-agent/`**. The agent finds the
`.env` automatically by walking up to the parent folder.

## Setup (about 5 minutes)

1. **Get a YouTube Data API key** (free): Google Cloud Console → create a project →
   APIs & Services → enable **YouTube Data API v3** → Credentials → create API key.
2. **The brain needs no key** — the default runs on your Claude Code subscription.
   (Only switch providers if you want to; see *Swapping the brain* below.)
3. Install deps (the venv already exists in the parent folder):
   ```
   cd youtube-topic-agent
   ../venv/bin/python -m pip install -r requirements.txt
   ```
4. Add your key — create `.env` in the **parent** `YT-AGENTS/` folder:
   ```
   cp .env.example ../.env      # then edit ../.env and paste your YouTube key
   ```
   (If `../.env` already exists, leave it — your keys are already there.)

## Run it

From inside `youtube-topic-agent/`:

```
../venv/bin/python run.py "faceless finance youtube channels"
```

Teach it when something works (do this after you post and see results):

```
../venv/bin/python run.py win "I tried index investing for 30 days"
```

Future runs will then weight toward that kind of winner.

> Tip: run `source ../venv/bin/activate` once and then plain `python run.py "..."`
> works for the rest of your terminal session.

### Sharper outliers: `--deep`

```
../venv/bin/python run.py --deep "faceless finance youtube channels"
```

The default run measures outliers as `views / channel_subscribers` — fast, but a
rough proxy. `--deep` instead measures each candidate against **how that channel
normally performs**: `median_outlier = views / channel's median recent views`.
To build that baseline it pulls each channel's last ~20 uploads and takes the
median, **excluding** the candidate itself, anything newer than 14 days (too new
to have matured), and Shorts (so the baseline reflects long-form). Channels with
fewer than 3 usable recent uploads fall back to the old subs-ratio for that video,
and **both** numbers are kept in every result for transparency.

Baselines are cached in **`channel_cache.json`** (median + timestamp per channel,
refreshed after 7 days), so repeat `--deep` runs don't re-fetch the same channels.
The extra calls cost only ~40 quota units per run; the flag is opt-in so quick
runs stay cheap. The `--deep` flag can appear anywhere on the line.

### Google Trends (a free bonus layer)

Each run also layers in **Google Trends** (via the unofficial `pytrends`): *rising*
related searches feed extra candidate angles into query planning, and the niche's
*trend direction* (rising / flat / falling) is passed to the agent so it can weight
timing and flag topics that are trending up. Results are cached in
**`trends_cache.json`** (refreshed daily) to stay under the rate limit.

Trends is **best-effort**: `pytrends` is unofficial and gets rate-limited, so every
call degrades gracefully — on any failure it returns empty/"unknown", prints a short
note, and the run continues on YouTube signals alone. It never crashes a run. To
turn it off entirely, set `VIRAL_SCOUT_TRENDS=0` in your environment.

---

## Talk to Viral Scout (conversational mode)

Instead of just running a job, you can *talk* to Scout as a person — same soul and
expertise — and he can run a real research job mid-conversation when it'd help.

```
../venv/bin/python run.py chat
```

**Commands** (anything else is just conversation):

| Command | What it does |
|---------|--------------|
| `/scout <niche>` | run a research job now and have Scout talk it through |
| `/summary` | distill the session so far into memory, then **show** what Scout remembers (a real, manually-triggerable checkpoint) |
| `/new` | distill this session into memory, then start a fresh thread — **keeping** what matters about you |
| `/help` | list commands |
| `/exit` | distill the session into memory, save, and quit |

**How research-in-chat works.** Scout decides when real data would help and calls an
in-process `scout_research` tool. You're always asked first — **"🔍 Scout wants to
research '<niche>'. Run it? [y/N]"** — and nothing runs until you approve. Approve and
he runs the normal pipeline and discusses the findings in his own voice; decline and he
just keeps talking.

**Memory model — a distilled summary, not a transcript.** Across sessions, Scout's only
long-term memory is a single **distilled summary** in `chat_state.json` (git-ignored):

```json
{ "summary": "<durable context about you>", "updated": 1781900000 }
```

The raw word-for-word chat is **not** kept between sessions. During a live session the
full transcript stays in **RAM** so Scout has normal working memory of the current
conversation; it never touches disk on its own. On every session boundary one helper —
`distill(existing_summary, transcript)` in [chat.py](chat.py) — folds the whole session
into the summary, **keeping** durable signal (your channel/identity, niche and sub-angles,
audience, upload cadence, style/preferences like "hates clickbait", decisions, and
research wins) and **dropping** the junk (greetings, small talk, off-topic detours,
jailbreak/identity tests). It **merges** with the existing summary rather than replacing
it — knowledge accumulates, and contradictions resolve in favor of the most recent info.
The summary is kept bounded and consolidated (soft cap ~400–600 words). Distillation runs
through the provider-agnostic `llm.chat()` seam, so it's cheap and portable.

`distill` fires in four places: **`/exit`**, **Ctrl+C** (SIGINT), **`/new`**, and
**`/summary`**. On launch Scout loads **only** the summary (plus a small capped snapshot of
your research history from `memory.json`) and starts a fresh, empty transcript — no replay.

**No data loss on exit.** Saving on exit is a quick **synchronous** step ("💾 Saving
session summary…") with a timeout — not a detached background process (a child spawned
during shutdown can be killed before it finishes). If the distill call fails or times out,
the raw transcript is parked under a `"pending"` key in `chat_state.json` instead of being
dropped; the **next launch** folds `"pending"` back into the summary and clears it. A
second Ctrl+C during the save force-exits immediately, but still flushes the raw transcript
to `"pending"` first. A Ctrl+C during a research run aborts cleanly; `memory.json` is
written atomically so it can't be corrupted mid-run.

**What Scout says about his memory.** His chat persona now describes this accurately —
"I keep a distilled summary of what matters about you across sessions … but not the
word-for-word history" — so he no longer claims to start fresh every time (because he
doesn't).

**Context budget + compaction.** Within a session, every turn is still kept under a
conservative, configurable token budget (`BUDGET_TOKENS` in [chat.py](chat.py), default
~6,000) so the live prompt never grows unbounded — sized so even a small ~8k-context model
has room to answer. If the in-RAM transcript would exceed it, older turns are **compacted**
into the summary and only the last few raw turns are sent verbatim; the session-end distill
then consolidates everything. If one message is too big to fit even after compaction, Scout
asks you to `/new`.

**Provider portability.** The durable summary in `chat_state.json` is plain and
provider-independent, and `chat()` already swaps across Claude / Gemini / DeepSeek via
the `SAGE_LLM` switch. Only the *send* step (`converse()` in `llm.py`) is Claude-specific;
on a brain without tools the native research tool becomes the `SCOUT_REQUEST:` marker
fallback in chat.py, and the same saved memory still works.

---

## How it works (the pipeline)

1. **Expand** — pulls YouTube autocomplete for your niche (real searches, free) and,
   best-effort, Google Trends *rising* queries for more candidate angles.
2. **Plan** — the brain picks the 6 most promising queries to investigate.
3. **Gather** — searches YouTube, pulls each video's stats + each channel's sub count
   (and, with `--deep`, each channel's median-recent-views baseline).
4. **Score** — computes the signals:
   - `median_outlier = views / channel median recent views` (`--deep`; the sharpest
     outlier signal) **or** `subs_ratio = views / channel_subs` (fast fallback)
   - `views_per_day` (velocity — what's hot now)
5. **Analyze** — the brain applies `SKILL.md` to the ranked data (plus the niche's
   Trends direction) and writes topic ideas, flagging ones trending up.
6. **Remember** — saves the run; your wins feed back into step 2 and step 5.

## Quota notes
The free YouTube quota is ~10,000 units/day. A search costs 100 units; stats calls cost 1.
A default run uses ~600 units (≈ **15 runs/day** free). `--deep` adds only ~40 units/run
(channel baselines, cached for 7 days). Google Trends is free and doesn't touch the
YouTube quota at all.

## Swapping the brain
All LLM wiring lives in `llm.py` — the only place to change providers. The default
brain is **Claude on your Claude Code subscription** (no key). Models are separate,
configurable constants at the top of `llm.py`:
- **Research** runs on `CLAUDE_MODEL = "opus"` (Claude Opus 4.8) via the Agent SDK on
  your subscription, through the `chat(system, user)` seam.
- **Chat** runs on `CHAT_MODEL = "claude-sonnet-4-6"` through the `converse(...)` seam.

Swap the `chat()` brain with the **`SAGE_LLM`** env var (one switch):
- *(unset)* / `claude` (default) — your Claude subscription. `ANTHROPIC_API_KEY` must
  stay **unset**; if set, the SDK bills the metered API and `llm.py` warns you.
- `gemini` — Google Gemini (free tier). `SAGE_LLM=gemini` + `GEMINI_API_KEY`.
- `deepseek` — DeepSeek (OpenAI-compatible, raw `requests`). `SAGE_LLM=deepseek` +
  `DEEPSEEK_API_KEY`. Wired but untested (no key on hand).

Nothing else changes — including the saved chat in `chat_state.json`.

---

## Known limits & easy upgrades

- **Outlier accuracy:** ✅ *done* — run with `--deep` to divide a video's views by its
  channel's *median recent views* instead of its subscriber count (see **Sharper
  outliers: `--deep`** above). The default fast run still uses the `views/subs` proxy.
- **Hidden subscriber counts:** channels that hide subs are skipped in fast mode (can't
  trust the ratio); under `--deep` they can still be ranked by the median baseline.
- **Deeper demand data:** ✅ *done* — Google Trends (via free `pytrends`) now feeds rising
  searches into planning and a trend direction into synthesis (best-effort; see above).
- **More coverage:** raise `per_query` or the number of planned queries (watch your quota).
- **Free-model names change:** if you run `SAGE_LLM=gemini` and Gemini errors on the model
  name, check Google AI Studio for the current free-tier model and update `GEMINI_MODEL`
  in `llm.py`.
