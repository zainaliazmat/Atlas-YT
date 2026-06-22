# Atlas — the Showrunner

Atlas is the **head of your agent fleet**: a single "meeting room" chat where you (the
CEO) talk to a calm showrunner that coordinates an 8-agent video studio and runs it
through a deterministic production pipeline that turns a topic brief into a finished,
narrated, fact-checked explainer video.

The fleet (all 8 built, all with real engines):

| Agent | Persona | Role |
|---|---|---|
| `youtube-topic-agent` | Viral Scout 🔎 | finds ranked viral topic ideas |
| `topic-researcher` | Sage 📚 | research pack + script fact-check |
| `scriptwriter` | Marlow 📝 | brief → one-point-per-scene script |
| `art-director` | Iris 🎨 | style guide + storyboard |
| `asset-sourcer` | Magpie 🗂️ | license-cleared asset manifest |
| `audio-designer` | Cadence 🎙️ | narration (TTS) + documentary mix |
| `composition-engineer` | Mason 🛠️ | artifacts → HyperFrames render → `video.mp4` |
| `reference-analyst` | Vera 🔬 | builds a `reference_rubric` from reference videos (a standalone job, **not** a pipeline stage) |

You talk to Atlas. Atlas decides who to send in, runs them, and brings the answer back
as a clear call. The existing per-agent chats still work for debugging; Atlas's room
(terminal REPL or the Chainlit **web operator UI**) is the main interface now.

```
You: find me research on a viral topic in home espresso

Atlas: On it — sending Scout in on 'home espresso'.
🔎 Viral Scout is scanning 'home espresso'…
✅ Viral Scout returned 8 topic ideas
🧠 I'm going with 'the $200 machine that beats the $1000 one' because it's Scout's top
   outlier and the gear-myth framing travels. Handing it to Sage.
📚 Sage is researching 'budget vs premium espresso machines'…
✅ Sage finished the research pack
Sage is back. The core claim holds — three credible sources show sub-$300 machines
matching premium shots in blind tests. One myth to bust on camera: "more bars = better
espresso" is false. Full pack saved under topic-researcher/research_packs/. Want me to
lock this in, or have Scout pull a second option to compare?
```

---

## The supervisor pattern

Atlas is a **supervisor with a registry**, not a hardcoded router. Three pieces:

1. **Registry** (`registry.py`) — the one place that declares who Atlas can delegate to.
   Each managed agent is ONE entry: name, a one-line blurb, its capabilities (jobs +
   persona), the adapter that wraps it, and the sibling project directory.

2. **Adapters** (`adapters/`) — each managed agent is wrapped behind a uniform
   interface **without touching that agent's code**:
   - a **JOB** (`run_job`) that calls the agent's engine **in-process** and returns a
     compact digest (Scout: `find_topics(niche)`; Sage: `research(topic, angle)`).
   - a **PERSONA** (`ask`) that loads the agent's `SOUL.md` + `STYLE.md` and gives a
     single-turn, in-character reply through Atlas's LLM seam.

3. **Orchestrator** (`orchestrator.py`) — Atlas runs on the Claude Agent SDK. The
   registry's capabilities are exposed as **tools generated FROM the registry**
   (`scout_find_topics`, `sage_research`, `scriptwriter_write_script`, …,
   `reference_analyst_build_rubric`, `ask_<agent>` for each), plus the single
   `produce_video` tool that runs the whole pipeline. Atlas's LLM decides which to call
   and in what order, streaming its reasoning as it goes.

```
              ┌──────────── you (CEO) ────────────┐
              │   chat.py (REPL)  /  web/app.py    │
              └───────────────┬───────────────────┘
                       orchestrator.py
                   (Claude Agent SDK query loop)
                              │  tools generated from…
                         registry.py ──── adapters/ (in-process, isolated)
                              │
                 ┌────────────┴─────────────┐
            ask_<agent> / <agent>_<job>   produce_video
                                              │
                                         pipeline.py
                              (deterministic spine: order + contract
                               validation + auto-gate + two human gates)
                                              │
        Scout · Sage · Marlow · Iris · Magpie · Cadence · Mason  (+ Vera, off-pipeline)
                                  → video.mp4
```

### Why in-process (and how it's safe)
Both siblings ship modules with the **same names** (`llm`, `chat_state`, `search`,
`youtube`). `adapters/loader.py` imports each engine with its module graph isolated
(snapshot `sys.path`/`sys.modules`, drop the colliding names, load, restore), caches it
**load-once**, and guards the global mutation with a **thread lock**. Result: both
engines run in one process, each bound to its own `llm` — verified by tests. The
synchronous engines spin their own event loop, so jobs are dispatched via
`asyncio.to_thread` to avoid nesting with the SDK's loop.

---

## The production pipeline (make me a video)

