# Atlas — the Showrunner (orchestrator over the agent fleet)

> **Status note (2026-06-22).** This is the **original Phase-1/2 plan + pre-build
> review** for when Atlas orchestrated just Scout & Sage. It is kept for the
> architectural rationale and the review record; it does **not** describe the current
> system. Since then Atlas evolved into the **Showrunner** of an 8-agent video studio:
> Scout, Sage, Marlow (scriptwriter), Iris (art director), Magpie (asset sourcer),
> Cadence (audio), Mason (composition engineer), and Vera (reference analyst). **All 8
> are built with real engines.** A deterministic 10-stage production pipeline
> (`pipeline.py`) with frozen JSON-Schema contracts, a compose auto-gate, and two
> human pause-and-resume gates turns a brief into `video.mp4`; a Chainlit web operator
> UI is a second frontend. See `README.md` and `CHANGELOG.md` (0.3.0) for the current
> reality — the registry/adapters are the ground truth. The supervisor pattern below
> (registry + adapters + tools generated from the registry) is exactly what carried
> the fleet from 2 agents to 8 with no orchestrator changes, as promised.

## Problem & premise
I (the CEO) run a fleet of single-purpose YouTube agents:
- **Viral Scout** (`youtube-topic-agent/`) — finds ranked viral topic ideas for a niche.
- **Sage** (`topic-researcher/`) — produces a fact-checked research pack for a topic.

Today I talk to each agent in its own chat. I want **one meeting room** where I talk to a
chief-of-staff agent — **Atlas** — that knows each agent's strengths, delegates to them,
reports back, and will absorb *future* agents with near-zero changes. Atlas is a new project
that **depends on** the others and must **not modify or break** them; they stay independently
runnable.

**Premises (to be challenged in review):**
1. A supervisor with a *registry + adapters* is the right abstraction for "head of all agents,
   including future ones" (vs. hardcoding Scout/Sage into the orchestrator).
2. In-process import of the sibling engines (proven feasible) is better than subprocess CLI calls.
3. The orchestrator should be an LLM agent that autonomously picks tools (claude_agent_sdk),
   not a hardcoded if/else router.
4. Status updates must be deterministic (emitted from inside tools), while decisions/synthesis
   are the orchestrator LLM's streamed text.

## Verification already done (against the INSTALLED stack)
- `claude_agent_sdk` **v0.2.105** (same version Scout & Sage run on). Confirmed installed surface:
  `ClaudeSDKClient` (multi-turn), `query` (streaming-input, the pattern `converse()` already uses),
  `tool` + `create_sdk_mcp_server` (in-process tools), streaming `ToolUseBlock`/`ToolResultBlock`/
  `AssistantMessage`/`TextBlock`, `CanUseTool` + `permission_mode` (approval gate).
- **Sibling import isolation PROVEN** with a live probe: both projects ship modules named
  `llm`/`chat_state`/`search`/`youtube`; a naive dual-import collides. A loader that snapshots
  `sys.path`+`sys.modules`, drops colliding local names, loads the engine, then restores, yields
  `scout.run` and `sage.run` callable in ONE process, each bound to its own `llm.py`. No subprocess.
- Shared root `.env` (`YOUTUBE_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`); both siblings
  switch provider via `SAGE_LLM`. Atlas mirrors the policy (its own switch, default `claude`, no Ollama).

## Architecture
New project `atlas/`, depends on the siblings, touches neither.

