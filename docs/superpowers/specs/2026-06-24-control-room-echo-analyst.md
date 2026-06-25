# Control Room — #7 Echo 📈 the Analyst (closes the self-improvement loop)

**Date:** 2026-06-24
**Status:** Spec for review (design locked via the forks in §1; not yet built)
**Author:** CEO + Atlas (brainstorming session)
**Branch:** `control-room` (continues Slices 0–6)
**Depends on:** **#6 Herald** (real uploads to measure + the per-channel OAuth token store) ·
the **eval foundation** (PROJECT_CONTEXT §13) · the **Slice-6 T4 surface** (the proposal envelope,
the `echo_fn` seam, the accept path — `2026-06-24-control-room-slice6-coaches-echo.md`).
**Master spec:** `2026-06-23-control-room-design.md` (§4 tiers · §5 loop + Echo methodology ·
§9 YouTube/`yt-analytics.readonly` · §12 #7 · §13 E10/E11).

---

## 0. One-paragraph summary

Echo is the agent that closes the loop: it reads **real-world YouTube performance** and turns it into
**proposals the CEO approves** — never an auto-write. It is purely **additive**: ONE registry entry
(`analyst` 📈) + ONE adapter (a sibling to the coaches and Vera, **not** a pipeline stage) + a
read-only **performance report** artifact. It lives **behind the Slice-6 `app.state.echo_fn` seam**:
Slice-6 already built the unified proposal envelope, the card UI (soft proposal + rubric-contradiction
flag), and the single guarded accept path; #7 builds the **real engine behind that seam**. Echo's
defining property is that it has a **distinct methodology**: it must **NOT** reuse the reproducible-eval
guardrails (`tracking.noise_floor`, the held-out/K≥5 gates), because reality is one-shot, confounded,
and weeks-late. Instead Echo reasons **observationally over cohorts** (groups of like uploads),
acknowledges confounders honestly, and routes only **aggregate** evidence via **Tier 4**. Its single
most valuable output is a **rubric contradiction** — which is permanently a **CEO-interview item**, not
a button, because the rubric is CEO-owned and structurally unwritable.

---

## 1. Locked decisions (this session)

| # | Fork | Decision |
|---|---|---|
| **A** | **Cohort segmentation axis** | **`channel × format`.** Echo compares like-with-like only: same channel AND same length-format (`short` ~60–90s / `long` ~5–8min). This controls the two biggest structural confounders (channel size, format design space — the two-target-formats reality). It fills slower → honest that meaningful output is weeks out. (§6.2) |
| **B** | **Trigger model** | **On-demand now + same-seam cron later, both propose-only.** A CEO-initiated **"Refresh Echo"** (`POST /api/echo/refresh`) pulls the latest analytics and emits proposals + a report; a future cron driver uses the **identical** propose-only seam + store. No auto-apply on either path. (§6.5, §11) |
| **C** | **Methodology** | **Cohort/observational — explicitly NOT the eval guardrails.** Echo does not import `tracking.noise_floor` or any holdout/reproducibility gate. The n=1 guard (E10) and the min-N/window threshold are Echo's own discipline. (§5) |
| **D** | **Write authority** | **Tier 4 — propose only.** Echo emits the Slice-6 envelope; the CEO accept is the only write (`loop.apply_soft_change`). Rubric contradictions are `acceptable:false` (no write path). The rubric stays unwritable. (§4, §8, §9) |
| **E** | **Shape** | **One registry entry + one adapter + a read-only report.** Sibling to the coaches; NOT a `STAGES` entry; never mutates a project/rubric/contract/spine. (§3) |
| **F** | **Secrets** | **Tokens passed in, never read globally; redacted everywhere.** Echo reuses Herald's per-channel OAuth refresh tokens (`yt-analytics.readonly`); the token store is a **passed-in dependency contract** (§6.4), since #6 Herald owns it. |

---

## 2. Architecture rules this spec respects (re-stated, non-negotiable)

- **Two planes (PROJECT_CONTEXT §3).** The LLM does judgment; the deterministic plane does
  guarantees. Echo is judgment — it **proposes**; it never satisfies a guarantee.
- **Tiered write authority (master §4).** Echo is **T4**: the existing `WriteBoundaryError` boundary +
  CEO approval, unchanged. Echo and Herald **cannot route around it** — they propose; they never write
  soft-tier or the rubric directly. `loop.can_write_rubric()` **stays true** and is asserted in tests.
- **The improver stays least-privileged.** Echo is *more* observational and *less* privileged than the
  internal eval, not more. It adds external ground truth but gains **no new write power** (§5.4).
- **Additive only.** Add ONE `registry.AgentEntry` + ONE `adapters/analyst.py` + the Echo engine in a
  sibling project dir + dashboard read/propose plumbing. **No edits** to `pipeline.py`, `STAGES`,
  contracts, gates, `rubric/__init__.py`, `eval/loop.py`, `eval/rollup.py`, `eval/diagnose.py`,
  `eval/tracking.py`, or the coach adapters. Every engine/LLM/analytics touch is an **injectable seam**.
- **Decoupling (PROJECT_CONTEXT §11).** Echo's engine **never imports atlas**; Atlas owns the contract
  and stamps/validates the report at the adapter boundary (mirrors Sage/Vera). Settings/tokens are
  **passed in**, never read globally by the engine.
- **The loader invariant (master §6.3, E14).** Echo adds no lazy import of a colliding bare name at
  call time and no mutable module-level globals two concurrent videos could stomp. (Echo is
  off-pipeline and cohort-batch, so it never runs inside the belt's per-video concurrency — but the
  invariant is stated and tested anyway.)
- **Secrets never in UI/logs.** Refresh tokens are redacted through the existing `J()` redact pass;
  no token is surfaced in any report, event, card, or log line (§6.4, E27).
- **e2e gotchas (carried).** Inject fakes for every seam (`echo_fn`, `analytics_fn`); never hit the
  real YouTube Analytics API; `ANTHROPIC_API_KEY` / YouTube creds are **never set in tests**;
  `wait_until="domcontentloaded"`; restart the server after any backend change (no `--reload`).

---

## 3. What Echo IS (the additive shape)

Echo mirrors **Vera the Reference Analyst** exactly in topology: a delegable agent that is **not** a
pipeline stage. Vera *defines* the standard from reference videos; Echo *reads back reality* from
published videos. Both are one registry entry + one adapter, and neither touches `STAGES`.

```
registry.AgentEntry(
    name="analyst", display="Echo", emoji="📈",
    project_dir=<repo>/analyst,                 # sibling engine + soul/ (never imports atlas)
    adapter_cls=AnalystAdapter,
    role="Performance Analyst (loop closure)",
    jobs=[ JobSpec("analyze_cohorts",  "analyst_analyze_cohorts", …),   # pull + aggregate + hypothesize
           JobSpec("report",           "analyst_report", …) ],          # render the CEO report
)
```

- **`adapters/analyst.py`** — wraps the engine like `adapters/reference_analyst.py`: loads the engine
  lazily via the loader, runs the job in-process, stamps `schema_version`, validates the report against
  a **frozen `performance_report.schema.json`** contract at the boundary, returns a compact digest.
  The adapter receives the analytics seam + tokens as **call params** (decoupling): it never reaches
  into the dashboard or a global token store.
- **The engine (`analyst/analyst.py`)** — pure, offline-testable: given **cohort analytics rows**
  (passed in), it (a) aggregates into per-cohort signals, (b) applies the diagnosis map to generate
  *hypotheses*, (c) detects rubric contradictions, and (d) renders the report. It does the **YouTube
  pull only through an injected `analytics_fn`** so the whole engine runs with canned data and zero
  network in tests.
- **`soul/` (SOUL/STYLE)** — Echo's persona: a sober, confounder-aware analyst — *"correlation is not a
  greenlight; show me the cohort or I say nothing."* SOUL is engine-shared; STYLE/examples are
  chat-only (the established soul-persona split).

Echo surfaces in the dashboard on the **Coaches view's Echo lane** (built in Slice-6) + a small
**Echo report panel**; it adds no new top-level rail entry.

---

## 4. Write-tier mapping (Echo is T4 — propose, never write)

| Echo output | Tier | Guard / mechanism |
|---|---|---|
| **Soft-tier coach addendum** (from cohort evidence) | **T4** | Emits the Slice-6 `source:"echo"`, `kind:"soft_addendum"` envelope into the proposals store. The **CEO accept** is the only writer (`POST /api/proposals/{id}/accept` → `loop.apply_soft_change`). Echo writes **no** persona file itself. |
| **Rubric contradiction** (reality vs a rubric band) | **T4 (read-only escalation)** | `kind:"rubric_contradiction"`, `acceptable:false`, `addendum:null`, `soft_path:null`. **No Accept button exists** — the absence is the guarantee. Acknowledge/Dismiss only. (§9, E11/E18.) |
| **Performance report** | **read-only artifact** | A dashboard-owned JSON Echo writes (text/report only). It mutates **no** project, rubric, contract, or spine file. (§10.) |
| **Analytics pull** | **read-only external** | `yt-analytics.readonly` only — Echo never publishes, edits, or deletes anything on YouTube. (Herald owns the write scopes; Echo reuses only the read scope.) |

**Cross-cutting audit (master §4):** a T4 accept that originated from an Echo proposal is recorded on
the dispatcher event ring with `initiator="ceo"`, `tier="T4"`, `source="echo"` — so the audit shows no
T4 write ever originated from the LLM/analytics plane. Echo's own refresh is `initiator="ceo"`
(CEO-initiated) or `initiator="cron"` (the future driver), tier `read` — it writes only the report +
pending proposals, never a persona/rubric file.

---

## 5. Echo's distinct methodology — the crux (structural separation)

This is the section the whole sub-project exists to get right. Echo and the internal eval **cannot
share statistics**, and the spec makes that separation **structural, not advisory**.

### 5.1 Why the eval guardrails do not apply
The internal eval's central safety number is the **judged noise floor**: `tracking.noise_floor()`
literally means *"run the held-out set K≥5× and read the variance of `measured_value`"* (verified in
`atlas/eval/tracking.py`). The `decide()` gate in `eval/loop.py` then requires a judged move to exceed
`sigma·σ`, and the held-out `verify_fn` requires the change to **generalize across re-runs**. **Every
one of these assumes the experiment is reproducible.**

Reality is not reproducible. **You cannot publish the same video five times.** Each real-world datapoint
is **one-shot, non-reproducible, and confounded** by topic, thumbnail surfacing, channel size,
seasonality, and the recommendation algorithm — and it **arrives weeks late**. Applying the
noise-floor/holdout machinery to it would be a category error: there is no σ to measure because there
is no K.

### 5.2 The structural separation (prove it, don't assert it)
- **Echo's engine and adapter MUST NOT import `eval.tracking`** (nor call `noise_floor`, `decide` with a
  `noise_floor=`, or any held-out `verify_fn`). This is enforced by an **import-boundary test**: the
  test imports `analyst.analyst` and `adapters.analyst` and asserts `eval.tracking` is **not** in their
  transitive module graph (verified achievable — `eval.diagnose`/`eval.rollup` import only
  `rubric`/`eval.types`, never `tracking`, so Echo can **import them read-only** to reuse the escalation
  concept without pulling the noise floor; §5.4 chooses import-not-mirror). The test additionally scans
  for **calls to** `noise_floor(`/`verify_fn=`/`sigma=` (an AST/call scan, **not** a raw substring grep
  — Echo's report prose may legitimately contain the word "sigma"/"noise"). The separation is a CI gate,
  not a comment.
- **Echo never calls `run_loop`.** The internal loop's reproducible gates are for the internal proxy.
  Echo's evidence is the **cohort aggregate** (§6.3), carried in the envelope's `evidence.cohort` field
  (the Slice-6 contract already reserves `evidence.cohort` and leaves `evidence.verdict`/`held_out`
  null for Echo items).
- **Echo writes nothing on its own.** It has no `apply_soft_change` call. The only writer is the
  Slice-6 CEO accept endpoint. (Structural proof carried from Slice-6 §4.4.)

### 5.3 Echo's OWN discipline (what replaces the noise floor)
1. **Cohort/aggregate only.** A claim is about a *pattern across N like uploads* ("this hook shape
   underperforms across the last N shorts on this channel"), **never** a single video.
2. **The n=1 guard (E10/E25).** A cohort below **min-N** (a placeholder constant `ECHO_MIN_COHORT_N`,
   see O1) produces **no proposal** — only an `under-powered` note on the report. One viral fluke or
   one flop routes nothing. `proposals_store.normalize_echo_proposal` already drops single-outcome
   items at the seam (Slice-6 E17); Echo additionally never *emits* one.
3. **Confounder honesty (E26).** Every cohort signal on the report carries an explicit
   **confounder/caveat block**: sample size, window, and the named confounders Echo could not control
   (topic mix, surfacing, channel-size drift, seasonality, sparse/late data). Echo **states correlation,
   never causation** — a hypothesis is phrased as *"candidate cause … worth a coaching experiment,"*
   not *"this caused …."*
4. **Windowed + late-data aware.** Cohorts are computed over a rolling window (`ECHO_WINDOW_DAYS`,
   O1); a cohort whose newest upload is younger than an **analytics-maturity lag** (`ECHO_MATURITY_DAYS`
   — retention/CTR keep moving for days/weeks after publish) is flagged **`immature`** and excluded
   from proposing (surfaced, not proposed).

### 5.4 The deep point — rubric contradictions are CEO-owned (E11)
When Echo's real retention **contradicts** a rubric band (e.g. retention is fine where `hook_strength`
says the hook should fail), **the rubric is wrong** — a **ground-truth decomposition gap**. This is
exactly the one thing the improver structurally *cannot* fix: the CEO owns the rubric and there is **no
write path** (`rubric/__init__.py` exposes no writer; `apply_soft_change` refuses every rubric path;
`can_write_rubric()` stays true). So Echo's most valuable insight is **permanently a CEO-interview item,
not a loop item** (§9). This reuses the existing `decomposition_gap → escalate_to_ceo` concept in
`rollup.py`/`diagnose.py` — Echo **imports `eval.diagnose` read-only** (it's `tracking`-free, §5.2) and
reuses the *escalation* shape; it adds no new fix path and edits neither module.

> **Prove nobody can wire Echo into auto-tuning the rubric.** Three independent walls: (1) `rubric`
> has no writer; (2) `apply_soft_change` raises `WriteBoundaryError` on every rubric/contracts/spine
> path regardless of caller; (3) a `rubric_contradiction` proposal is `acceptable:false` so the accept
> endpoint returns 409 before any writer is reached (Slice-6 E18). A negative-safety test asserts all
> three. Echo cannot become an auto-tuner even if its engine is later changed.

---

## 6. The analytics pipeline (pull → cohort aggregate → signals)

### 6.1 What Echo pulls (read-only, per channel)
Via the YouTube Analytics API (`yt-analytics.readonly`), per published video:
- **Audience-retention curve** (relative/absolute retention by elapsed ratio) — the intro-drop and
  mid-sag signals.
- **CTR** (impressions click-through rate) + impressions — the packaging signal.
- **Average view duration / % viewed** — the overall stickiness signal.
- **Audio-related drop-off proxy** — derived from the retention curve at known audio-event timestamps
  (true per-modality audio analytics are limited; Echo uses retention-at-timestamp as the proxy and
  says so on the report).
- Identity/joins: `videoId → project slug` (from Herald's publish record), `channelId`, `format`
  (short/long from the project length), publish timestamp.

### 6.2 Cohort definition (Fork A — `channel × format`)
A **cohort** is the set of published videos sharing the same `(channelId, format)` within the rolling
window. This is the unit Echo aggregates and reasons over. Rationale: channel size and short-vs-long are
the two confounders large enough to manufacture false signals on their own; holding both constant is the
cheapest honest comparison. **The cohort key is a structured tuple** (`channelId`, `format`) so a third
axis — **`hook-shape`** is the chosen next refinement (O5) — is **additive, not a rewrite**. v1 keeps
the axis coarse and trustworthy; finer segmentation waits for real cohorts to exist.

### 6.3 Aggregation → cohort signals
For each cohort with **n ≥ min-N** and **mature** data, Echo computes robust aggregates (median + IQR,
not mean — one outlier must not move the signal):
- `intro_retention_drop` — median retention loss over the first ~10–15% of the video.
- `mid_retention_sag` — median retention slope through the middle third.
- `ctr_band` — median CTR vs the cohort's own rolling baseline (Echo compares a cohort to **its own
  history**, never to an absolute number it can't justify). **The baseline's own trend is reported
  alongside (review F4)** — comparing to self is blind to slow collective decline, so a falling baseline
  is surfaced explicitly: "in band vs my baseline, but my baseline fell 40% this quarter" must be
  visible, not masked.
- `audio_dropoff` — median retention dip at audio-cut timestamps.
Each signal carries `{n, window, median, iqr, maturity, confounders[]}`. **Under-powered or immature
cohorts are reported, never proposed** (§5.3).

### 6.4 The token-store contract (passed-in dependency; Herald owns it)
Herald (#6) owns the per-channel OAuth token store (master §9: one encrypted refresh token per
`channelId`, `youtube.upload` + `force-ssl` + `yt-analytics.readonly`, the
`connected|needs-reconnect|expired|revoked` state machine). **The Herald spec does not exist yet**, so
Echo's spec **defines the contract it consumes** and depends on nothing more:

```
TokenProvider (passed in — Echo never reads a global):
  list_channels() -> [ {channelId, label, format_hint?, status} ]
  analytics_token(channelId) -> {access_token, ...}   # refreshed by Herald; Echo only reads
  # Echo NEVER sees youtube.upload/force-ssl tokens; only the analytics read path is exposed to it.
```

- Echo receives a `TokenProvider` (or, in the dashboard, the `analytics_fn` seam that wraps it) as a
  **call parameter**. Tokens are **never** surfaced in a report/card/event/log; the `J()` redact pass
  covers any accidental inclusion (E27).
- If a channel's token is `expired|revoked|needs-reconnect`, Echo **skips that channel's cohorts** and
  notes `channel unavailable — reconnect in Herald` on the report (never a 500; E28). This degrades
  exactly like the Slice-6 `echo_fn=None` empty-lane behavior.
- **Until #6 ships,** the `TokenProvider`/`analytics_fn` is the **fake seam**: a canned cohort-data
  provider. Everything in §6.3/§7/§8/§9 is buildable and testable against it now.

### 6.5 The band ↔ real-metric attribution map (the hard, explicit part — review F5)
The rubric bands measure **artifacts** (`info_density`, `words_per_scene`, `speech_cadence`, loudness,
motion — verified in `rubric.py`/`rollup.py`). They do **not** predict retention or CTR. So there is **no
free function** from a real cohort signal (e.g. "intro retention dropped 38%") to a rubric band or a
coach. That mapping must be **authored explicitly and conservatively**, and v1 keeps it deliberately
small and honest:

- **`ATTRIBUTION` is an explicit, CEO-reviewable table** in `analyst/` (not inferred): `real_signal →
  {candidate stage/band, confidence: low|med}`. It is a **hypothesis lookup**, never a proven cause. It
  maps only the signals where a defensible artifact link exists (e.g. `intro_retention_drop →
  script-stage hook framing`); a signal with **no** defensible artifact link (most of CTR/packaging) is
  **report-only**, never routed.
- **The map's existence does NOT assert causation.** A routed signal becomes a *candidate proposal the
  human still judges*; Echo never claims the artifact band caused the real outcome. The diagnosis map
  (§7) is exactly this table applied at cohort scale.
- **Bands are validated to exist at build time.** Any `band_id` the attribution table names is asserted
  to be a real entry in `rubric.bands()` (E40) — a renamed/absent band fails CI, so the spec never ships
  a `hook_strength` that the rubric doesn't actually have. (Whether a *hook* band should exist at all is
  a CEO-owned rubric question, O8 — and a missing one is itself a §9 decomposition gap.)

### 6.6 Trigger model (Fork B — on-demand + same-seam cron)
- **On-demand:** `POST /api/echo/refresh` (CEO, T1-ish read-trigger) runs
  `proposals.refresh_echo(echo_fn, …)` — pull → aggregate → hypothesize → upsert pending proposals +
  write the report. Mirrors Slice-6's on-demand `POST /api/coaches/{name}/propose`. Writes **no** persona
  file; emits proposals + the report only. **The pull is network + LLM**, so the endpoint runs it off the
  event loop via `asyncio.to_thread` (mirrors `intake.py`'s Scout finder, review F9) — it never blocks
  the SSE/dashboard.
- **Analytics quota (review F8).** `yt-analytics.readonly` queries draw on the **same** ~10k-units/day
  project bucket as Herald's `videos.insert` (master §9), so an unbounded refresh could starve publishing.
  Echo therefore **caches mature cohort pulls** (a mature video's retention/CTR is settled — never
  re-fetched), only queries new/immature videos, and a refresh that would exceed a per-run query budget
  **back-pressures and reports `quota-deferred`** rather than failing (E37). The report shows units spent.
- **Cron (future):** a thin driver calls the **same** `refresh_echo` seam on a schedule and lands in
  the **same** store. It does not auto-apply (Fork A's "one production write path" holds). Shelled to
  #7-or-later; the on-demand path proves the identical flow now.

---

## 7. The diagnosis map (a hypothesis generator, cohort scale, routed via T4)

The map from master §5 is **the §6.5 `ATTRIBUTION` table applied at cohort scale** — applied **only** to
cohort signals that cleared the n=1/maturity guards, generating **candidate proposals**, never
auto-applying. Each row is a *hypothesis lookup* (an artifact-link of stated confidence), not a proven
cause; the human draws the final causal line:

| Real cohort signal | Hypothesised cause | Candidate route (proposal target) |
|---|---|---|
| Low CTR (vs cohort baseline) | weak title/thumbnail | **Herald** packaging / **Iris**+**Glint** (thumbnail) — surfaced as a packaging hypothesis on the report; a coachable slice (e.g. thumbnail-relevant rubric band) becomes a `soft_addendum` only if it maps to a soft-tier owner |
| Intro retention drop | weak hook | **Quill** (editorial `soft_addendum`) — **OR** a **rubric gap → CEO** if reality contradicts the `hook_strength` band (§9) |
| Mid-video sag | pacing/script | **Quill / Marlow** (editorial `soft_addendum`) |
| Audio drop-off | mix/voicing | **Flux** (production `soft_addendum`) |

**Routing discipline:**
- Echo maps a signal to an **owning stage**, then reuses `loop.coach_for_stage(stage)` →
  `editorial_coach`/`production_coach` so an Echo soft proposal targets the **same coach** a diagnosed
  internal shortfall would (one consistent owner; no two coaches optimizing blind — mirrors
  `diagnose.pick_primary_target`'s single-owner rule).
- The **direction** still comes from the CEO-owned rubric band, not from Echo — Echo supplies *cohort
  evidence + the band it concerns*; the proposal's `direction` is derived the same way the loop derives
  it (the band decides; Echo, like a coach, proposes the text only).
- **The soft_addendum / contradiction dichotomy (review F6).** A `soft_addendum` is coherent **only when
  reality and the rubric point the same way** — the rubric says "move band X" *and* the cohort evidence
  corroborates that moving X would help reality. When reality and the rubric **diverge** (reality says
  the band is fine / wrong), that is **not** an addendum — it is a `rubric_contradiction` (§9). Echo
  never authors an addendum against a band that real performance contradicts; the two kinds are mutually
  exclusive by construction.
- A signal with **no clean soft-tier owner** (e.g. pure thumbnail/packaging, owned by Herald/Glint, not
  a soft-tier persona) is surfaced as a **report hypothesis only** — not a `soft_addendum` (there's no
  soft-tier file to write), keeping the T4 write path honest.

---

## 8. The T4 proposal path (Echo → Slice-6 envelope → CEO accept)

Echo emits the **exact Slice-6 unified envelope** (`source:"echo"`), so it reuses the store, the card
renderer, the accept/reject/acknowledge lifecycle, and the negative-safety tests with **zero new write
surface**:

```jsonc
{
  "source": "echo",
  "kind": "soft_addendum",                 // or "rubric_contradiction"
  "tier": "T4",
  "band_id": "script:hook_strength",       // the CEO-owned band the cohort signal concerns
  "stage": "script", "owner": "Marlow", "coach": "editorial_coach",
  "direction": "RAISE hook_strength toward band centre (cohort intro-drop median 38% over n=7 shorts)",
  "evidence": {
    "verdict": null, "held_out": null,     // Echo does NOT use the reproducible gates (§5)
    "cohort": {                            // aggregate ONLY (E10/E25)
      "channelId": "UC…", "format": "short", "n": 7, "window_days": 30,
      "signal": "intro_retention_drop", "median": 0.38, "iqr": 0.09,
      "maturity": "mature",
      "confounders": ["topic mix uncontrolled", "surfacing varies", "algorithm"],
      "baseline": "cohort rolling median 0.22"
    }
  },
  "addendum": "## Coach note …",           // authored by the owning coach (via the coach seam), soft-tier
  "soft_path": ".../scriptwriter/COACH_ADDENDUM.md",
  "acceptable": true,
  "accept_reason": "Soft-tier persona addendum from cohort evidence; CEO accept performs the only write."
}
```

- **Authoring the addendum text.** Echo supplies `{band_id, direction, cohort evidence}`; the **owning
  coach** authors the persona text through its existing `propose_addendum` job (the same path the
  internal loop uses via `loop.delegate_to_coach`). Echo does not write coaching prose itself — it
  brings the evidence; the coach writes the note; the CEO writes the file. (If the coach seam is
  unavailable, Echo falls back to a deterministic rule note, mirroring `loop.propose_fix`.)
- **Accept = the one write.** `POST /api/proposals/{id}/accept` (Slice-6 §4.4) calls
  `loop.apply_soft_change(soft_path, addendum)` — the single guarded writer. A tampered `soft_path`
  raises `WriteBoundaryError` → 409 (Slice-6 E16). Echo never reaches a writer.
- **Dedupe.** The store's `(source, band_id, addendum-hash)` dedupe (Slice-6 §7.1) means a re-run that
  re-derives the same cohort proposal supersedes the prior pending card instead of piling up.

---

## 9. Rubric contradiction → CEO interview (Echo's highest-value output, E11)

When a cohort signal **contradicts** a rubric band, Echo emits a `rubric_contradiction` card, not a fix:

- **Detection.** Echo holds the *real* outcome (e.g. cohort intro-retention is healthy) against the
  band's *predicted* verdict (the band says the hook property should fail / pass). A **sustained,
  cohort-scale, mature** disagreement is a decomposition gap — *the rubric is measuring the wrong thing
  or missing a term.* **Sensitivity (O2):** Echo raises the interview item only when **≥2 consecutive
  mature cohorts agree** on the disagreement (one-off or immature disagreements are not contradictions —
  same n=1/maturity discipline as §5.3). This avoids interview spam without letting a wrong band linger.
- **Shape.** `kind:"rubric_contradiction"`, `acceptable:false`, `addendum:null`, `soft_path:null`,
  `evidence.cohort` populated, plus a `contradiction` block: `{band_id, band_predicts, reality_shows,
  cohort, sustained_over}`.
- **Card (Slice-6 §6.2).** Renders as a **CEO-INTERVIEW flag**: the contradicting band, the cohort
  evidence, and the read-only actions **Acknowledge** (track; status→`acknowledged`; **no write**) and
  **Dismiss**. **There is no Accept button** — and the absence is the guarantee. Plus the lock line:
  *"🔒 The rubric is CEO-owned — this is an interview item, not a tunable. There is no write path."*
- **Acknowledged items have a persistent home (review F14).** `acknowledge` does **not** make the
  highest-value output vanish — it moves the item into a **read-only "CEO interview queue"** on the Echo
  lane (a filtered view of `status=acknowledged` rubric_contradiction items). That queue **is** the
  agenda for the eventual human rubric interview; an acknowledged item stays visible there until the CEO
  dismisses it (after editing `rubric.json` by hand). Without this, acknowledging would drop the insight
  on the floor.
- **Why it's the most valuable output.** It is the only signal that can correct the *standard itself* —
  but correcting the standard is a **human** act (the CEO edits `rubric.json` by hand, outside the
  dashboard, after the interview). Echo surfaces the gap with evidence; it never closes it. This is the
  honest limit of an improver that is less privileged than its own success criterion.

---

## 10. The CEO report + performance contract (frozen-but-extensible)

Echo writes ONE read-only artifact per refresh: a **performance report** (text/report only; mutates no
project/rubric/contract). Validated at the adapter boundary against a frozen
`performance_report.schema.json` (mirrors how Vera's rubric is validated against `reference_rubric`).

```jsonc
{
  "schema_version": "1.0",
  "generated": 1750000000,                 // stamped store-side (dashboard plane)
  "window_days": 30, "min_cohort_n": 5, "maturity_days": 14,   // the discipline constants in force
  "cohorts": [
    {
      "channelId": "UC…", "channel_label": "…", "format": "short",
      "n": 7, "status": "mature",          // mature | immature | under-powered | unavailable
      "signals": {
        "intro_retention_drop": {"median": 0.38, "iqr": 0.09, "baseline": 0.22},
        "ctr": {"median": 0.041, "baseline": 0.052},
        "mid_retention_sag": {...}, "audio_dropoff": {...}
      },
      "confounders": ["topic mix uncontrolled", "surfacing varies", "seasonality", "algorithm"],
      "hypotheses": [ {"signal": "intro_retention_drop", "cause": "weak hook",
                       "route": "editorial_coach", "became_proposal": "prop-0012"} ],
      "contradictions": [ {"band_id": "script:hook_strength", "interview_item": "prop-0013"} ]
    }
  ],
  "skipped": [ {"channelId": "UC…", "reason": "token needs-reconnect (Herald)"} ],
  "notes": "Observational, confounded, lagged. Not an experiment — no causal claim."
}
```

**Performance contract (the durable promise the report keeps):**
1. **Observational, never causal** — every signal ships with its confounders; no field claims causation.
2. **Cohort-gated** — no signal below `min_cohort_n`; no proposal from an `immature`/`under-powered`
   cohort.
3. **Read-only** — the report writes nothing but itself (a dashboard-owned JSON, gitignored, mirrors
   `settings_store`/`proposals_store`); it never mutates a project, rubric, contract, or persona.
4. **Frozen-but-extensible** — `schema_version` is stamped; new signal keys are additive (a reader
   tolerates unknown keys), exactly like the artifact contracts.

Persistence: a dashboard-owned `control_room_echo_report.json` (latest) + an append-only
`echo_reports.jsonl` history (so the CEO can see drift over time), both injectable via
`app.state.echo_report_path`. (Retention policy = O4.)

---

## 11. Backend — new modules + endpoints (all additive)

### 11.1 The engine + adapter (the real Echo, behind the seam)
- **`analyst/analyst.py`** (sibling project; never imports atlas) — `analyze_cohorts(cohort_rows, rubric_view, *, now)` →
  `{cohorts, hypotheses, contradictions, report}`; pure, offline, no network (the pull is the injected
  `analytics_fn`). Robust aggregation (median/IQR), the n=1/maturity guards, the diagnosis map, the
  contradiction detector. **Does not import `eval.tracking`** (§5.2, asserted).
- **`atlas/adapters/analyst.py`** — `AnalystAdapter(Adapter)`: `run_job("analyze_cohorts", …)` and
  `run_job("report", …)`. Loads the engine via the loader; stamps `schema_version`; validates the
  report against the frozen contract; returns a digest. Tokens/`analytics_fn` arrive as params.
- **`atlas/registry.py`** — ONE `AgentEntry(name="analyst", …)` (the only registry edit; mirrors how
  Vera/the coaches were added — additive, no orchestrator edits, tools just appear). *(This is the one
  exception to "no registry edits" — adding the agent IS the deliverable; Slice-6 explicitly deferred
  "no registry entry for Echo" to #7.)*
- **`atlas/contracts/performance_report.schema.json`** — the frozen report contract (§10).

### 11.2 Dashboard wiring (mostly built in Slice-6; #7 fills the engine)
- **`atlas/dashboard/proposals.py`** — `refresh_echo(echo_fn, projects_dir)` already exists as the
  seam (Slice-6 §7.2). #7 supplies the **real default `echo_fn`**: build the analyst adapter from the
  registry, resolve the `TokenProvider` from Herald's store, pull via `analytics_fn`, run
  `analyze_cohorts`, normalize hypotheses → envelopes (dropping n=1, Slice-6 E17), write the report.
  `echo_fn=None`/raising → `[]` + empty lane (Slice-6 E21), unchanged.
- **`atlas/dashboard/echo.py`** (new, small) — `default_echo_fn` (the real wiring above) +
  `build_report` persistence (latest + jsonl). Read-only; fires nothing external but the analytics
  read pull. Mirrors `publish.py`'s read-only contract.
- **`atlas/dashboard/data.py`** — `echo_report(path)` (read the latest report for the panel, tolerant /
  degraded when absent), and a small `echo_cohorts` summarizer for the Echo lane header.

| Method + path | Tier | Behavior |
|---|---|---|
| `GET /api/echo/report` | read | Latest performance report (or a degraded "no report yet" state). Never 500s. |
| `POST /api/echo/refresh` | read-trigger (CEO) | Runs `refresh_echo` via `app.state.echo_fn`: pull → aggregate → upsert pending proposals → write the report. Writes **no** persona file. `echo_fn=None`/raising → empty result, never 500 (E21/E28). Event ring `initiator="ceo"`, tier `read`, `source="echo"`. |
| *(proposals list/accept/reject/acknowledge)* | T4 / read | **Unchanged from Slice-6** — Echo reuses them verbatim. |

New `app.state` seams (mirror the established `None`-defaulting pattern):
`app.state.echo_fn` (Slice-6) · `app.state.analytics_fn = None` (the canned-cohort fake in tests) ·
`app.state.token_provider = None` (Herald's store, injected) · `app.state.echo_report_path`.
**`ANTHROPIC_API_KEY` / YouTube creds are never set in tests.**

---

## 12. Frontend — the Echo lane (built in Slice-6; #7 fills the data)

Slice-6 already shipped the Echo card UI (the `source:"echo"` soft proposal + the rubric-contradiction
CEO-interview flag) on the **Coaches view's Echo lane**. #7 adds only:
- A **"Refresh Echo" button** on the Echo lane → `POST /api/echo/refresh`, then re-list proposals + the
  report (light confirm; it writes nothing but the report/proposals). **It is a slow, quota-spending pull,
  not an instant action (review F13):** the button shows a **disabled/loading state** while the
  `to_thread` pull runs, a **"last refreshed Xh ago"** stamp so the CEO doesn't spam a weeks-relevant
  pull, and it is **idempotent under double-click / navigate-away mid-pull** (a refresh in flight is
  reused, not duplicated).
- **Three distinct empty states, never one blank panel (review F11).** "No proposals" must disambiguate:
  **(a) never run** ("Echo hasn't analyzed yet — Refresh"), **(b) ran, no mature cohorts**
  ("Not enough mature uploads yet — n/min-N per cohort shown"), **(c) ran, cohorts mature, nothing
  actionable** ("Analyzed N cohorts, no actionable signal"). A blank panel read as "all fine" when it
  means "never ran" or "tokens broken" is the silent-failure-as-green-check trap — each state is labelled.
- A small **Echo report panel** (read-only). **The card hierarchy leads with trust, not drama (review
  F12):** each cohort card leads with **sample size + maturity + confidence** and the **confounder caveat
  block**, and renders the dramatic metric (e.g. "intro drop 38%") **after** that context — the honesty
  is in the hierarchy, never skippable fine-print under a scary number. Reuses the existing `.card`/status
  tokens; no new modal/drawer (T4 cards stay in-card; §Slice-6 §8).
- The **immature/under-powered/unavailable** cohort states render as muted, explicitly-labelled rows —
  never as a usable signal (the n=1/maturity discipline is visible in the UI).
- The **CEO interview queue** (§9, review F14) — a read-only filtered view of `acknowledged`
  rubric_contradiction items, so the highest-value output persists as the human-interview agenda instead
  of vanishing on acknowledge.

No new rail entry; no change to the read-only Quality screen.

---

## 13. Dependency reality (a stated CEO expectation)

**Echo's meaningful output is weeks out even after it is built.** It needs: (a) **#6 Herald live** (real
uploads to measure + the OAuth token store), and (b) **accumulated cohort history** — at least
`min_cohort_n` mature uploads per `(channel × format)` cohort, and analytics maturity lag on top. With
the `channel × format` axis (Fork A) cohorts fill **deliberately slowly** in exchange for trustworthy
signals. **Therefore:** #7 is built and fully tested **now** behind the fake analytics seam (the
methodology, the diagnosis map, the T4 wiring, the contradiction escalation, the report contract); but
the first *real* proposal arrives only after Herald has been publishing for weeks. This is a feature of
honesty, not a defect — Echo refuses to speak before it has a cohort. The dashboard surfaces the
empty/under-powered state plainly so "no signal yet" never reads as "everything's fine."

---

## 14. Edge cases (continuing the master + Slice-6 E-numbering)

| # | Scenario | Required behavior |
|---|---|---|
| E25 | Echo sees one viral fluke / one flop | n=1 guard: a cohort below `min_cohort_n` proposes **nothing**; surfaced as `under-powered` on the report only (reaffirms E10). |
| E26 | A signal is real but confounded (topic/seasonality/surfacing) | Reported **with** the confounder block; phrased as a *candidate cause / coaching experiment*, never causation. No silent over-claim. |
| E27 | A refresh token / access token would appear in a report, card, event, or log | Redacted by the `J()` pass; Echo never includes a token in any output by construction. Asserted in a negative-safety test. |
| E28 | A channel's token is expired/revoked/needs-reconnect | That channel's cohorts are **skipped** with a `reconnect in Herald` note on the report; never a 500; other channels still analyzed (mirrors Slice-6 E21). |
| E29 | Analytics data is immature (video published days ago; retention/CTR still moving) | Cohort flagged `immature`, **excluded from proposing**, shown on the report so the CEO sees why it's held back. |
| E30 | Reality contradicts a rubric band (sustained, cohort-scale, mature) | `rubric_contradiction` CEO-interview card; `acceptable:false`; no Accept; Acknowledge tracks, never writes (reaffirms E11/E18). |
| E31 | Someone tries to wire Echo to auto-tune the rubric | Impossible by three walls: no rubric writer; `apply_soft_change` refuses; `acceptable:false` → 409 before any writer. `can_write_rubric()` stays true (asserted; reaffirms E24). |
| E32 | Echo's engine imports `eval.tracking` / a noise-floor gate in a refactor | **Import-boundary test fails CI** — the methodology separation is a structural gate (§5.2). |
| E33 | Herald not built yet (no token store) | `analytics_fn`/`token_provider` is the fake seam; Echo runs on canned cohorts; the real lane is empty-but-rendered until #6 ships (mirrors Slice-6's `echo_fn=None`). |
| E34 | `control_room_echo_report.json` missing/corrupt | Degrades to "no report yet"; parsed in place, never rewritten behind the user's back (mirrors E13/E20). |
| E35 | A cohort signal maps to a non-soft-tier owner (pure thumbnail/packaging) | Surfaced as a **report hypothesis only**, never a `soft_addendum` (no soft-tier file to write) — the T4 write path stays honest (§7). |
| E36 | The `videoId → slug` join fails — video published outside Atlas, slug renamed/deleted, or no publish record | The orphan video is **excluded from cohorts** with an `unjoined` note on the report; it never silently corrupts an aggregate. A partial cohort (some videos joinable) reports its real `n` after exclusion (review F7). |
| E37 | A refresh would exceed the analytics query budget / the shared daily quota is low | Echo **back-pressures**: mature cohorts are served from cache, the run reports `quota-deferred` for what it couldn't fetch, and it never starves Herald's publish budget or 500s (review F8, §6.6; mirrors master E9). |
| E38 | `analytics_fn` returns partial/malformed data — missing retention curve for some videos, bad shape | Those videos drop from the cohort with a `partial-data` note; a cohort that loses too many to clear `min_cohort_n` becomes `under-powered`, not a false signal. Never a 500 (review F7). |
| E39 | A cohort is "in band vs its own baseline" while the baseline itself is falling | The baseline's own trend is reported; a declining baseline is **flagged**, so self-relative comfort can't mask absolute decline (review F4, §6.3). |
| E40 | The `ATTRIBUTION` table names a `band_id` the rubric doesn't have | **Build-time assertion fails CI** — every attribution `band_id` must exist in `rubric.bands()`; the spec never ships a phantom band like `hook_strength` if the rubric lacks it (review F5, §6.5). |

---

## 15. Testing (injectable seams; no real LLM/engine/network)

**Structural / negative-safety (the point of the sub-project):**
- **Methodology separation (E32):** assert `eval.tracking` is not in Echo's transitive module graph; an
  AST/call scan finds no `noise_floor(`/`verify_fn=`/`sigma=` **call** (not a raw substring grep — F10b).
- **Attribution integrity (E40):** every `band_id` in the `ATTRIBUTION` table exists in `rubric.bands()`.
- **No auto-tune (E31):** `can_write_rubric()` stays true; a `rubric_contradiction` has no accept path
  (409); `apply_soft_change` refuses a rubric `soft_path` even if an Echo record is tampered (reuse
  Slice-6 E16).
- **Echo proposes, never writes:** `refresh_echo` with an injected `echo_fn` performs **no** persona
  write (assert no file under any soft dir appears until a CEO accept).
- **Secrets (E27):** a token threaded through a fake `token_provider` never appears in the report/cards/
  events/logs.

**Unit (mirror `test_chat_api`/`test_publish_api`/`test_settings_api`):**
- `analyst.analyze_cohorts` on canned cohort rows: correct median/IQR aggregation; n=1 → no proposal
  (E25); immature → excluded (E29); contradiction detection on a crafted band-vs-reality fixture (E30);
  confounder block always present (E26).
- `refresh_echo` real default path with an injected `analytics_fn` (canned) + fake `token_provider`:
  emits normalized `source:"echo"` envelopes, drops n=1, writes the report, never hits network.
- Channel-skip on `expired` token (E28); empty/degraded report read (E34).

**e2e (Playwright; `domcontentloaded`; restart server after backend change; inject `echo_fn`/
`analytics_fn`):**
- The Echo lane renders soft proposals + a rubric-contradiction flag from an injected `echo_fn`.
- "Refresh Echo" runs and re-lists proposals + the report; the report panel shows the confounder block
  and an under-powered/immature cohort as muted/labelled.
- A `rubric_contradiction` card shows **no Accept**; Acknowledge resolves it; the event appears in the
  Activity feed tagged `ceo`.
- Empty lane (no `echo_fn`) renders without crashing.

---

## 16. Build order

**Buildable NOW (behind the fake analytics seam — no Herald, no network, no keys):**
1. `analyst/analyst.py` engine — cohort aggregation (median/IQR), the n=1/maturity guards, the
   diagnosis map, the contradiction detector, the report builder (+ unit tests, incl. the methodology-
   separation import test).
2. `contracts/performance_report.schema.json` + `adapters/analyst.py` + the ONE `registry.AgentEntry`
   (+ adapter/contract-boundary tests).
3. `dashboard/echo.py` (`default_echo_fn` + report persistence) + the real `proposals.refresh_echo`
   default wired to the **fake `analytics_fn`/`token_provider`** (+ API tests **incl. negative-safety**).
4. `app.py` — `GET /api/echo/report`, `POST /api/echo/refresh`, the new `app.state` seams (proposals
   accept/reject/acknowledge already exist from Slice-6).
5. Frontend — the "Refresh Echo" button + the read-only report panel + the muted immature/under-powered
   states (the Echo cards themselves are Slice-6).
6. e2e — the four flows in §15.

**SHELLED until #6 Herald + accumulated cohort history (weeks out):**
- The **real `TokenProvider`/`analytics_fn`** bound to Herald's per-channel OAuth store (real
  `yt-analytics.readonly` pulls). Until then the fake seam carries everything.
- **Real cohort data** → the first real proposals/contradictions (needs weeks of mature uploads; §13).
- The **cron driver** (the same propose-only `refresh_echo` seam on a schedule, Fork B).
- The **actual discipline constants** (`min_cohort_n`, `window_days`, `maturity_days`) — placeholders
  now; CEO-owned numbers (O1).

---

## 17. What this sub-project deliberately does NOT do (YAGNI)

- **No rubric write path of any kind** — a contradiction escalates read-only; the CEO edits
  `rubric.json` by hand if they choose (outside the dashboard).
- **No reuse of the reproducible-eval guardrails** — Echo never imports `tracking.noise_floor` or any
  held-out/K≥5 gate; that machinery is for the internal proxy, not for confounded one-shot reality.
- **No causal claims** — observational/cohort only, confounders always shown.
- **No new pipeline stage / no `STAGES` change / no gate / no contract-shape change** beyond the new
  read-only report contract — Echo is off-pipeline (one entry + one adapter), like Vera.
- **No finer cohort segmentation** (hook shape, topic cluster) in v1 — `channel × format` only (O5).
- **No auto-fire / no auto-apply** — both the on-demand and the future cron path are propose-only; the
  CEO accept is the only write (Slice-6 Fork A holds).
- **No chat path to Echo's writes** — chat is T1-only (master §8/E7); it may navigate to the Coaches/
  Echo view but can never accept a proposal or trigger a write.
- **No Echo write of any persona/coaching prose** — Echo brings evidence; the owning coach authors the
  note; the CEO writes the file.

---

## 18. OPEN QUESTIONS FOR CEO REVIEW — **RESOLVED 2026-06-24**

All seven were resolved by the CEO in this session (recommendations accepted). Recorded here with
their resolutions; the body reflects them.

1. **The discipline constants (O1 — the most important numbers).** The **minimum cohort size
   `min_cohort_n`**, the **rolling `window_days`**, and the **analytics-maturity lag `maturity_days`**
   that make a `(channel × format)` signal *proposable* rather than noise — Echo's noise-floor-
   equivalent, CEO-owned.
   **→ RESOLVED:** start at **`min_cohort_n = 5`, `window_days = 30`, `maturity_days = 14`**, **tuned
   against real data** once Herald has published for weeks. Short vs long *may* later get different
   floors — revisit when real cohorts exist (re-open if the data demands it).

2. **Contradiction sensitivity (O2).** How *sustained* a reality-vs-rubric disagreement must be before
   Echo raises a `rubric_contradiction` CEO-interview item.
   **→ RESOLVED:** require **≥2 consecutive mature cohorts agreeing** on the disagreement before
   raising the interview item (avoids interview spam without letting a wrong band linger). Encoded in
   §9.

3. **CTR baseline (O3).** What Echo compares a cohort's CTR against.
   **→ RESOLVED:** **cohort's own rolling baseline** — Echo has no trustworthy absolute number, so it
   never invents one. (If the CEO later sets an explicit per-format CTR target they own, Echo can adopt
   it then; not now.)

4. **Report retention (O4).** History vs latest-only.
   **→ RESOLVED:** **keep the append-only `echo_reports.jsonl` history** — it's small and is the proof
   that coaching actually moved reality over time. (§10.)

5. **Finer cohorts later (O5).** Whether/how to segment below `channel × format`.
   **→ RESOLVED:** **stay coarse (`channel × format`) for v1**, but **design the cohort key to extend
   toward `hook-shape`** later (closest to what coaching can change). The cohort key is a structured
   tuple so a third axis is additive, not a rewrite. (§6.2.)

6. **Rubric-contradiction notification (O6 — carries Slice-6 O5).**
   **→ RESOLVED:** **Activity feed only for now**; external notify (email/Slack) is a later add if items
   start getting missed. (§9.)

7. **The Herald token-store contract (O7).** The `TokenProvider` interface Echo consumes
   (`list_channels` / `analytics_token`, **read-scope only** — §6.4).
   **→ RESOLVED:** **confirmed** — Echo must be **structurally incapable** of touching the channel:
   Herald (#6) exposes only this read-only handle and **never** hands Echo the `youtube.upload` /
   `force-ssl` tokens. This is the one promise to lock when the Herald spec is written. (§6.4.)

### New decisions raised by the plan review (2026-06-24) — **STILL OPEN**

The CEO/eng/design stress-test surfaced three items that need a CEO decision (the rest were folded into
the body as edits — see §19):

8. **Sequencing — write Herald's token-store section first? (review F1).** Echo's §6.4 `TokenProvider`
   is invented by the consumer; if Herald's real OAuth design differs, Echo reworks. Recommend: spec at
   least Herald's token-store/`TokenProvider` section **before** building Echo, so O7 is a real handshake.
   *(Decision: build Echo now on the assumed contract, or gate #7 on a Herald token-store spec first?)*

9. **Window semantics — calendar days vs last-N-uploads (review F2, the load-bearing one).** With
   `window_days = 30` calendar days, a **low-volume channel may never reach `min_cohort_n` mature uploads
   in window — so Echo proposes nothing, ever, and the loop never actually closes.** A **rolling
   "last N uploads regardless of date"** window closes the loop on any cadence (at the cost of comparing
   across a longer time span). *(Decision: keep a calendar window, switch to a rolling-N-uploads window,
   or make it configurable per channel?)* Recommend a rolling-N window so the loop can actually close.

10. **Report-only v1? (review F3 — the cleanest de-risking cut).** The proposal machinery already shipped
    in Slice-6. Echo v1 could ship **read-only (the report panel only)**, let the CEO eyeball real cohorts
    for a few weeks, and wire the diagnosis-map → T4 proposals **only after** the signals prove
    trustworthy — avoiding auto-generated confounded proposals before there's real data to trust.
    *(Decision: full proposal path in v1, or report-only v1 then add proposals once signal is proven?)*

11. **Should a `hook` rubric band exist at all? (review F5).** The diagnosis map wants to route
    intro-retention drops to a hook property, but the rubric may have no `hook_strength` band (it measures
    `info_density`/`words_per_scene`/etc.). A **missing** band is itself a §9 decomposition gap — a
    CEO-owned rubric question, not something Echo invents. *(Decision: does the CEO want to add a hook
    band to `rubric.json`, or should intro-retention signals stay report-only until they do?)*

---

## 19. Plan-review hardening (2026-06-24)

This spec was stress-tested with the CEO / eng / design review lenses before any build. The **safety
architecture passed** (the §5.2 import boundary, the three walls against rubric auto-tuning E31, the
single Slice-6 guarded write path — nobody can turn Echo into an auto-tuner). The review's value was on
**whether Echo produces real value**, not whether it's safe. Folded into the body as edits:

| Finding | Change | Where |
|---|---|---|
| **F4** baseline can mask absolute decline | report the baseline's own trend; flag a falling baseline | §6.3, E39 |
| **F5** band→retention mapping didn't exist | explicit `ATTRIBUTION` table (hypothesis lookup, stated confidence) + build-time band-existence assertion | §6.5, §7, E40 |
| **F6** soft_addendum vs contradiction muddied | stated the mutually-exclusive dichotomy (reality agrees → addendum; diverges → contradiction) | §7 |
| **F7** unnamed data-join shadow paths | orphan video / partial / malformed-data edge cases | E36, E38 |
| **F8** analytics API quota unaddressed | shared-bucket back-pressure + mature-cohort cache + `quota-deferred` | §6.6, E37 |
| **F9** refresh would block the event loop | `asyncio.to_thread` (mirrors `intake.py`) | §6.6 |
| **F10b** import test fragile (substring grep) | transitive-graph + AST call scan; import `diagnose` read-only | §5.2, §5.4, §15 |
| **F11** "no proposals" had 3 conflated meanings | three distinct, labelled empty states | §12 |
| **F12** scary number led, caveat hid | card hierarchy leads with n/maturity/confidence + confounders | §12 |
| **F13** slow quota-spending button looked instant | loading/disabled state, "last refreshed", idempotent | §12 |
| **F14** acknowledged interview items vanished | persistent read-only CEO interview queue | §9, §12 |

**Left as CEO decisions (not edited), §18 items 8–11:** F1 (Herald spec first) · F2 (window semantics —
the loop-may-never-close risk) · F3 (report-only v1) · F5's rubric-band question.