`produce_video` runs `pipeline.py` — a **deterministic state machine**, deliberately
separate from Atlas's LLM judgment. The conversational plane picks topics and talks to
you; the spine GUARANTEES stage order, validates every artifact against a frozen
JSON-Schema contract (`contracts/`) before advancing, and halts on a failed gate.

Ten stages, one fixed order, two human gates (★) and one automatic gate (▲):

```
research → script → factcheck ★GATE → style → storyboard
   → assets ∥ narration → compose ▲auto-gate → audiomix → render ★GATE → video.mp4
```

- **All 10 stages bind real engines.** Only `research` keeps an opt-in offline stub
  fallback, behind the `ATLAS_RESEARCH_STUB` env flag (dev / no-network); by default it
  runs Sage's real engine like every other stage.
- **Two human gates as pause-and-resume:** at the **fact-check gate** and the
  **final-render gate** the runner persists `status: blocked_at_<gate>` to
  `project.json` and returns — it never blocks mid-tool. You sign off, Atlas re-invokes
  `produce_video` with `approve=<gate>`, and the pipeline resumes where it left off
  (done stages are skipped). A fact-check `block` verdict **can't be approved away** —
  it routes back upstream until the script is fixed.
- **The compose auto-gate (▲):** before spending a render, Mason self-scans each scene
  and runs HyperFrames `lint` + `validate` + `inspect`; a scene that fails blocks the
  stage.

Run it: `python run.py produce "<brief>"`. In the **web operator UI** the two gates
render as **Approve / Revise** buttons with inline artifact/media previews.

---

## Adding a future agent

The orchestrator never changes. You add **one registry entry + one adapter class**.

**Honest effort estimate:**
- A **persona-only** agent (just `ask`, no job): ~30 lines — a registry entry plus a
  tiny adapter that inherits `ask` from `adapters/base.Adapter`.
- A **job agent** that joins the canonical flow: ~80–150 lines — the adapter must map
  the agent's specific engine signature into `run_job`, emit progress lines, and you
  add one line to the playbook in `orchestrator.py` if it should participate in the
  default Scout→decide→Sage flow. (Direct address and `/ask` work with zero playbook
  changes.)

**Worked example — add "Pixel", a thumbnail critic:**

```python
# adapters/pixel.py
from adapters.base import Adapter

class PixelAdapter(Adapter):
    module_name = "critic"          # pixel-agent/critic.py, with run(concept) -> dict

    def run_job(self, job_name, progress, **params):
        concept = (params.get("concept") or "").strip()
        progress.start(self.entry.emoji, self.entry.display, "sketching", concept)
        result = self.engine().run(concept)          # in-process engine call
        progress.done(self.entry.display, "returned thumbnail directions")
        return {"ok": True, "text": result["summary"]}
```

```python
# registry.py — append to REGISTRY
AgentEntry(
    name="pixel", display="Pixel", emoji="🎨",
    blurb="Critiques and sketches thumbnail directions for a concept.",
    project_dir=str(_ROOT / "pixel-agent"),
    adapter_cls=PixelAdapter,
    jobs=[JobSpec(name="critique", tool="pixel_critique",
                  description="Sketch thumbnail directions for a video concept.",
                  params={"concept": str})],
)
```

That's it — `pixel_critique` and `ask_pixel` now appear as tools, Atlas can route to
them, and `/agents` lists Pixel. (Tests confirm a new entry surfaces its tools with no
orchestrator change.)

---

## The meeting room (commands)

```
python run.py chat
```

| Command | What it does |
|---|---|
| *(any message)* | Goes to Atlas — it delegates, routes a direct address, or answers itself. |
| `/agents` | Who's on the team, what each does, and each agent's effective provider. |
| `/ask <agent> <question>` | Ask one agent directly (deterministic routing, bypasses the LLM). E.g. `/ask sage is ozempic safe long-term?` |
| `/summary` | Distill the meeting so far, then show what Atlas remembers. |
| `/new` | Distill + start a fresh thread (keeps what Atlas knows about you). |
| `/help` | Show the command list. |
| `/exit` (`/quit`) | Save (distill) and leave. |

Atlas is **autonomous but transparent**: it picks the topic itself and does NOT gate
every step with `[y/N]`, but it announces each decision (`🧠 I'm going with 'X'
because…`) so you can redirect ("no, research #2 instead"). Status lines (🔎/📚/✅) are
deterministic, emitted from inside the tools; decisions and synthesis are Atlas's words.

### One-shot (prove the orchestration without the chat)
```
python run.py "AI tools & productivity for professionals and business"
```
Runs the canonical research flow once and prints it: Scout finds topics → Atlas decides
& says why → Sage researches → Atlas reports.

### Produce a video (the full pipeline)
```
python run.py produce "<brief>" [--unattended]
python run.py produce "" --resume <slug> --approve factcheck   # resume after sign-off
```