```
atlas/
  registry.py        # ONE entry per agent: name, blurb, capabilities, adapter ref, project dir.
  adapters/
    loader.py        # the proven isolation loader: load_engine(dir, module) -> module
    base.py          # uniform Adapter ABC: .job(name, **kwargs) + .ask(question, context)
    scout.py         # wraps youtube-topic-agent: find_topics(niche)->ranked ideas; ask(persona)
    sage.py          # wraps topic-researcher: research(topic, angle)->pack; ask(persona)
  tools.py           # generates SDK @tool fns FROM the registry (future agent -> tool appears free)
  orchestrator.py    # Atlas brain: query() loop w/ generated tools, streams reasoning, runs tools seq.
  progress.py        # deterministic status-line emitter (🔎/✅/📚) — injected into adapters
  llm.py             # Atlas seam: chat()+converse(), ATLAS_LLM switch — mirrors siblings
  validate.py        # niche/topic validation (reuse Sage's validate_topic logic) before API spend
  chat_state.py      # atomic_write_json / load_json (corruption-safe) — same convention as siblings
  compaction.py      # summary-only memory distill (Phase 2)
  chat.py            # meeting-room REPL (Phase 2)
  run.py             # entry: `python run.py chat` (primary); `python run.py "<niche>"` one-shot canonical flow
  soul/{SOUL.md,STYLE.md,examples/{good-outputs.md,bad-outputs.md}}   # Atlas persona
  tests/             # pure-unit, mocked adapters/distiller
  README.md requirements.txt .gitignore CHANGELOG.md PLAN.md
```

