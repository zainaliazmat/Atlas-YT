# PROJECT_CONTEXT.md — YT-AGENTS

> Onboarding document for an AI assistant with **zero prior knowledge** of this codebase.
> Read this top-to-bottom and you can discuss the project intelligently and suggest changes.
> Where something is inferred rather than verified, it says so.

---

## 1. Project Overview

**YT-AGENTS is a multi-agent "video agency" that turns a topic brief into a finished,
narrated, fact-checked explainer video — autonomously.** You (the "CEO") talk to a single
manager agent called **Atlas (the Showrunner)** in a chat "meeting room." Atlas delegates
to a fleet of specialist agents — each a self-contained Python project with its own
personality, brain, and memory — and runs them through a deterministic production
pipeline, pausing at two human approval "gates," and produces a final `video.mp4`.

**The problem it solves:** producing an explainer video normally means coordinating a
researcher, a fact-checker, a scriptwriter, an art director, an asset/licensing person, an
audio engineer, and a motion-graphics builder. This project encodes each of those as an
**LLM-backed agent** and wires them into one supervised, contract-validated assembly line
that can run end-to-end with no human in the loop except at two sign-off points.

**Who it's for:** a solo creator / "CEO of a one-person agency" who wants a fleet of
agents to do the production grind while they stay in the director's chair.

**Status (high level):** It works end-to-end. There is a real, completed example project in
`atlas/projects/` that ran all 10 stages and produced a real `video.mp4`. Two of the eight
agents (Scout, Sage) predate the pipeline; the other five specialists have since been built
and dropped into their registered slots. (See §12 for the one stage that is still a stub.)

---

## 2. Tech Stack

| Layer | Choice |
|---|---|
| **Language** | Python 3 (uses `from __future__ import annotations`; targets 3.10+ syntax) |
| **Runtime** | A single shared `venv/` at the repo root (all agents share one environment) |
| **Default LLM brain** | **Claude via the Claude Code subscription**, through `claude-agent-sdk` (>=0.2.105) — **no API key**, draws from your Pro/Max plan, NOT the metered API |
| **Alternative brains** | Google Gemini (`google-generativeai`, `gemini-2.5-flash`) and DeepSeek (raw `requests`, `deepseek-v4-flash`), each behind a per-agent env switch |
| **Contract validation** | `jsonschema` (Draft 2020-12) |
| **Config / secrets** | `python-dotenv`, one shared root `.env` |
| **Video render engine** | **HyperFrames** — a Node.js CLI (`npx hyperframes`), NOT a Python dep. Needs **Node ≥ 22** + **FFmpeg/FFprobe** on PATH |
| **Text-to-speech** | **Kokoro TTS** (Kokoro-82M) via HyperFrames `tts` (`kokoro-onnx` + `soundfile`) |
| **Optional transcription** | `whisper.cpp` (word-level caption timing; optional, never required) |
| **External data APIs** | YouTube Data API v3 (Scout), Google Trends via `pytrends` (Scout), web search (DuckDuckGo `ddgs` default; Tavily/Brave optional), Wikipedia REST, GDELT news, plus PD/CC asset & audio archives (see §9) |
| **Package manager** | `pip` + per-project `requirements.txt` |
| **Tests** | `pytest` (≈40 test files across the repo; pure unit tests, no network) |
| **Version control** | **Not a git repo** (no `.git`). There are `.gitignore` files, but the tree is not initialized. |

There is no web frontend in the tree today, though `atlas/session.py` is explicitly built as
a UI-neutral core "both the terminal REPL and a future web operator UI" would share.

---

## 3. Architecture

The system has two distinct planes that are deliberately kept separate:

1. **The conversational plane (Atlas the Showrunner):** an LLM agent on the Claude Agent
   SDK `query()` loop. It picks topics, announces decisions in-character, relays gate
   details to the CEO, and decides which tools to call. It is *not trusted* to guarantee
   correctness.
2. **The deterministic plane (the production spine):** `atlas/pipeline.py`, a plain
   state-machine that runs stages in a fixed order, validates every artifact against a
   frozen JSON-Schema contract before advancing, enforces the two human gates as
   pause-and-resume, and is resumable/idempotent.

