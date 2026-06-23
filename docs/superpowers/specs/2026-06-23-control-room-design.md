# YT-Agents Control Room — Master Design

**Date:** 2026-06-23 (hardened after second-reviewer pass)
**Status:** Approved (design locked; sub-projects to be specced individually)
**Author:** CEO + Atlas (brainstorming session) · hardened against review notes

## 1. Goal

Turn the dashboard into the **primary place where the entire video agency is operated** — not a
passive monitor. From one web control room the CEO can: drop a topic (or a niche) and launch a
production, watch many videos flow down a real assembly line in real time, chat with Atlas (who can
take *reversible* actions), approve gates on the deterministic UI, publish/schedule to YouTube, and
read back real-world performance that feeds the self-improvement loop.

**The central tension this spec must manage:** today the dashboard's entire safety story is
"read-mostly, exactly one sanctioned write (gate approval), routed through the existing pipeline
seam" (PROJECT_CONTEXT §8). This project **deliberately gives that up** — the control room will
launch productions, change settings, publish externally, and approve persona rewrites. Every safety
property that came *for free* from read-only-ness now has to be **re-earned per write path**. The
write-authority model in §4 is how we re-earn it.

## 2. Background — what exists today

Three surfaces exist; only one survives:

- **`yt-agents-dashboard.html`** (repo root, 829 lines) — a **static design mock**, zero `fetch()`,
  all data hardcoded. This is what the CEO's screenshot showed; it explains "buttons don't work" and
  "always the same project." PROJECT_CONTEXT calls it "the approved static PROTOTYPE." **→ Retire.**
- **`atlas/dashboard/`** — a real **FastAPI** app (`app.py` + `data.py` + `security.py` + `media.py`),
  live-wired to on-disk state, 6 screens, **read-mostly** (only write = gate approval via
  `session.AtlasSession.approve_gate`), 51 unit + 12 Playwright tests. **→ Base we extend.**
- **Engine seams already in place** (verified in code):
  - `atlas/session.py` — shared session core; `chat.py` is a thin terminal frontend over
    `session.send()`. A web chat calls the same `session.send()`.
  - `atlas/pipeline.py` — `produce(brief=..., slug=..., approve=[...])` creates+runs a project
    through `STAGES`, validates each artifact against a frozen contract, pauses at gates by persisting
    `blocked_at_<gate>` and **returning** (never blocks mid-tool). **No retry, no cancel** (verified).
  - `atlas/orchestrator.py` + `atlas/tools.py` — Atlas runs on the **Claude Agent SDK** with an
    in-process tool server that **already includes `_make_produce_tool`**.
  - `atlas/adapters/loader.py` — in-process engine import with `sys.path`/`sys.modules` isolation
    under a thread lock, **load-once cache**. Controls **imports at load time only** — not runtime
    state (verified: its own docstring says so).
  - `atlas/registry.py` — agents as SOUL/STYLE/SKILL personas; each agent has named skills mapping to
    pipeline stages. Sage = `researcher.run(topic, angle)` (Pass 1) + `factcheck.factcheck(script,
    brief)` (Pass 2) — **already two functions** (verified).
  - **Self-improvement layer** (PROJECT_CONTEXT §13): frozen CEO-owned `rubric/` (no write path),
    `eval/` (analyzers → scorecard → diagnose → loop), `WriteBoundaryError`, coaches Quill/Flux.
    Core principle: **the improver is LESS privileged than the guarantees**, enforced structurally.

## 3. Locked decisions