### 1. Registry (the extensibility seam)
A list of entries, each declaring `name`, `blurb` (one line of what it's good at), `capabilities`
(e.g. `["job:find_topics", "persona"]`), the adapter class, and the sibling project dir. Atlas
reads the registry to know who it can delegate to. **Adding a future agent = one registry entry +
one adapter class. Zero orchestrator edits.**

### 2. Adapters (loose coupling; never modify Scout/Sage)
Uniform interface per managed agent:
- **JOB capability** — calls the sibling engine in-process via the loader.
  Scout: `find_topics(niche) -> ranked ideas`. Sage: `research(topic, angle) -> pack`.
- **PERSONA capability** — `ask(question, context) -> in-character reply`: load that agent's
  `soul/SOUL.md` (+ STYLE.md) and do a single-turn persona response via Atlas's LLM seam.
- Each adapter receives a `progress` callback and emits deterministic status lines as it runs.

### 3. Orchestrator (Atlas's brain)
Atlas is a `claude_agent_sdk` agent. The registry's capabilities are exposed as **SDK tools**
(`ask_scout`, `scout_find_topics`, `ask_sage`, `sage_research`) **generated from the registry**,
so a future agent's tools appear automatically. Atlas's LLM autonomously decides which tools to
call and in what order. System prompt encodes the default **playbook**:
> "When asked for research on a viral topic in a niche: call `scout_find_topics`, review the
> options, DECIDE the single strongest topic and state why, then call `sage_research` on it,
> then bring findings back to the CEO as a clear summary."

Also handles **direct address** ("Scout, what do you think of X?") by routing to that agent's
persona, and answers general questions itself.

### Progress / transparency
- **Status = deterministic**, emitted inside each tool/adapter:
  `🔎 Scout is scanning '<niche>'…` → `✅ Scout returned <N> topics`;
  `📚 Sage is researching '<topic>'…` → `✅ Sage finished`.
- **Decisions/synthesis = Atlas's streamed text.** Atlas must announce
  `🧠 I'm going with '<topic>' because <reason>` before handing to Sage.
- **Autonomous but interruptible:** Atlas picks the topic; it does NOT `[y/N]`-gate each step,
  but announces each decision so the CEO can redirect ("no, research #2 instead").

### Reliability
- Validate niches/topics before spending API calls.
- Run delegated jobs **sequentially** (rate-limit safe).
- Any sub-agent failure is **reported and handled** (continue or pause) — never crashes the room.
- Atomic writes; tolerant loads; REPL stays alive on errors.

### Provider policy (carried over)
- Default brain: **Claude via subscription** (`claude_agent_sdk`), no API key, warn if
  `ANTHROPIC_API_KEY` set. Gemini + DeepSeek as configurable alternatives. No Ollama.
- Provider-agnostic state; Atlas's own files are the source of truth.

### Memory (summary-only model, the corrected one)
Distill on `/exit`, SIGINT, `/new`, `/summary` via the LLM seam: drop junk, keep durable signal,
merge, bounded; clear transcript; persist summary only; no-data-loss "pending" fallback on distill
failure; accurate memory self-description. (Phase 2.)

### The chat — the meeting room (Phase 2)
- On launch: load registry, greet briefly, note who's present (Scout, Sage), load Atlas's summary
  + a compact memory snapshot.
- Commands: `/exit`, `/new`, `/summary`, `/help`, `/agents` (list managed agents + what each does),
  `/ask <agent> <question>` (force a direct question to one agent).
- Existing per-agent chats stay intact for debugging; Atlas's chat is the main interface.

## Build phases
- **Phase 1 (orchestration core):** registry + adapters (Scout+Sage) + orchestrator + ONE full
  autonomous end-to-end run of the canonical flow on a real niche (niche → Scout finds → Atlas
  decides & says why → Sage researches → Atlas reports), with deterministic progress lines.
  Prove end-to-end BEFORE the chat layer.
- **Phase 2 (meeting-room chat):** memory model, direct-address routing, all commands, persona polish.

## Tests (pure-unit, no network/API — mirror the other agents)
- registry resolves agents and generates the right tools; adding a MOCK agent entry surfaces its
  tool with no orchestrator change.
- routing: a direct-address question targets the correct agent (mock adapters).
- orchestration: with MOCKED adapters, the canonical flow calls `scout_find_topics` → (decision)
  → `sage_research` in order, and progress lines are emitted.
- memory distill on `/exit`/`/new`/`/summary`/SIGINT (mock distiller; summary saved, transcript
  cleared) + pending fallback on distill failure.
- atomic write + corrupt recovery; niche/topic validation rejects garbage; chat system prompt
  excludes the orchestration/output contract.
- Honestly note which behaviors are manual/integration (the real multi-agent run, real recall).

## Finish
README (supervisor pattern, registry + how to add a future agent in a few lines, adapters,
meeting-room chat, all commands, memory + provider policy), requirements, .gitignore, final tree,
short changelog.

## Known risks / open questions (seed for review)
- **Import isolation durability:** the loader keeps each engine's sibling refs in its module
  globals after restore. Risk if an engine lazily imports a colliding module *after* load (e.g.
  Scout's `converse` path). Mitigation: load eagerly; keep adapters job-focused; persona `ask`
  uses Atlas's OWN seam, not the sibling's `converse`.
- **Async/event-loop nesting:** sibling engines call `asyncio.run` internally (their `llm.chat`).
  Atlas's orchestrator also runs an event loop (SDK). Calling `agent.run()` (which calls
  `asyncio.run`) from inside Atlas's async tool would nest loops and crash. Mitigation: run sibling
  jobs in a worker thread (`asyncio.to_thread`) so their `asyncio.run` gets its own loop.
- **Rate limits:** Scout(plan+analyze) + Sage(decompose+classify) + Atlas(orchestration turns) =
  several subscription LLM calls per canonical run. Sequential execution + graceful failure.
- **`SAGE_LLM` shared switch:** both siblings read `SAGE_LLM`; Atlas adds `ATLAS_LLM`. A future
  agent could want a different provider. Document the precedence.

---

## GSTACK REVIEW REPORT  (/autoplan — adapted, subagent-only)

**Mode:** adapted full review (non-git env). **Voices:** Claude subagents ran (CEO/Eng/DX);
**Codex unavailable** — its Linux sandbox needs bubblewrap/user-namespaces, absent here, so all
three codex passes failed to initialize. Tagged `[subagent-only]` per degradation matrix.
**UI scope:** none (terminal REPL) → Design phase skipped. **DX scope:** yes.

### Consensus tables (single independent voice; "—" = codex N/A)
CEO: premises mostly sound; **DISAGREE on abstraction timing** (registry premature for N=2) and
**in-process vs subprocess** (subprocess safer). Eng: **architecture sound, in-process viable**,
to_thread mitigation correct; 3 must-fix safety items. DX: architecture honest, but headline
"few lines / zero edits" oversells; provider-switch model confusing.

### Cross-phase theme (high-confidence — surfaced independently by CEO and DX)
The **extensibility promise is oversold**: registry+adapters + "add an agent in a few lines" is
carrying cost for two agents (CEO) and materially understated effort given differing engine
signatures + async + persona loading (DX, est. ~80–150 lines per real job-agent).

### Auto-decided (6 principles) — folded into the build
| # | Phase | Decision | Principle | Rationale |
|---|-------|----------|-----------|-----------|
| 1 | Eng | Load each engine ONCE at registry init, cache the module on the adapter; never re-run the loader per job | P1/P5 | Re-loading re-runs module side effects + risks re-collision (Eng-F1) |
| 2 | Eng | Wrap the loader's snapshot→mutate→restore in a `threading.Lock`; do all loads eagerly single-threaded at startup | P1 | sys.modules/sys.path are process-global; concurrent load silently cross-wires (Eng-F2, CRITICAL-adjacent) |
| 3 | Eng | Every generated tool wraps `await asyncio.to_thread(run,…)` in try/except → returns `{"ok":false,"error":…}`; REPL also wraps each turn | P1 | Two-layer containment = "never crash the room" (Eng-F6) |
| 4 | Eng | Add a timeout to the JOB `to_thread` await (the `chat()` path has NO timeout — silent hang risk); catch sibling rate-limit RuntimeError and pause, don't abort | P1 | Most likely production hang (Eng-F5) |
| 5 | Eng | Confirm + assert the to_thread mitigation with a test (calling a `asyncio.run`-using fn inside a live query loop succeeds via to_thread, raises without) | P1 | Makes the mitigation load-bearing, not incidental (Eng-F4/F7) |
| 6 | Eng | Add tests: loader identity, load-once idempotency, concurrent-load, event-loop nesting, tool-error containment, job timeout, provider-lock | P1 | Coverage of the real risk surface (Eng-F7) |
| 7 | DX | Atlas OWNS niche validation in `validate.py` (Scout's `validate_niche` lives in its chat.py, NOT importable from the engine); reuse Sage's engine-level `validate_topic` | P4/P5 | Confirmed asymmetry (DX-F4) |
| 8 | DX | Define provider precedence: `ATLAS_LLM` drives orchestration + persona `ask`; JOBs inherit the sibling's `SAGE_LLM` (locked at import). `/agents` prints each agent's effective provider; startup warns on divergence | P5 | SAGE_LLM/ATLAS_LLM split is a real footgun (DX-F2, Eng-F3) |
| 9 | DX | Persona `ask` loads SOUL+STYLE only (not examples) to bound voice+tokens; rework the distill prompt for the multi-agent room (not single-creator framing) | P5 | Sibling personas are large + chat-tuned (DX-F5) |
| 10 | DX | Ship `.env.example` + a Setup/TTHW block; specify `run.py "<niche>"` output; document `/quit` alias + `/ask <agent> <question>` arity | P1 | Parity with siblings, getting-started friction (DX-F3/F5) |
| 11 | DX | Honest-up the README claim: "persona-only agent ≈ 30 lines; a job-agent joining the canonical flow needs an adapter + one playbook line"; include a worked 3rd-agent example | P5 | Don't oversell "a few lines" (DX-F1, CEO theme) |

### Surfaced to CEO (NOT auto-decided)
- **D-ARCH (architecture, voices disagree):** in-process import + registry+adapters (your spec, Eng-validated)
  vs subprocess isolation + hardcoded-first (CEO voice). Default = your spec.
- **D-ORCH (taste):** LLM autonomously picks tools (your spec) vs hardcoded canonical pipeline + LLM only
  for direct-address/general chat (CEO-F4).
- **D-SEQ (taste, minor):** sequential jobs (your spec, rate-safe) vs parallelize independent jobs later.