### Web operator UI (the second frontend)
```
pip install -r requirements-web.txt     # chainlit (terminal needs none of it)
chainlit run web/app.py -w               # → http://localhost:8000
```
The same meeting room as a web app: streaming chat, the two gates as Approve / Revise
buttons, inline artifact/media previews, a roster sidebar, and per-agent persona chat.
It drives the same `session.py` core — no orchestrator or pipeline changes.

---

## Memory

Atlas's long-term memory is **a single distilled summary** (`chat_state.json`), the same
model the rest of the fleet uses:

- During a meeting the full transcript lives in RAM; the orchestrator gets a **bounded**
  context each turn (summary + a recent window).
- On every boundary (`/exit`, Ctrl+C, `/new`, `/summary`) Atlas distills the meeting
  into the summary — keep durable signal (niches, decisions, your standards), drop the
  junk, merge with prior memory, stay bounded — then clears the transcript and persists
  **only the summary**.
- **No data loss:** if a distill fails or times out, the raw turns are parked under
  `pending` and folded in on the next launch.
- Atlas describes its memory honestly: a running summary of what matters, not a
  transcript.

State is **provider-agnostic** (our `chat_state.json`, never a Claude session id), so the
brain can be swapped and the saved memory still works.

---

## Provider policy

- **Default brain: Claude on your Claude Code subscription** via `claude_agent_sdk` — no
  API key. Do **not** set `ANTHROPIC_API_KEY` (it would bill the metered API; `llm.py`
  warns you). No Ollama.
- Switch Atlas's brain with **`ATLAS_LLM`**: `gemini` (needs `GEMINI_API_KEY`) or
  `deepseek` (needs `DEEPSEEK_API_KEY`).
- **Precedence:** `ATLAS_LLM` governs Atlas's own reasoning + persona `ask`. A delegated
  **job** runs inside the sibling's engine, which reads its **own** switch (`SAGE_LLM`),
  frozen at import. So "ask Scout" and "Scout does a job" can run on different providers
  — `/agents` surfaces Atlas's effective provider so it's never invisible.

---

## Setup

Atlas **depends on** its siblings and imports their engines in-process, so share one
environment at the repo root (the intended setup):

```
cd YT-AGENTS
python -m venv venv && source venv/bin/activate
pip install -r youtube-topic-agent/requirements.txt
pip install -r topic-researcher/requirements.txt
pip install -r scriptwriter/requirements.txt
pip install -r art-director/requirements.txt
pip install -r asset-sourcer/requirements.txt
pip install -r audio-designer/requirements.txt
pip install -r composition-engineer/requirements.txt
pip install -r atlas/requirements.txt
```

Actual rendering also needs Node ≥ 22 + the HyperFrames CLI (`npx hyperframes`),
FFmpeg/FFprobe on PATH, and the Kokoro TTS packages (`kokoro-onnx`, `soundfile`).

Keys live in the **shared root `.env`** (`atlas/.env.example` documents the rest):
- `YOUTUBE_API_KEY` — for Scout's job (free YouTube Data API v3 quota).
- Sage's default search needs **no key**. Atlas's default brain needs **no key**.

Then:
```
cd atlas
python run.py chat                       # the meeting room (primary)
python run.py "your niche here"          # one-shot research flow
python run.py produce "<brief>"          # the full video pipeline
python -m pytest tests/ -q               # pure-unit tests, no network
```

---

## Project layout

```
atlas/
  registry.py          # one entry per agent → who Atlas can delegate to (8 agents)
  adapters/
    loader.py          # in-process sibling import: isolated, cached, thread-safe
    base.py            # uniform adapter: run_job + persona ask
    scout.py sage.py scriptwriter.py art_director.py
    asset_sourcer.py audio.py composition_engineer.py reference_analyst.py
    stubs.py           # offline placeholder producers (research-stage fallback only)
  tools.py             # generates SDK tools FROM the registry (+ containment, timeout)
  pipeline.py          # the deterministic spine: stage order + contracts + gates + resume
  contracts/           # frozen JSON-Schema artifact shapes + validator
  orchestrator.py      # Atlas's brain: SDK query loop, streamed reasoning, playbook
  progress.py          # deterministic 🔎/📝/✅ status lines
  llm.py               # Atlas's brain seam (ATLAS_LLM switch)
  validate.py          # niche/topic validation (Atlas-owned)
  chat_state.py        # atomic writes + tolerant loads (summary-only memory)
  session.py           # UI-neutral session core shared by both frontends
  chat.py              # the terminal meeting room REPL + memory + commands
  web/app.py           # the Chainlit web operator UI (optional, additive)
  projects/            # per-video working dirs (project.json + artifacts + assets)
  run.py               # entry: `chat` | `"<niche>"` | `produce "<brief>"`
  soul/                # Atlas persona: SOUL.md + STYLE.md + examples/
  tests/               # pure-unit tests (no network/API)
  PLAN.md              # the plan + the pre-build review report
```

The siblings (`youtube-topic-agent/`, `topic-researcher/`, and the six specialist
projects) are **never modified** and stay independently runnable.