The glue is a **registry + adapters + generated tools** pattern: each specialist agent is
ONE entry in `registry.py`; tools are generated *from* the registry; adding an agent =
one registry entry + one adapter, with **no orchestrator changes**.

```
            ┌──────────────── You (the CEO) ────────────────┐
            │        chat.py / session.py  (meeting room)     │
            └───────────────────────┬────────────────────────┘
                              orchestrator.py
                       (Claude Agent SDK query() loop)
                                    │  tools generated FROM…
                                registry.py ──── adapters/ ──── loader.py
                                    │            (uniform wrap)  (in-process,
                                    │                             isolated import)
                    ┌───────────────┴────────────────┐
                    │  produce_video tool             │  ask_<agent> / <agent>_<job> tools
                    ▼                                  ▼
              pipeline.py  ◄── validates each stage ──►  contracts/*.schema.json
              (deterministic spine)                      (frozen artifact shapes)
                    │
   research(stub) → script → factcheck ★GATE → style → storyboard
        → assets ∥ narration → compose ▲auto-gate → audiomix → render ★GATE → video.mp4
                    │  each stage's producer calls a specialist engine in-process
                    ▼
        youtube-topic-agent/  topic-researcher/  scriptwriter/  art-director/
        asset-sourcer/  audio-designer/  composition-engineer/   (sibling projects)
                                    │
                              npx hyperframes  (tts / lint / validate / inspect / render)
                                    +  FFmpeg  (concat, mix, mux)
```

**Request/data flow for "make me a video":**
1. CEO asks Atlas for a video → Atlas calls the `produce_video` tool with a `brief`.
2. `pipeline.produce()` creates a `project.json` under `atlas/projects/<slug>/` and runs
   stages in order. Each stage's **producer** reads the upstream artifact(s) from the
   project dir, calls a specialist's **engine in-process**, and writes a new artifact
   (e.g. `script.json`).
3. After each stage, the pipeline **validates the artifact against its frozen contract**.
   A failure blocks the stage (it does not crash).
4. At the **fact-check gate** and the **final-render gate**, the pipeline persists
   `status: "blocked_at_<gate>"` to `project.json` and **returns** — it never blocks
   mid-tool. Atlas relays the details to the CEO and waits.
5. CEO signs off → Atlas re-invokes `produce_video` with `approve=<gate>`; the pipeline
   resumes from where it left off (already-done stages are skipped).
6. Final stage renders + muxes audio → `video.mp4`.

**Why in-process (not subprocess):** all sibling projects ship modules with the *same bare
names* (`llm`, `chat_state`, `search`, …). `adapters/loader.py` imports each engine with its
module graph isolated (snapshot `sys.path`/`sys.modules`, drop colliding names, load,
restore), caches load-once, and guards the mutation with a thread lock — so two engines run
in one process each bound to its own `llm`. Synchronous engines spin their own event loop, so
jobs are dispatched via `asyncio.to_thread` to avoid nesting with the SDK's loop.

---

## 4. Directory Structure