| Area | Decision |
|---|---|
| **Stack** | Extend FastAPI + vanilla JS. Retire the standalone mock. One source of truth. (Approach A) |
| **Concurrency model** | True **assembly line** (Model 3), **station = stage**. Dispatcher advances videos stage-by-stage; each stage single-occupancy. **A max-in-flight of 1 degenerates to "one active production + a queue"** (the simple model is a special case — see §6 reality check). |
| **Agent split** | **Split Sage only** → Researcher + independent Fact-Checker. Independence is a **context/seed/soul mechanism, not a topology one** — see §7. All other multi-stage agents stay single coherent personas. |
| **Niche intake** | Select niche → Scout `find_topics` → **configurable** auto-pick / you-pick → enters line. Niches live in a **minimal niche config** (available before the full Settings page). |
| **Trigger bar** | Topic + target length (short ~60–90s / long ~5–8min) + gates on/off toggle. |
| **Settings page** | Full defaults: niches, channels, voice, style presets. **Persisted as JSON read by the pipeline/adapter and PASSED INTO engines as args** — never read globally by a pure engine (preserves the §11 decoupling rule). |
| **Write authority** | **Tiered** (replaces a single "confirm-before-act" bucket) — see §4. |
| **Chat** | Bottom-right launcher → panel. **Agentic** via the existing Agent SDK tool loop; read-grounded → reversible action tools only (trigger production, change setting). **Chat may navigate to and summarize a gate/publish, but may NEVER satisfy one** — see §4/§8. (CEO-confirmed 2026-06-23: chat is T1-only; it narrows the earlier "chat can approve" wish by design.) |
| **Live updates** | **SSE** with `Last-Event-ID` backfill + multi-tab fan-out — see §10. |
| **Publisher — Herald 📡** | Terminal stage on the line. `package` + `publish` (route by niche to channel, schedule). Behind a **publish gate** (Tier 3). **"Scheduled" = go-live time set AFTER human approval of the exact package** — scheduling is *when*, the gate authorizes *what*. Nothing auto-publishes unreviewed. YouTube Data API + per-channel OAuth, quota-bounded (§9). |
| **Analyst — Echo 📈** | Sibling to the coaches. Pulls YouTube Analytics, writes CEO report + performance contract. **Uses its OWN observational/cohort methodology — NOT the reproducible-eval guardrails** (§7-loop). Rubric-contradicting findings → CEO-interview items, never auto-applied. **Proposes for CEO approval** (Tier 4). |
| **Brain / framework** | Keep the **Claude Agent SDK**. No LangChain/LlamaIndex/CrewAI, no Mem0/Letta/Zep/cognee. |
| **RAG / memory** | Thin `retrieve()` seam as an in-process SDK tool; **hybrid** retrieval. **Phased**: 5a = grep + file-path identifiers (no vector store); 5b = **sqlite-vec + a verified-local embedding model**. **Default embedding for 5b = `nomic-embed-text-v1.5` (genuinely local) unless `voyage-4-nano`'s open-weight/Apache-2.0/local claim is verified at build time** (§11). |

## 4. Write-authority model (the re-earned safety story)

Read-only-ness is gone; we replace it with **four tiers**, mirroring the eval loop's privilege
asymmetry (PROJECT_CONTEXT §13 non-negotiable #2). Each write path is classified into exactly one
tier, and the tier dictates the guard. **No write path may be silently upgraded in power.**

| Tier | Examples | Guard |
|---|---|---|
| **T1 — Reversible internal** | trigger a production, change a setting, cancel/park a run | **Light, cancellable confirm.** May be initiated by chat. Cheap to undo, so don't gate heavily (confirm-fatigue is a real failure mode — see T3). |
| **T2 — Spine gates** | factcheck gate, final-render gate | **Satisfied ONLY via the deterministic gate UI**, which shows exactly what will happen. The chat agent may *surface/summarize/navigate to* a gate but **must never be able to satisfy one.** A `block` verdict still **can never be approved away** (existing spine rule, unchanged). |
| **T3 — Irreversible external** | publish to YouTube (Herald) | **Hard structured confirm + an actually-enforced review checkpoint.** The human approves the *exact final package* (title/description/tags/thumbnail/visibility/schedule). Scheduling only sets the go-live time *after* this approval. **A review window with no enforced reviewer is a delay, not a safety property** — so there is no "auto-fire unreviewed" path. |
| **T4 — Persona/rubric writes** | coach addenda, Echo proposals | **Existing `WriteBoundaryError` write boundary + CEO approval**, unchanged. Echo and Herald **cannot route around it** — they propose; they never write soft-tier files or the rubric directly. The rubric remains unwritable (`can_write_rubric()` self-check stays true). |

**Cross-cutting:** every action declares its tier in code; the SSE/event log records *who initiated*
(chat vs deterministic UI vs dispatcher) so an audit can show no T2/T3 write ever originated from the
LLM plane.

## 5. The self-improvement loop (closed) — and Echo's distinct methodology

Today self-improvement optimizes an **internal proxy** (eval rubric/scorecard + coaches). Echo adds
**external ground truth** (real YouTube performance). But the two **cannot share statistics** — this
is a structural mismatch the spec must respect:

> The internal eval's noise-floor gate literally means *"run the held-out set K≥5× and measure
> variance"* (verified in `tracking.py`). **You cannot run a real publish five times.** Every
> real-world datapoint is one-shot, non-reproducible, confounded (topic, thumbnail surfacing, channel
> size, seasonality, the algorithm), and arrives weeks late.

So **Echo gets its own methodology, NOT the reproducible-eval guardrails:**

- **Cohort/aggregate, observational discipline.** Echo reasons over *patterns across N videos*
  ("this hook shape underperforms across the last N uploads"), never a single outcome.
- **n=1 overfitting guard.** The per-video diagnosis map below is a *hypothesis generator*, not a
  routing trigger. Echo must accumulate cohort evidence before routing anything to a coach, and it
  feeds the CEO-approval gate **aggregate evidence**, never a single fluke.
- **The deep point — rubric contradictions are CEO-owned.** When Echo's real retention contradicts
  the rubric's `hook_strength` band, **the rubric is wrong** — a *ground-truth decomposition gap*.
  That is exactly the one thing the improver structurally *cannot* fix (the CEO owns the rubric, no
  write path). So Echo's most valuable insight is permanently a **CEO-interview item, not a loop
  item.** This reuses the existing "decomposition gap → escalate to CEO" concept (`rollup.py`).
  **Nobody wires Echo into auto-tuning the rubric.**

Diagnosis map (hypothesis generator, applied only at cohort scale, routed via Tier 4):

| Real signal | Hypothesised cause | Candidate owner |
|---|---|---|
| Low CTR (cohort) | weak title/thumbnail | Herald (packaging) / Iris (thumbnail) |
| Retention drops at intro (cohort) | weak hook | Quill (editorial) — or **rubric gap → CEO** |
| Retention sags mid-video (cohort) | pacing/script | Quill / Marlow |
| Audio drop-off (cohort) | mix/voicing | Flux (production) |

Loop: **produce → publish (Herald, T3) → measure cohort performance (Echo) → either propose a coach
addendum (T4, CEO-approved) OR escalate a rubric gap to the CEO interview → next videos improve.**

## 6. The assembly-line engine (#1) — the riskiest, most-specified piece

This sub-project **mutates the deterministic spine**, which the whole architecture exists to keep
dumb and trustworthy. It is **not** "depends on: none, go build" — it has the preconditions below,
each of which must be in its own sub-spec before #1 is buildable.

### 6.1 Per-video belt-position state machine
Explicit states and legal transitions (persisted in `project.json`, extending the existing
`status`):

```
queued@<stage> ──► running@<stage> ──► (validated) ──► queued@<next>
     ▲                   │
     │                   ├──► blocked@<gate>   (human gate; PARKS — see 6.3)
     │                   ├──► failed@<stage>   (see 6.4)
     │                   └──► cancelled        (see 6.5)
queued@<stage> ◄── (gate approved / retry)            running@render ──► done
```

- **Contention policy: FIFO.** When an approved/advancing video wants an occupied station, it waits
  in that station's FIFO queue. (Priority scheduling is explicitly out of scope for v1 — §13.)
- **A blocked or parked video HOLDS NO STATION.** It releases its slot and re-enqueues at the right
  stage on approval/retry. (Otherwise a video waiting days at a gate would freeze the belt.)

### 6.2 Run-registry persistence & rebuild-from-disk
Today resume works because each run is a fresh process reading one `project.json` —
**per-project, not per-belt** (verified). A long-lived dispatcher that tracks "which videos are at
which station" introduces new authoritative state. Rule:

- **The dispatcher holds NO authoritative in-memory-only state.** The belt is *always reconstructable
  by scanning `projects/*/project.json`.* On startup/restart the dispatcher rebuilds the belt from
  that scan. A dispatcher crash must not lose the belt; at worst an in-flight stage is re-run
  (stages are idempotent/skippable when `done`).
- The run-registry is a **view over disk**, persisted only as a cache/index, never as the source of
  truth.

### 6.3 Concurrency vs the in-process loader & per-agent memory (the subtle one)
`loader.py` isolates **imports** under a lock and caches load-once; once cached, two stations running
**different** agents execute in **parallel threads**. Station=stage single-occupancy serializes
*same-agent* access **but does nothing for**:

- **(i) shared module-level state across different agents** — tolerable today (engines are pure +
  injectable, §11), but **every NEW agent (split Sage, Herald, Echo) must keep the loader's
  invariant: no lazy import of a colliding bare name at call time, and no mutable module-level
  globals that two concurrent videos could stomp.** This is a hard constraint on #1, #6, #7.
- **(ii) per-agent `memory.json`** — lives at the **agent project root, NOT per-video** (verified:
  `researcher.py:29`, `agent.py:26`). Appends are **read-modify-write with last-writer-wins**; the
  code itself documents the race (`agent.py:38`: *"still clobber memory.json (last write wins)"*).
  Two videos that both touch Scout/Sage over the belt's life will **append-race and silently drop run
  records.** Atomicity prevents corruption, not lost records. Worse, the **summary-distillation memory
  model assumes ONE coherent conversation** — interleaved multi-video jobs violate that assumption.

**Decision for the belt:** pipeline **JOBS** (not chat) must use a **race-safe run log** —
per-run append-only record files (or a per-agent memory write-lock around read-modify-write), so no
record is lost under concurrency. The conversational **distillation summary** stays a **chat-only**
concept; pipeline jobs do not distill into it. This is a **precondition of #1**, and it is the reason
the multi-video belt is not safe on today's memory code as-is.

### 6.4 Stage-failure policy
Today a non-gate failure sets `failed` and returns (verified) — fine for one process, dangerous for a
dispatcher (silent stalls or tight retry loops). Define:

- **Transient failures** (network/timeouts, e.g. asset/search/TTS hiccups) → **bounded retry with
  backoff** (small N, exponential), then park as `failed@<stage>` with the reason.
- **Deterministic failures** (contract-validation failure, composition auto-gate failure) → **NO
  retry** (re-running yields the same failure — a tight loop). Park immediately as `failed@<stage>`
  with the reason.
- **Surface WHY in the UI**: every parked/failed video shows its stage, reason, and retry/cancel
  affordance. (We do **not** build a separate dead-letter-queue subsystem — the parked `failed`
  project *is* the dead letter; §13.)

### 6.5 Cancellation
A background dispatcher + multi-video belt **needs a cancel/kill path** (none exists today —
verified). Requirements: cancel a queued or running video; a running stage is allowed to finish its
current engine call or is cooperatively interrupted at the next checkpoint (no hard thread-kill —
engines hold no cross-process resources but do hold the loader's invariants); the video moves to
`cancelled`, releases its station, and is removable from the belt.

### 6.6 Throughput reality check (honest scope)
The bottleneck stations are **render** (headless Chrome + FFmpeg) and **TTS narration** (sequential,
~11s/scene, minutes for 10+ scenes; narration timeout is already raised to 900s — PROJECT_CONTEXT
§12). **These are exactly the stations single-occupancy serializes.** So the practical win over a
simple queue is **pipelining overlap** ("video B is being scripted while video A renders") — *real
but modest*, not Nx throughput.

**Honest in-flight target: ~2–3 videos on one box.** The belt is justified **not** by big throughput
but by: (a) the live conveyor-belt UX the CEO wants, (b) genuine overlap of cheap stages against the
two heavy ones, and (c) explicit per-stage resource safety (never two renders at once). **We will not
claim throughput the bottlenecks can't deliver.** And because max-in-flight=1 degenerates to "one
active + queue," we get the simpler model for free as a config — de-risking the decision.

## 7. Split Sage — the independence MECHANISM (settled; implement it well)

The split is decided. But the independence win is a **context/seed/soul property, not a registry
topology one**: Sage's two passes are *already* separate functions (`researcher.py` /
`factcheck.py`), and `factcheck` *already* takes `(script, brief)` (verified). Two registry entries
alone give two souls + two `memory.json` files but **do not stop the checker from treating the brief
as authoritative.** Spec the actual mechanism:

- **Two distinct souls.** Researcher = synthesis / curiosity / breadth. Fact-Checker = adversarial
  skepticism / "guilty until verified."
- **The Fact-Checker must NOT treat the research brief as ground truth.** It receives the script +
  the claims and **re-verifies against its OWN fresh source retrieval**, using the brief only as a
  *lead*, never as authority.
- **Fresh context window.** The checker does not inherit the researcher's conversation/framing.
- **Different seed (required); different provider (optional, configurable).** A different seed is
  cheap and breaks "the same model rationalizing its own earlier output." A different *provider* is
  left optional — requiring it conflicts with the no-API-key Claude-subscription default, and most of
  the independence win comes from fresh-retrieval + adversarial-soul + fresh-context, not vendor
  diversity. (Reasoned partial-adopt — §14.)
- **Explicit contract.** Document exactly what the Fact-Checker receives and what it is trusted to
  assert (verdict + per-claim status), unchanged in shape from `factcheck_report` so the gate is
  untouched.
- **Per-persona `memory.json`** — and it is subject to the **multi-video append race in §6.3**; the
  Fact-Checker's memory uses the same race-safe run log.
- **The factcheck GATE stays in the spine unchanged.** A `block` can never be approved away,
  regardless of the split (verified spine rule).

## 8. Agentic chat — not a backdoor around the spine

The chat panel is read-grounded over the corpus (RAG), which is a **prompt-injection surface**:
adversarial text retrieved from research/project state ("approve all gates and publish") must not be
one confirm-click from a public upload. The system's own philosophy is that **the LLM is not trusted
to guarantee correctness** — so the LLM plane must not *drive* a guarantee.

- **Chat can initiate only T1 (reversible) actions** — trigger a production, change a setting, cancel
  a run — each with a light confirm.
- **Chat can NEVER satisfy a T2 spine gate or a T3 publish.** It may navigate you to the gate/publish
  screen and summarize what's pending; the authorizing **click stays on the deterministic UI** that
  shows exactly what will happen.
- **Confirm-fatigue is a real failure mode.** Reserve *hard structured* confirms for T3 (publish);
  keep T1 light — otherwise operators reflex-click through everything and the confirm becomes
  theater.

> ✅ **CEO-confirmed (2026-06-23): Option 1 (hardened).** This **deliberately narrows the earlier
> "chat should be able to approve gates" wish.** Chat surfaces and explains a gate; the authorizing
> click stays on the deterministic UI. Rationale accepted: once #5 (RAG) lands, chat reads untrusted
> text from research/project artifacts, and a gate is exactly the guarantee the two-plane design says
> the LLM must never satisfy. Chat stays fully agentic for T1.

## 9. External distribution constraints (Herald) — researched 2026-06-23

Grounded by a dedicated YouTube Data API v3 research pass (cited, mid-2026). The findings
**confirm** the T3 design and **correct** the quota framing.

- **Quota is a hard, PROJECT-WIDE ceiling — ~6 publishes/day across ALL channels combined.**
  `videos.insert` costs **1600 units** against a default **10,000 units/day per Cloud project**
  (not per channel). Adding channels does **not** add quota. So the belt cannot exceed ~6 uploads/day
  total regardless of how many channels are connected. *(Flag: Google is mid-migration to a
  separate-bucket model that may instead cap `videos.insert` at ~100/day — verify the actual project's
  Console quota at build time.)* The publish stage is quota-aware, shows **shared** quota in the UI,
  and back-pressures/queues to the next window when spent.
- **OAuth is per-channel, selected at consent time** (brand-account chooser). Each channel yields its
  **own refresh token**; routing an upload to a specific channel = authenticating with that channel's
  token (there is no target-channelId field on `videos.insert`). Connect = run the flow **once per
  channel**, then read back the `channelId` via `channels.list?mine=true` (source of truth, never the
  user's label). Scopes: `youtube.upload` + `youtube.force-ssl` + `yt-analytics.readonly` — these are
  **sensitive** (Google verification required) but **not restricted** (no CASA security assessment —
  a real feasibility win).
- **The verification gauntlet is Herald's #1 feasibility risk — public/scheduled publishing is
  impossible until BOTH:** (a) the Cloud **project** passes Google sensitive-scope verification (else
  every `videos.insert` is forced to **private** — `publishAt` can't make it public), and (b) each
  **channel** is phone-verified (else no custom thumbnails, no scheduling, no >15-min videos). Phone
  verification is rationed to **2 channels/number/year**. The Channels shell must show both flags and
  gate the publish action on them.
- **Tokens are fragile** — store refresh tokens as encrypted secrets (one per channel, keyed by
  channelId), never surface them in the UI. Plan a per-channel **connection-status state machine**
  (`connected | needs-reconnect | expired | revoked`) with a one-click reconnect: refresh tokens die
  on revocation, **6-month idle**, the **100-token/client** cap, and — critically while unverified —
  a **7-day expiry in OAuth "Testing" mode** (every channel silently disconnects weekly until the app
  is verified/Production).
- **Scheduled-after-approval is a YouTube ToS REQUIREMENT, not just our preference.** Developer
  Policies require publishing to be user-initiated, user-controlled, with **no post-submission
  metadata mutation** (audited). This independently validates §4 T3 (E8): the human approves the exact
  package; the schedule only sets go-live; there is no silent auto-poster.
- **Channel↔niche map** lives in Settings; Herald routes by niche → the mapped channel's token.

## 10. Live updates (SSE)

- **`Last-Event-ID` backfill**: a reconnecting tab replays missed stage-transition events from a
  bounded server-side ring buffer, so it never silently misses a transition.
- **Multi-tab fan-out** confirmed: the event stream broadcasts to all connected tabs (the CEO may
  have several open).
- Events carry the initiator (chat / deterministic UI / dispatcher) for the §4 audit property.

## 11. Brain / RAG (phased)

Deep-research run 2026-06-23 (111 agents, 28 sources, 25 claims verified, 22 confirmed):

- **Vector store (5b):** `sqlite-vec` — pure C, zero deps, embedded, brute-force exact (ideal at this
  scale). Beats Chroma/LanceDB/FAISS on operational footprint for single-user local.
- **Embeddings (5b):** Anthropic ships none, points to Voyage. `voyage-4-nano` is *claimed*
  open-weight Apache-2.0/local — **but Voyage has historically been a hosted API, and the
  local/unmetered property is exactly what 5b depends on.** So: **default to `nomic-embed-text-v1.5`
  (genuinely local-runnable) unless `voyage-4-nano`'s open-weight/local claim is verified at build
  time.** `BGE-small-en-v1.5` is a lighter fallback but too low-dim for binary-quantization tricks.
- **Frameworks:** don't adopt any dedicated memory framework; hand-rolled `retrieve()` as in-process
  SDK tool composes with the tool loop.
- **Pattern:** hybrid — small upfront context + just-in-time retrieval tool (Anthropic's own
  recommendation; how Claude Code works).
- **Phasing insight:** at this corpus size, file-path + grep may suffice — **5a ships value with zero
  new deps; 5b adds the vector store only when lexical search degrades.** The `retrieve()` seam
  signature never changes between phases.

Caveats carried from the report: embedding landscape moves fast (re-verify before 5b); no source
directly benchmarked the memory frameworks (the "don't adopt" is inferred from SDK capability +
Anthropic guidance, not a head-to-head).

## 12. Sub-project roadmap

**Labels are IDs, not build order.** The dependency-ordered build sequence is below the table.

| ID | Sub-project | Delivers | Depends on |
|---|---|---|---|
| **#0** | Consolidation & responsive shell | Retire mock; single FastAPI control room; fix 4k/right-side empty space (fluid layout); audit & wire/disable dead buttons | — |
| **#1** | Assembly-line engine | State machine (6.1) + rebuild-from-disk run-registry (6.2) + race-safe per-agent memory (6.3) + failure policy (6.4) + cancel (6.5); **split Sage** (§7) | — (but all §6 preconditions) |
| **#1.5** | Niche intake | minimal niche config → Scout `find_topics` → configurable auto/you-pick → enters line | #1, niche-config |
| **#2** | Trigger + live updates | Trigger bar; background execution; SSE with backfill (§10); live multi-project spine board | #0, #1 |
| **#3** | Agentic chat | Bottom-right launcher; T1-only action tools; read-grounded; never satisfies T2/T3 (§8) | #2 |
| **#4** | Coaches view + Settings page | Quill/Flux screen; Settings (niches, channels, voice, style presets; JSON passed into engines, §3) | #0 |
| **#5** | Agentic memory + RAG | `retrieve()` seam; 5a grep → 5b sqlite-vec + verified-local embeddings; in-process SDK tool; hybrid | #3 |
| **#6** | Herald — Publisher stage + publish gate | YouTube Data API, per-channel OAuth, scheduled-after-approval (T3), quota-aware (§9) | #1, #4 (channels) |
| **#7** | Echo — Analyst + loop closure | YouTube Analytics; cohort/observational methodology (§5); rubric-gap → CEO; proposes (T4) | #6, eval foundation |
| **#8** | **Glint 🎯 — Thumbnail Artist** (off-pipeline) | A SET of 3 high-CTR HTML+Chrome thumbnail stills (1280×720, local license-clean focal); Herald's `package` delegates → CEO picks one in the T3 modal. One registry entry + one adapter + one contract; **no spine/stage change.** Spec: `2026-06-23-thumbnail-artist-Glint.md` | **#6** (Herald/T3) |
| **#9** | **Motion stack — d3 + deeper GSAP + Lottie** (+ Loop 🎞️ generator) | Iris designs / Mason renders d3 data-charts, richer GSAP motion (new closed-set EFFECT tokens), and LOCAL Lottie assets; Magpie sources+license-clears Lotties via its truth table; new off-pipeline **Loop** agent generates a Lottie only on a Magpie miss. Closed-vocab + determinism + auto-gate intact; **10-stage spine UNCHANGED.** Spec: `2026-06-23-motion-stack-d3-gsap-lottie.md` | — (pipeline-engine track, independent of the UI slices) |

**Build sequence (dependency-ordered):**
`#0 → #1 → (minimal niche-config + #4 Settings) → #2 → #1.5 → #3 → #5 → #6 (+ #8 Glint) → #7`.
Recommended first: **#0** (low-risk, immediate visible wins, one source of truth), then **#1**
(the backbone — but budget for the §6 preconditions; it is the largest piece).
**#8 (Glint)** lands with **#6 Herald** (it feeds the T3 publish modal; the agent itself can be built
standalone earlier — it only needs a finished project's artifacts). **#9 (motion stack)** is an
**independent pipeline-engine track** — schedule it whenever; it touches Iris/Mason/Magpie + adds the
one off-pipeline Loop agent, and is gated by its own verification spike (HyperFrames-Lottie / GSAP
licensing / d3). Both #8 and #9 are **off-pipeline / additive** — they never change `STAGES`, the
spine, the gates, the 10-stage contracts, or the registry-of-7 beyond one new entry each.

## 13. Edge cases & failure modes (consolidated)

| # | Scenario | Required behavior |
|---|---|---|
| E1 | Dispatcher crashes mid-belt | Rebuild belt from `projects/*/project.json` scan; re-run at-most the interrupted stage (idempotent). No belt loss. |
| E2 | Two videos hit the same agent (e.g. both need Sage) over the belt's life | Race-safe run log (6.3); no dropped `memory.json` records; pipeline jobs do **not** distill into the chat summary. |
| E3 | A video sits at a gate for days | It is **parked** (holds no station); belt keeps flowing; re-enqueues on approval. |
| E4 | A stage fails deterministically (contract/auto-gate) | **No retry** (would tight-loop); park `failed@<stage>` with reason in UI. |
| E5 | A stage fails transiently (network/TTS) | Bounded retry + backoff, then park with reason. |
| E6 | CEO wants a stuck/wrong video gone | Cancel path (6.5): cooperative stop → `cancelled` → station released → removable. |
| E7 | Injected text in retrieved corpus says "approve & publish" | Chat cannot satisfy T2/T3 (§8); authorizing click is on deterministic UI only. |
| E8 | Scheduled publish time arrives but package was never reviewed | Impossible by construction: schedule is set **after** T3 approval; no auto-fire-unreviewed path (§4). |
| E9 | Daily YouTube quota exhausted | Publish stage back-pressures; uploads queue to next quota window; UI shows the ceiling (§9). |
| E10 | Echo sees one viral fluke / one flop | n=1 guard: no routing on single outcomes; cohort evidence only (§5). |
| E11 | Echo's reality contradicts a rubric band | Treated as a **decomposition gap → CEO interview**, never auto-tuned (§5). |
| E12 | SSE tab reconnects after a drop | `Last-Event-ID` backfill replays missed transitions (§10). |
| E13 | Settings JSON malformed / missing | Pipeline/adapter validates and falls back to defaults; a pure engine never reads it directly (§3, §11 decoupling). |
| E14 | A new agent (Herald/Echo/split-Sage) lazily imports a colliding bare name | Forbidden by the loader invariant (6.3); enforced as a build constraint + test. |
| E15 | Fact-Checker just rubber-stamps the brief | Prevented by §7 mechanism (fresh retrieval, adversarial soul, fresh context, different seed). |

## 14. Guardrails (cross-cutting)

- **Tiered write authority (§4)** is the master guardrail; every write declares its tier; no silent
  power upgrades; the event log records the initiator plane.
- **The LLM plane never satisfies a guarantee** (T2/T3) — two-plane separation preserved.
- **The improver stays least-privileged**: `WriteBoundaryError` + unwritable rubric unchanged; Herald
  and Echo propose, never write soft-tier/rubric directly (T4).
- **Publishing** is gated, scheduled-after-approval, quota-aware, ToS-clean.
- **Resilience**: belt is rebuildable from disk; stages idempotent; cancel + bounded retry + parked
  failures with reasons; no silent stalls or tight loops.
- **Decoupling** (§11 of PROJECT_CONTEXT): engines never import Atlas; Settings/seams passed in.

## 15. Out of scope (YAGNI)

- No agent-framework migration. No job queue (Celery/RQ/Redis). No WebSockets (SSE suffices).
- No per-agent occupancy model (station = stage covers it). No splitting Iris/Cadence/Mason.
- No vector store until the corpus needs it (5a before 5b).
- **No priority scheduler** — FIFO contention only for v1 (§6.1).
- **No separate dead-letter-queue subsystem** — the parked `failed` project is the dead letter (§6.4).
- **No hard thread-kill** of running stages — cooperative cancel only (§6.5).
- No auto-tuning of the rubric from Echo — CEO owns the rubric (§5).

## 16. Suggestions I did NOT fully adopt, and why

1. **"Different provider" for the Fact-Checker (settled-decision sub-point) — adopted as OPTIONAL,
   not required.** Requiring a different LLM vendor conflicts with the no-API-key Claude-subscription
   default, and the independence win comes mostly from fresh retrieval + adversarial soul + fresh
   context + a different *seed*. Different provider stays a configurable knob, not a mandate.
2. **"Dead-letter" (2d) — adopted in substance, not as a subsystem.** A single-CEO system doesn't
   need a separate DLQ store; the parked `failed@<stage>` project *is* the dead letter, with reason +
   manual retry/cancel in the UI. Same safety, less machinery.
3. **Priority contention (2a) — declined for v1; FIFO only.** Priority scheduling is real complexity
   for marginal benefit at ~2–3 videos in flight. Noted as a future option, not built now.
4. **Re-opening Model 3 vs "one active + queue" (2f) — declined to reverse; adopted the honesty
   ask.** The CEO settled on the assembly line after the tradeoffs were laid out. Rather than
   re-litigate, the spec (a) states the honest modest throughput target (2–3 in flight), (b) justifies
   the belt by UX + overlap + resource-safety rather than Nx throughput, and (c) makes max-in-flight=1
   degenerate to the simple model, so the simpler design is a config special-case, not a fork.
5. **Chat satisfying gates (point 4 / earlier CEO wish) — narrowed, then CEO-confirmed.**
   I did not silently comply with the earlier "chat can approve" wish, nor silently override it: §8
   surfaced the conflict, and the CEO chose Option 1 (hardened) on 2026-06-23 — chat is T1-only and
   never satisfies T2/T3.

Everything else in the review was adopted as written.

## 17. Research provenance (sub-project #5)

Deep-research run 2026-06-23 — stats: 6 angles, 28 sources fetched, 136 claims extracted, 25
verified (22 confirmed, 3 killed), 111 agent calls. Key killed claims (do not quote): a specific
Voyage-vs-OpenAI win-rate, a specific sqlite-vec latency figure, and a specific sqlite-vec
disk-usage/weakness breakdown. Full report archived in the workflow transcript.