```
YT-AGENTS/
├── .env                       # SHARED secrets for the whole fleet (root-level; gitignored)
├── skills-lock.json           # pinned HyperFrames doc-skills (from heygen-com/hyperframes)
├── venv/                      # the one shared virtualenv (noise — skip)
│
├── atlas/                     # ★ THE SHOWRUNNER / ORCHESTRATOR (the brain of the system)
│   ├── run.py                 # entry point: `chat` (meeting room) | "<niche>" | `produce …`
│   ├── chat.py                # terminal REPL frontend (commands, memory, SIGINT handling)
│   ├── session.py             # UI-neutral session core (shared by terminal + future web UI)
│   ├── orchestrator.py        # Atlas's brain: SDK query() loop, system prompt, playbooks
│   ├── registry.py            # ★ THE REGISTRY — one AgentEntry per agent; the source of truth
│   ├── tools.py               # generates SDK tools FROM the registry (+ timeout/containment)
│   ├── pipeline.py            # ★ THE PRODUCTION SPINE — deterministic stages + gates + resume
│   ├── progress.py            # deterministic 🔎/📝/✅ status-line emitter
│   ├── llm.py                 # Atlas's brain seam (ATLAS_LLM switch)
│   ├── validate.py            # niche/topic validation
│   ├── chat_state.py          # atomic JSON writes + tolerant loads (summary-only memory)
│   ├── contracts/             # ★ FROZEN ARTIFACT CONTRACTS (JSON Schema) + validator
│   │   ├── __init__.py        #   validate(name, obj), CONTRACT_VERSION, version_for()
│   │   └── *.schema.json      #   project, research_brief, script, factcheck_report, …
│   ├── adapters/              # uniform wrappers around each specialist (no sibling edits)
│   │   ├── loader.py          #   in-process isolated import (the collision fix)
│   │   ├── base.py            #   Adapter ABC: run_job (JOB) + ask (PERSONA)
│   │   ├── stubs.py           #   offline placeholder producers + StubAdapter
│   │   ├── scout.py sage.py scriptwriter.py art_director.py
│   │   ├── asset_sourcer.py audio.py composition_engineer.py
│   ├── soul/                  # Atlas's persona: SOUL.md + STYLE.md + examples/
│   ├── projects/              # ★ per-video working dirs (project.json + all artifacts + assets)
│   ├── tests/                 # 14 test files (contracts, pipeline, registry, routing, …)
│   ├── README.md PLAN.md CHANGELOG.md   # (README/PLAN describe the early Scout+Sage phase)
│   └── atlas.log              # produce_video arg-logging (INFO)
│
├── youtube-topic-agent/       # "Viral Scout" — finds ranked viral YouTube topic ideas
│   ├── agent.py youtube.py trends.py   # engine + YouTube Data API + Google Trends
│   ├── run.py chat.py llm.py chat_state.py compaction.py
│   ├── memory.json channel_cache.json trends_cache.json
│   ├── SKILL.md soul/ tests/ README.md
│
├── topic-researcher/          # "Sage" — research pack (Pass 1) + script fact-check (Pass 2)
│   ├── researcher.py factcheck.py search.py
│   ├── run.py chat.py llm.py compaction.py memory.json
│   ├── research_packs/        # saved JSON + Markdown research packs
│   ├── SKILL.md soul/ tests/ README.md
│
├── scriptwriter/              # "Marlow" — research brief → one-point-per-scene script
│   └── script_engine.py run.py chat.py llm.py SKILL.md soul/ tests/
├── art-director/              # "Iris" — script → style_guide.json + storyboard.json
│   └── art_engine.py run.py chat.py llm.py SKILL.md soul/ tests/
├── asset-sourcer/             # "Magpie" — storyboard → license-cleared asset_manifest.json
│   └── source_engine.py sources.py run.py chat.py llm.py assets/ SKILL.md soul/ tests/
├── audio-designer/            # "Cadence" — narration (TTS) + documentary audio mix
│   └── audio_engine.py audio_sources.py sfx_kit.py hf_audio.py run.py llm.py SKILL.md soul/
├── composition-engineer/      # "Mason" — artifacts → HyperFrames HTML + render → video.mp4
│   └── composition_engine.py hf_tools.py run.py chat.py llm.py SKILL.md soul/ tests/
│
└── .agents/skills/            # HyperFrames documentation skills (hyperframes, -cli, -media,
                               #   -animation, faceless-explainer, embedded-captions, …)
```

Every specialist project follows the **same skeleton**: an `*_engine.py` (pure, deterministic,
LLM-injectable), `run.py` (CLI), `chat.py` (co-worker REPL), `llm.py` (provider seam),
`chat_state.py`/`compaction.py` (memory), `SKILL.md` (the engine's job contract / method),
`soul/` (SOUL.md identity + STYLE.md voice + examples/), and `tests/`.

---

## 5. Entry Points & Core Flow

**Primary entry point: `atlas/run.py`.** From inside `atlas/`:

| Command | What it does |
|---|---|
| `python run.py chat` | The **meeting room** REPL (the main interface). |
| `python run.py "<niche>"` | One-shot: Scout finds topics → Atlas decides → Sage researches → reports. |
| `python run.py produce "<brief>" [--unattended] [--resume <slug> --approve <gate>]` | Run/resume the full video pipeline from the CLI. |

**Core code path for a meeting turn:**
1. `chat.py` → `session.AtlasSession.send()` ([atlas/session.py:250](atlas/session.py#L250)) —
   builds bounded context (durable summary + fleet snapshot + recent window) and calls the orchestrator.
2. `orchestrator.Orchestrator.run_turn_async()` ([atlas/orchestrator.py:171](atlas/orchestrator.py#L171)) —
   runs the Claude Agent SDK `query()` loop with `permission_mode="bypassPermissions"` (tools
   auto-run), streaming Atlas's text and auto-executing tools.
3. Tools are generated by `tools.build_server()` ([atlas/tools.py:222](atlas/tools.py#L222)) —
   one `<agent>_<job>` + one `ask_<agent>` per registry entry, plus the single `produce_video` tool.

**Core code path for video production:**
- `tools._make_produce_tool` → `pipeline.produce()` ([atlas/pipeline.py:182](atlas/pipeline.py#L182)) —
  the deterministic runner. Its `STAGES` list ([atlas/pipeline.py:58](atlas/pipeline.py#L58)) is the
  one fixed order; gate checkpoints are `_factcheck_gate()` and `_final_render_gate()`.

---

## 6. Key Modules / Components

### Atlas core
- **`registry.py`** — the single source of truth for "who Atlas can delegate to." Each
  `AgentEntry` has `name/display/emoji/blurb/project_dir/adapter_cls/jobs/role`. A `JobSpec`
  declares a delegable job (`name`, generated `tool` name, `description`, `params`, `timeout`).
  Helpers: `build_adapters()`, `get_entry()`, `roster()` (the `/agents` call sheet).
- **`adapters/`** — uniform wrap of each agent **without modifying it**. `base.Adapter` gives two
  capabilities: a **JOB** (`run_job` — calls the engine in-process, returns a compact digest) and a
  **PERSONA** (`ask` — loads the agent's `SOUL.md`+`STYLE.md` and replies in-character via Atlas's
  `llm` seam). `loader.load_engine()` is the isolated, cached, thread-safe importer.
- **`tools.py`** — turns the registry into SDK tools. Two hardening guarantees live here: **error
  containment** (every call wrapped so a sibling exception becomes a narratable tool result, never a
  crash) and **per-job timeout** (`asyncio.wait_for`). `produce_video` uses a full JSON Schema with
  `required: []` so both `{brief}` (new) and `{slug, approve}` (resume) shapes are valid; the handler
  enforces "exactly one of."
- **`pipeline.py`** — the deterministic spine (see §3/§5). Owns stage order, contract validation,
  the composition auto-gate, the two human gates (pause-and-resume via `project.json`), and resume
  logic. **Key rule:** a fact-check `block` verdict **can never be approved away** — it routes back
  upstream and re-blocks until the script is fixed and re-checked (`_factcheck_gate`,
  [atlas/pipeline.py:341](atlas/pipeline.py#L341)).
- **`contracts/`** — frozen JSON-Schema shapes for every artifact, `additionalProperties: true`
  (frozen-but-extensible). `validate(name, obj) -> (ok, errors)` never raises on bad data.
- **`session.py`** — UI-neutral session: memory (summary-only distillation), context assembly,
  status routing. The same object will back a future web UI.

### The eight agents (each is an independent, runnable project)

| Agent (project) | Persona | Role | Engine entry point(s) | Reads → Writes |
|---|---|---|---|---|
| `youtube-topic-agent` | **Viral Scout** 🔎 | Topic intake | `agent.run(niche, deep=False)` → ranked ideas | YouTube + Trends → topic ideas (in memory) |
| `topic-researcher` | **Sage** 📚 | Researcher & Fact-Checker | `researcher.run(topic, angle)` (Pass 1); `factcheck.factcheck(script, brief)` (Pass 2) | web → `research_brief`; script+brief → `factcheck_report` |
| `scriptwriter` | **Marlow** 📝 | Scriptwriter | `script_engine.write_script(brief)` | `research_brief.json` → `script.json` |
| `art-director` | **Iris** 🎨 | Art Director | `art_engine.design_style(script)`; `art_engine.build_storyboard(script, style_guide)` | `script.json` → `style_guide.json` + `storyboard.json` |
| `asset-sourcer` | **Magpie** 🗂️ | Asset Sourcer & Licensing | `source_engine.source_assets(storyboard, style_guide, client, pdir)` | `storyboard.json` → `asset_manifest.json` + downloaded files |
| `audio-designer` | **Cadence** 🎙️ | Audio / Sound Designer | `audio_engine.record_narration(script, pdir)`; `audio_engine.mix_audio(...)` | `script.json` → `narration.wav` + `narration.transcript.json` + `master.wav` + `audio_manifest.json` |
| `composition-engineer` | **Mason** 🛠️ | Composition Engineer | `composition_engine.compose(pdir)`; `composition_engine.run_render(pdir)` | all artifacts → scene HTML + `composition_manifest.json` → `video.mp4` |

**A few critical domain mechanisms:**
- **Iris's "one #FFD000 beat":** exactly one scene per video carries the `highlighter-FFD000`
  signature effect (riso-yellow highlighter sweep). It's never trimmed under the motion budget.
  Iris *specifies*, never implements (no HTML); Mason builds it.
- **Magpie's license truth table:** an asset reaches `cleared` only with an accept-list license
  (CC0/PDM/PD/CC-BY/CC-BY-SA) **and** complete attribution **and** a local file. Pexels/Pixabay/NASA
  are force-`sourced` (flagged for human review). Anything else → flagged local placeholder. URIs are
  always local (HyperFrames forbids render-time fetches).
- **Cadence's documentary mix ("master-bridge"):** VO is authoritative (0 dB) and is the sidechain
  key that hard-ducks the licensed music bed; one signature SFX lands on the cut into Iris's signature
  beat. Everything is pre-mixed into `master.wav`, and the narration track's `uri` points at the
  master — so the renderer muxes the full mix with **zero Composition Engineer edits**.
- **Mason's auto-gate:** before spending a render, each scene passes a self-scan (no network/fetch,
  no SMIL filters, no late `gsap.set`) + HyperFrames `lint` + `validate` (headless Chrome) + `inspect`
  (layout/overflow + motion assertions). A scene that fails the gate blocks the stage.

---

## 7. Data Models & Schemas

The pipeline's "language" is a set of JSON artifacts, each pinned by a schema in
`atlas/contracts/`. All set `additionalProperties: true` and require a `schema_version` string.

| Artifact (file) | Contract | Purpose / key fields |
|---|---|---|
| `project.json` | `project` | **Master state** for one video: `project_id`, `slug`, `status`, `config.gates`, per-stage `stages{status, artifact, validated}`, `gates{factcheck, final_render}`, `artifacts`, `history`. |
| `research_brief.json` | `research_brief` | Sage's pack shape (reused): `verified_facts[]`, `myths_and_corrections[]`, `contested_or_uncertain[]`, `key_statistics`, `sources[]`, `suggested_angles`. |
| `script.json` | `script` | `working_title`, `hook`, `cta`, `total_scenes`, `est_runtime_sec`, `scenes[]` where each scene has `scene_no`, `point`, `narration` (required), `on_screen_text`, `claims[{claim_id, text, source_ref}]`, `visual_note`, `duration_est_sec`. |
| `factcheck_report.json` | `factcheck_report` | `verdict` (`pass`/`block`), `summary{verified, flagged, unverifiable}`, `claims[]`. **The fact-check gate reads this.** |
| `style_guide.json` | `style_guide` (v1.1) | palette (incl. `signature_highlight: #FFD000`), typography, motion budget, layout, fps, textures, dos/donts. |
| `storyboard.json` | `storyboard` (v1.1) | `total_scenes`, per-scene `layout`, `shots[{kind, content, asset_ref}]`, `transition`, `effects[]`, `signature_beat`. |
| `asset_manifest.json` | `asset_manifest` | `assets[{asset_id, scene_no, type, source, uri, license, attribution, status}]` (`status` ∈ cleared/sourced/placeholder). |
| `narration.transcript.json` | `narration_transcript` | `total_duration_sec`, `segments[{scene_no, start_sec, end_sec, text}]` — the **downstream timing authority** for captions. |
| `audio_manifest.json` | `audio_manifest` (v1.1) | `total_duration_sec`, `master_uri`, `vo_uri`, `tracks[{role, uri, gain_db, ducking, license, status}]`. |
| `composition_manifest.json` | `composition_manifest` | Mason's per-scene build + auto-gate record (additive). |

**Vocabulary is closed-set** across the art/composition stages (the three orthogonal axes):
LAYOUTS (10: centered-statement, split-screen, full-bleed-image, lower-third, data-chart,
quote-card, map-focus, list-stack, comparison-2up, title-card), TEXTURES (5 global static:
paper, grain, halftone, vignette, scanlines), EFFECTS (7 per-scene: stutter-12fps, stepped-ease,
highlighter-FFD000, map-draw, chromatic-aberration, push-in, parallax), TRANSITIONS (5 at
boundaries: cut, match-cut, dip-to-black, push, wipe). Unknown tokens are an error, never
silently dropped.

**Agent memory model** (every agent): `chat_state.json` holds a single distilled `summary`
(+ a `pending` backlog field for crash-safety); `memory.json` (Scout/Sage) logs past `runs`/`wins`.
The full transcript lives only in RAM and is distilled into the summary on every session boundary.

---

## 8. APIs / Interfaces

This project has **no HTTP API**. Its interfaces are CLIs, the in-process orchestrator tools, and
each engine's public functions.

**Atlas meeting-room commands** (`python run.py chat`):
`<any message>` (delegates/answers), `/agents` (roster + each agent's effective provider),
`/ask <agent> <question>` (deterministic direct address), `/summary`, `/new`, `/help`,
`/exit` (`/quit`).

**Orchestrator tools** (generated; what Atlas's LLM can call):
- `<agent>_<job>` per JobSpec — e.g. `scout_find_topics(niche)`, `sage_research(topic, angle)`,
  `sage_factcheck(topic)`, `scriptwriter_write_script(topic)`, `art_director_design_style(topic)`,
  `art_director_build_storyboard(topic)`, `asset_sourcer_source_assets(topic)`,
  `audio_record_narration(topic)`, `audio_mix_audio(topic)`,
  `composition_engineer_compose_scenes(topic)`, `composition_engineer_render_video(topic)`.
- `ask_<agent>(question, context)` — single-turn in-character reply.
- `produce_video(brief?, slug?, approve?, unattended?)` — run/resume the full pipeline.

**Per-agent CLIs** (each `python run.py …` inside its project) — examples:
`youtube-topic-agent`: `run.py "<niche>"`, `--deep`, `chat`, `win "<topic>"`.
`topic-researcher`: `run.py research "<topic>" [--angle …] [--handoff f.json]`, `chat`.
`audio-designer`: `run.py narrate <script|dir>`, `run.py mix <dir>`, `run.py chat`.
`composition-engineer`: `run.py compose <dir> [--no-render]`, `run.py render <dir>`, `run.py chat`.

**HyperFrames CLI** (shelled out by the audio/composition engines via `hf_audio.py`/`hf_tools.py`):
`npx hyperframes tts|transcribe|lint|validate|inspect|render …`, plus `ffmpeg`/`ffprobe` for
concat/mix/mux.

---

## 9. Configuration & Environment

**One shared root `.env`** holds all keys (each agent loads its local `.env` first, then the root).
**The default setup needs NO keys for the LLM brain** (Claude subscription) — only Scout's YouTube
job needs a key. Secrets below are masked.

| Variable | Controls | Required? |
|---|---|---|
| `YOUTUBE_API_KEY` | Scout's YouTube Data API v3 calls | Needed for Scout's job |
| `GEMINI_API_KEY` | Gemini brain (any agent set to `gemini`) | Only if switching brains |
| `DEEPSEEK_API_KEY` | DeepSeek brain | Only if switching brains |
| `SMITHSONIAN_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY` | Extra Magpie asset sources | Optional/free; missing = source silently skipped |
| `FREESOUND_API_KEY` | Extra Cadence audio source | Optional/free |
| `TAVILY_API_KEY` / `BRAVE_API_KEY` | Higher-quality web search for Sage | Optional |
| **`ANTHROPIC_API_KEY`** | **DO NOT SET** — if set, the SDK bills the metered API instead of your subscription. `llm.py` warns. | Must be unset |

**The provider switches** — each agent has its own env switch read at import (precedence is local):
`ATLAS_LLM` (Atlas), `SAGE_LLM` (Sage; **note:** Scout's `llm.py` also reads `SAGE_LLM` per the
sibling summary — inferred), `IRIS_LLM`, `MAGPIE_LLM`, `AUDIO_LLM`, `MASON_LLM`. Each defaults to
`claude` and accepts `gemini`/`deepseek`. **A delegated job runs inside the sibling's engine and reads
that sibling's OWN switch** — so "ask Scout" (Atlas's brain) and "Scout does a job" (Scout's brain)
can run on different providers. `/agents` surfaces each effective provider.

> ⚠️ **Model-ID note for an assistant:** `atlas/llm.py` currently sets `CLAUDE_MODEL`/`ORCH_MODEL`
> to `"claude-sonnet-4-6"`, while the sibling summaries mention Opus and a `CHAT_MODEL` of
> `claude-sonnet-4-6`. If asked about "the model," **read the actual `llm.py` for the agent in
> question** rather than assuming — model IDs vary per agent and the most current Claude models
> (e.g. Opus 4.8) may be preferable. Don't set `ANTHROPIC_API_KEY` to "fix" anything.

---

## 10. Build, Run & Test

**Install** (shared root venv is the intended setup):
```bash
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
**System prerequisites for actual rendering:** Node ≥ 22 + the HyperFrames CLI (`npx hyperframes`),
FFmpeg/FFprobe on PATH, and the Kokoro TTS Python packages (`kokoro-onnx`, `soundfile`). Optional:
`whisper.cpp` for word-level caption timing.

**Run:**
```bash
cd atlas
python run.py chat                              # the meeting room (primary interface)
python run.py "AI tools & productivity"         # one-shot Scout→decide→Sage
python run.py produce "GPT-4o vs Claude … brief" # run the full video pipeline
python run.py produce "" --resume <slug> --approve factcheck   # resume after sign-off
```

**Test** (per project; pure unit tests, no network/API):
```bash
cd atlas && python -m pytest tests/ -q
# CHANGELOG cites 58 passing for the Showrunner phase; counts grow as specialists land.
```

---

## 11. Conventions & Patterns

- **Registry-driven extensibility:** add an agent = one `AgentEntry` + one `Adapter`; tools and
  `/agents` listing appear automatically. The orchestrator never changes.
- **Two-plane separation:** the LLM does *judgment* (topic choice, synthesis, gate conversations);
  the deterministic spine does *guarantees* (order, contract validity, gates). Never move a guarantee
  into the LLM.
- **Frozen-but-extensible contracts:** `additionalProperties: true` + a documentary `schema_version`.
  New specialists ADD optional fields under a bumped version; old readers keep working.
- **Decoupling at the boundary:** specialist engines emit plain dicts and **never import Atlas**.
  Atlas stamps `schema_version` and validates at the seam (in the adapter / pipeline).
- **Engines are pure + injectable:** each `*_engine.py` takes the LLM/network seam as an argument
  (`chat_fn`, `client`, `tts_fn`, …) so tests run offline and deterministically.
- **Persona "soul" bundle:** `SOUL.md` (identity, used by the engine), `STYLE.md` (voice) and
  `examples/` (good/bad calibration) are chat-only; `SKILL.md` is the engine's job contract/method.
- **Provider seam:** every agent isolates LLM calls behind `llm.chat(system, user)` with one env
  switch; identical signatures across providers for true drop-in swaps. No Ollama (policy).
- **Crash-safe memory:** atomic JSON writes (`chat_state.atomic_write_json`); a failed distill parks
  raw turns under `pending` (no data loss), folded in on next launch; summary-only durable state.
- **Graceful degradation:** every external call is wrapped; failures return empty/placeholder and a
  note, never a crash. Status lines are deterministic; decisions are the LLM's words.
- **Determinism in the asset/audio/composition stages:** reproducible manifests, no `Math.random`/
  `Date.now` in rendered HTML, no render-time network.

---

## 12. Current State

**Complete and working:**
- The full orchestration core (registry/adapters/loader/tools/orchestrator/session/memory).
- The deterministic pipeline with contract validation, the composition auto-gate, and both human
  gates as pause-and-resume + resume-by-gate.
- **All seven roles have real engines** (Scout, Sage, Marlow, Iris, Magpie, Cadence, Mason). The
  registry's per-entry comments document each "stub slot was filled."
- **An end-to-end run actually succeeded:** `atlas/projects/gpt-4o-vs-claude-vs-gemini-vs-deepseek-comparison--…/`
  has `status: "done"`, all 10 stages `done`, both gates `approved`, and a real `video.mp4`
  (11 scenes, ~72s audio).

**Known gaps / in-progress / tech debt:**
- **The pipeline's `research` stage is still a STUB.** In `pipeline.py`, the first stage uses
  `stubs.produce_research` (offline placeholder brief), NOT Sage's real research engine — even though
  Sage's `sage_research` *conversational* tool is real. So a `produce_video` run currently scripts from
  placeholder research unless the brief itself carries the facts. **This is the most important thing to
  know before changing the pipeline.** (`stubs.produce_factcheck` is also retained but no longer wired —
  the pipeline uses Sage's real `produce_factcheck`.)
- **Docs lag the code:** `atlas/README.md` and `atlas/PLAN.md` describe the early "Scout + Sage only"
  phase; `CHANGELOG.md` 0.2.0 still calls five specialists "stubs." The registry/adapters are the
  ground truth — trust them over the prose docs.
- **Not a git repository** — there's no version history; consider `git init` before large changes.
- **Per-scene TTS is sequential** (~11s/scene overhead); a 10+ scene script can run minutes
  (narration job timeout is raised to 900s). Parallelizing per-scene TTS is a documented follow-up.
- **No web UI yet** — `session.py` is built for one, but only the terminal frontend exists.
- Model IDs are inconsistent across agents' `llm.py` (see §9 note).
- Provider-fallback chains (e.g. a transition `xfade` vs hard `cut`, `whisper.cpp` word timing) are
  best-effort and degrade silently — verify behavior when debugging render/caption issues.

---

## 13. Glossary

- **Showrunner / Atlas** — the manager agent the CEO talks to; orchestrates the fleet and the pipeline.
- **CEO** — the human user; the system's single principal.
- **Registry** — `registry.py`; the one declaration of who Atlas can delegate to.
- **Adapter** — a uniform wrapper (`run_job` + `ask`) around a specialist, so Atlas can use it without
  modifying it.
- **Engine** — a specialist's pure logic module (`*_engine.py`), called in-process by its adapter.
- **JOB vs PERSONA** — a JOB runs the engine and returns structured output; a PERSONA (`ask`) replies
  in-character via the LLM seam (no structured output).
- **The spine / pipeline** — `pipeline.py`; the deterministic stage machine that guarantees order,
  validation, and gates.
- **Contract** — a frozen JSON Schema in `atlas/contracts/`; every artifact is validated against one
  before the pipeline advances.
- **Artifact** — a file produced by a stage (`script.json`, `storyboard.json`, `video.mp4`, …).
- **Gate** — a mandatory checkpoint. **Fact-check gate** (after fact-check; a `block` can't be approved
  away) and **final-render gate** (before spending the render). Both pause-and-resume via `project.json`.
- **Auto-gate** — Mason's automatic per-scene check (self-scan + lint + validate + inspect) before a render.
- **Slug** — a project directory name under `atlas/projects/`; identifies one video for resume.
- **Soul / SOUL.md / STYLE.md** — an agent's persona bundle (identity / voice + calibration examples).
- **SKILL.md** — an agent's engine job contract / method (the "how it works" for the engine).
- **HyperFrames** — the HTML-as-source-of-truth video framework; its Node CLI (`npx hyperframes`)
  does TTS, lint/validate/inspect, and render. HTML + a paused GSAP timeline + `data-*` timing attrs.
- **The master-bridge** — Cadence pre-mixes everything into `master.wav` and points the narration
  track's `uri` at it, so the renderer muxes the full mix without composition-side changes.
- **#FFD000 beat** — the signature riso-yellow highlighter moment Iris reserves once per video.
- **Distillation** — collapsing a meeting transcript into a single durable summary at each session boundary.
- **Stub** — an offline, deterministic placeholder producer (`adapters/stubs.py`) that writes a
  schema-valid artifact so the data-flow runs without a real specialist.
```
