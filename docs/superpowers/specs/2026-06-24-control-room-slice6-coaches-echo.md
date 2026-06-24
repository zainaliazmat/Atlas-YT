# Control Room — Slice 6: Coaches view + the T4 proposal surface (+ Echo shell)

**Date:** 2026-06-24
**Status:** Spec for review (design locked via the 4 forks below; not yet built)
**Author:** CEO + Atlas (brainstorming session)
**Branch:** `control-room` (continues Slices 0–5 + 1.5 — 353 unit + 39 e2e green)
**Depends on:** the eval/rubric/coaches foundation (PROJECT_CONTEXT §13), the dashboard
write-tier model (master spec §4), the injectable-seam pattern (Slices 2–5).
**Master spec:** `2026-06-23-control-room-design.md` (§4 tiers · §5 loop + Echo methodology ·
§12 #7 · §13 E10/E11). **Self-improvement charter:** `self-improvement-enhancement-decisions.md`.

---

## 0. One-paragraph summary

This slice builds the **T4 surface** — the last Control-Room write tier and the only one that
touches the self-improvement department. It adds a **Coaches view** (Quill 🖋️ editorial + Flux 🎚️
production: their identity, the stages and rubric bands they own, the addenda they've authored,
and the eval-loop ledger) and a **propose → review → accept/reject inbox** for soft-tier coach
addenda. It resolves the central tension — *today the loop auto-applies a soft change the moment
eval improves; §4 T4 wants CEO approval **before** the write* — by moving the loop to **propose-only**
and making the **CEO accept the one and only thing that calls `apply_soft_change`**. It plans the
**Echo 📈 proposal data contract + card UI** behind an **injectable seam** (`app.state.echo_fn`) so
the whole surface is testable now, with the real Echo engine landing in #7. Throughout, the
**rubric stays unwritable** (`can_write_rubric()` stays true) and a T4 accept can **never** touch
rubric/contracts/spine (`WriteBoundaryError` by construction).

---

## 1. The four forks (settled 2026-06-24)

| # | Fork | Decision |
|---|---|---|
| **A** | Auto-apply vs CEO-approval | **Propose-only everywhere.** The loop never writes on its own; every would-accept change becomes a persisted **proposal**; only a CEO accept calls `apply_soft_change`. The autonomous path is also propose-only — one production write path, improver strictly least-privileged. (§4 below.) |
| **B** | Proposal model | **One envelope + discriminant.** A single proposal record with `source` (coach/echo) and `kind` (`soft_addendum` / `rubric_contradiction`), a shared accept/reject lifecycle, one store, one card renderer. `rubric_contradiction` is structurally accept-disabled. (§3.) |
| **C** | UI placement | **A new "Coaches" rail entry** (`v-coaches`). The read-only Quality screen (rubric/scorecard) is left unchanged; the self-improvement *department* — identity + the T4 inbox — gets its own home. (§6.) |
| **D** | Rubric contradictions | **Same inbox, accept-disabled CEO-interview card.** Reuses `diagnose.py`'s `decomposition_gap → escalate_to_ceo`. The *missing accept button* is the no-write guarantee. (§5.) |

---

## 2. Architecture rules this slice respects (re-stated, non-negotiable)

- **Tiered write authority (§4).** This slice IS the T4 surface: **existing `WriteBoundaryError`
  write boundary + CEO approval**. T4 accept writes soft-tier persona/prompt/playbook markdown
  ONLY; it can never write the rubric, contracts, or spine. The improver stays least-privileged.
- **The rubric is unwritable.** `rubric/__init__.py` exposes no writer; `loop.apply_soft_change()`
  physically refuses every rubric/contracts/spine path. `loop.can_write_rubric()` **must stay true**
  and is asserted in this slice's tests. We add **no** rubric write path — a rubric contradiction is
  a **read-only escalation to the CEO**, never a button.
- **Additive only.** Extend `atlas/dashboard/`. Touch `eval/loop/coaches/rubric` **only via existing
  seams**: `apply_soft_change` (the one guarded writer), `propose_fix`/`run_loop` with
  `write_soft=False`, the coach adapters' `propose_addendum` (soft-tier text only), and the
  read-only rubric accessors. **No edits** to `loop.py`, `rollup.py`, `diagnose.py`,
  `rubric/__init__.py`, the coach adapters, or `registry.py`.
- **Injectable seams for every engine/LLM touch.** Mirror `produce_fn` / `find_topics_fn` /
  `chat_fn`: add `app.state.coach_propose_fn` and `app.state.echo_fn`, both `None`-defaulting and
  test-injected. **`ANTHROPIC_API_KEY` is never set in tests** — no real LLM/engine runs.
- **ONE status language.** Reuse the belt vocabulary where it applies; proposals add their own small
  lifecycle vocabulary (`pending | accepted | rejected | acknowledged`), kept distinct and explicit.
- **The §4 audit records the initiator plane.** A T4 accept is tagged `initiator="ceo"`, tier `T4`,
  on the event ring — so the audit shows no T4 write ever originated from the LLM/chat plane.
- **e2e gotchas (carried).** `wait_until="domcontentloaded"` (never `load`/`networkidle`); restart
  the server after any backend change (no `--reload`); inject fakes for every engine seam.

---

## 3. The unified proposal envelope (Fork B)

One record type. Persisted in a dashboard-owned store (§7). The shape **encodes the invariants** —
a denied write is structurally impossible to express as an acceptable proposal.

```jsonc
{
  "id": "prop-0007",                 // stable, monotonic id minted by the store (NOT Date.now)
  "source": "coach" | "echo",        // who proposed it
  "kind": "soft_addendum" | "rubric_contradiction",
  "tier": "T4",
  "status": "pending" | "accepted" | "rejected" | "acknowledged",
  "created": 1750000000,             // stamped store-side at insert (dashboard plane — allowed)
  "resolved": null,                  // ts of accept/reject/acknowledge

  // -------- targeting --------
  "band_id": "script:info_density",  // the CEO-owned rubric band this concerns (read-only origin)
  "stage": "script",
  "owner": "Marlow",                 // the specialist whose persona an addendum would tune
  "coach": "editorial_coach" | "production_coach" | null,
  "direction": "LOWER it to about 2.9 — comfortably inside [2.0, 3.8] (currently 9.85)",

  // -------- evidence (provenance the CEO judges) --------
  "evidence": {
    // coach: the loop's own gates that already passed (the reason it's a would-accept)
    "verdict": { "target_before": 9.85, "target_after": 2.79, "beats_noise_floor": true,
                 "regressions": [], "noise_note": "objective value=2.79 inside band by margin 0.1 → yes" },
    "held_out": { "generalizes": true },
    // echo: cohort/aggregate ONLY — never a single outcome (E10)
    "cohort": null
  },

  // -------- the proposed change (soft_addendum ONLY) --------
  "addendum": "<!-- gstack-band: script:info_density -->\n## Coach note …\n…",  // soft-tier markdown;
                                     // leads with the dashboard-owned band marker (F3); null for contradictions
  "soft_path": ".../scriptwriter/COACH_ADDENDUM.md",  // a SOFT-TIER persona file; null for contradictions
  "supersedes": null,                // id of a same-band APPLIED note this would replace on accept (F5); else null

  // -------- acceptability (the structural guarantee) --------
  "acceptable": true | false,        // false ⇔ kind == "rubric_contradiction"
  "accept_reason": "Soft-tier persona addendum; CEO accept performs the only write."
}
```

**Invariants baked into the shape (verified at accept-time, not trusted from the record):**

1. `kind == "rubric_contradiction"` ⇒ `acceptable == false`, `addendum == null`, `soft_path == null`.
   There is **no change to write** — only a CEO-interview escalation to track (§5).
2. `kind == "soft_addendum"` ⇒ `soft_path` is a soft-tier persona file. On accept the endpoint calls
   `loop.apply_soft_change(soft_path, addendum)`, which **re-validates** the soft-tier boundary and
   raises `WriteBoundaryError` for any rubric/contracts/spine path — so a tampered `soft_path` cannot
   write a denied file (§4, E16). The record is **never** trusted; the guarded writer decides.
3. The store **normalizes** every inbound coach/echo dict into this envelope (mirrors
   `intake.normalize_candidates`); a malformed or single-outcome (n=1) Echo item is **dropped at
   normalization**, never surfaced (E10/E17).

> **Why one envelope, not three.** Coach addenda, Echo soft proposals, and rubric contradictions
> share the exact same CEO lifecycle (a card the CEO accepts, rejects, or acknowledges). The only
> axis of difference — *can this be written?* — is a single boolean the shape carries and the accept
> endpoint enforces. One store, one renderer, one set of negative-safety tests.

---

## 4. Resolving the auto-apply → CEO-approval tension (Fork A — the heart)

### 4.1 What happens today (verified in `eval/loop.py`)
`run_loop()` proposes a soft addendum, **writes it immediately** through `apply_soft_change()`
(`write_soft=True` default), re-measures, and — if it passes the noise-floor gate, the held-out
verifier, and the optional `spot_check_fn` — **keeps it** (else reverts by `unlink`). The human
sign-off is an *in-loop callback* (`spot_check_fn`). The dashboard must not trigger that auto-write.

### 4.2 The resolution: move the human sign-off OUT of the loop and INTO the T4 accept
The loop already exposes the two seams we need — **no `loop.py` edit required**:

- **`write_soft=False`** → the loop measures and decides but **writes nothing**.
- **the automated gates stay** → noise-floor (`decide`), held-out verifier (`verify_fn`).
- **`spot_check_fn` is left `None`** → its job (the final CEO sign-off before the change persists)
  **relocates to the dashboard accept**.

So the production flow becomes:

```
score a render ─► diagnose.pick_primary_target ─► run_loop(write_soft=False, verify_fn=…, spot_check_fn=None)
   │                                                       │
   │                          (noise-floor + held-out gates pass ⇒ "would-accept")
   ▼                                                       ▼
 nothing written                              emit a PENDING proposal → proposals store
                                                           │
                                              CEO opens the Coaches inbox, reviews evidence
                                                           │
                              ┌────────────── ACCEPT ──────┴────── REJECT ──────────────┐
                              ▼                                                          ▼
   loop.apply_soft_change(soft_path, addendum)   ← the ONE write          discard; status=rejected
   status=accepted; event ring initiator="ceo" tier T4                    (nothing was ever written)
```

The CEO accept **is** the spot-check that used to live in `spot_check_fn` — and it is now also the
moment of the write. The improver's automated gates still vet quality *before* a proposal is even
shown; the human still owns the persist decision; the write path is singular.

### 4.3 "Everywhere" — the autonomous path is also propose-only
Per Fork A, there is **one production write path**: the CEO accept. The dashboard always runs the
loop propose-only. Any future cron/CLI driver of the loop uses the **same propose seam**
(`proposals.propose_from_loop`, §7) and lands proposals in the **same store** — it does not
auto-apply. `run_loop(write_soft=True)` survives **only as a low-level primitive exercised by the
loop's own boundary unit-tests** (which deliberately prove the write boundary holds); it is **not
wired to any autonomous scheduler**. (Open question O6 confirms we keep the primitive for those
tests rather than deleting it.)

### 4.4 The accept write boundary (prove a denied write is impossible from the UI)
`POST /api/proposals/{id}/accept` is the T4 writer. It:
1. loads the proposal; if `status != "pending"` → **409 "already resolved"** (idempotent, E19);
2. if `acceptable == false` (rubric_contradiction) → **409**, mirroring how `_approve_gate` refuses
   a hard block — *never offers a write the boundary would reject* (E18);
3. else **smart-merges** (O2, F3): reads the existing `soft_path` file (if any), drops the section
   whose **band marker** (`<!-- gstack-band: <band_id> -->`) matches this proposal, keeps every
   other-band section, appends the new note, and calls
   **`loop.apply_soft_change(proposal.soft_path, merged_text)`** — the existing guarded writer (one
   write of the full merged content). `WriteBoundaryError` (denied root, or non-soft-tier path) →
   **409, nothing written** (E16);
4. on success: marks `accepted`, stamps `resolved`, records an event `{tier:"T4",
   initiator:"ceo", kind:"proposal_accept"}` on the dispatcher ring (the §4 audit).

**Stale × smart-accumulate (F5, E23×E22).** Accepting an *older* pending proposal for a band replaces
a *newer* already-applied note for the same band — a quiet regression. So accept computes `supersedes`
(the same-band applied note, if any) and the UI shows it: *"this replaces a newer note for `<band>`,
accepted `<date>` — proceed?"*. The write still goes through (soft-tier is reversible, §5/F8), but
the CEO is never surprised by it.

There is **no other endpoint** that writes a persona/rubric file. The chat plane (T1-only) has no
proposal-accept tool — the §8/E7 property is preserved unchanged. **Structural proof:** the only
call to `apply_soft_change` in the dashboard is step 3, reachable only from this CEO endpoint, and
`apply_soft_change` itself denies every non-soft-tier path regardless of what the record claims.

---

## 5. Rubric contradictions = CEO-interview escalations (Fork D, E11)

When Echo's real-world signal contradicts a rubric band (e.g. retention is fine where
`hook_strength` says it should fail), **the rubric is wrong** — a *ground-truth decomposition gap*,
which is exactly the one thing the improver structurally cannot fix (the CEO owns the rubric, no
write path). This reuses the existing concept in `diagnose.py` / `rollup.py`
(`decomposition_gap → escalate_to_ceo`).

- A `rubric_contradiction` proposal is `acceptable: false`, `addendum: null`, `soft_path: null`.
- Its card renders as a **CEO-INTERVIEW flag**: the contradicting band, the cohort evidence, and the
  read-only actions **Acknowledge** (track it; `POST …/acknowledge`, status → `acknowledged`, **no
  write**) and **Reject/Dismiss**. **There is no Accept button** — and the absence is the guarantee.
- It is the *most valuable* Echo output and is permanently a CEO-interview item, not a loop item.
  Nobody wires it into auto-tuning the rubric. (Notifying the CEO outside the dashboard is O5.)

---

## 6. The Coaches view (Fork C — new `v-coaches` rail surface)

A new left-rail entry between **Quality** and **Projects**. Three regions:

### 6.1 The two coaches (identity + ownership)
For Quill 🖋️ and Flux 🎚️, read-only from sources that already exist:
- **Identity** — `registry.get_entry` via `data._entry_brief` (name/display/emoji/role/blurb) +
  `data._read_soul` (SOUL voice line) + `data.provider_for` (effective brain). No new registry
  entries — Quill/Flux already exist.
- **Owned stages** — from `loop.EDITORIAL_STAGES` / `loop.PRODUCTION_STAGES` (read-only import).
- **Owned rubric bands** — a new `data.coach_owned_bands(coach_name)` that selects bands whose stage
  is in that coach's stage set (generalizes the existing `data._owned_bands`, which is keyed by
  *stage role* and returns `[]` for a coach because a coach is not a pipeline stage). Read-only,
  tolerant, degrades to `[]` when the rubric is absent.
- **Authored addenda** — *applied*: the soft-tier `COACH_ADDENDUM.md` files that **exist on disk** in
  the owning persona dirs (existence-checked via `loop._soft_path_for`'s targets per owned stage,
  read-only — we do not import the private helper; we mirror its owner-dir map in `data`); *pending*:
  the count of `pending` `soft_addendum` proposals whose `coach == this coach`.

### 6.2 The T4 proposals inbox (the accept/reject surface)
The unified inbox lives on this view (§3/§4). Cards, newest-first:
- **`soft_addendum` card** — source badge (🖋️/🎚️ coach or 📈 Echo), the band + direction, the
  **evidence** (the loop's noise-floor/held-out verdict for a coach; the cohort aggregate for Echo),
  a preview of the addendum markdown, and **Accept** / **Reject**. Accept shows a `T4 · persona write`
  tag and an acknowledgement checkbox (mirrors the `gr-ack` pattern in `openGateReview`) — the
  honest "this writes a persona file" confirm. Accept posts to `…/accept`; the card resolves in place
  (mirrors `renderProposal`'s in-card resolution).
- **`rubric_contradiction` card** — the CEO-INTERVIEW flag (§5): no Accept; **Acknowledge** / **Dismiss**
  + a lock line: *"🔒 The rubric is CEO-owned — this is an interview item, not a tunable. There is no
  write path."*

### 6.3 The eval-loop ledger
Reuse `data._loop_ledger()` (already surfaced on Quality) here too — the change ledger is the
department's activity log. No new backend.

> **The Quality screen is untouched.** It keeps the frozen read-only rubric/scorecard/trend. The
> write-bearing T4 inbox lives only on Coaches, so the frozen standard never shares a screen with an
> accept button.

> **Day-one honesty (F9).** The inbox is **empty by default**: coach proposals exist only after the
> loop runs (the post-render auto-propose wiring is shelled, §12) or after the CEO presses "ask the
> coach" (F1). So on day one the Coaches view's value is **identity + owned bands + the ledger**, not a
> busy inbox. This is by design, not a gap — we are not overselling a populated queue that the loop
> has to earn.

---

## 7. Backend — new modules + endpoints (all additive)

### 7.1 `atlas/dashboard/proposals_store.py` (mirror `settings_store.py`)
A single dashboard-owned JSON (`control_room_proposals.json`, **gitignored**), injectable via
`app.state.proposals_path`. Tolerant by construction (E20): missing/corrupt → empty list, parsed in
place, never rewritten behind the user's back; `validate`/`normalize` never raise.

**Concurrency (F2 — the store is read-modify-write and §10 supports multi-tab).** Accept, reject,
acknowledge, and `refresh_echo`-upsert all mutate this one file, so a naïve RMW would `last-writer-wins`
and silently drop a status change — the exact hazard the master spec flagged for `memory.json` (§6.3).
Every mutation therefore goes through a **single module-level write-lock + atomic replace** (serialize
under a `threading.Lock`, write to a temp file, `os.replace`). Reads are lock-free (parse-in-place).
The id minter reads-under-lock so two concurrent upserts can't mint the same `prop-NNNN` (E26).

- `load(path) -> list[proposal]`
- `normalize_coach_proposal(raw) -> proposal | None` and `normalize_echo_proposal(raw) -> proposal | None`
  — wrap raw coach/echo dicts into the §3 envelope; **drop** malformed or n=1 Echo items (E10/E17).
- `upsert(path, proposal) -> proposal` — mint a stable monotonic `id` (`prop-NNNN`, from the current
  max in the store — **not** `Date.now`/random, so tests are deterministic), stamp `created`.
- `get(path, id)`, `set_status(path, id, status, *, resolved_ts) -> proposal | None`.
- `dedupe key` = `(source, band_id, addendum-hash)` so re-proposing the same change doesn't pile up
  duplicate pending cards (a fresh proposal supersedes an unresolved identical one).

### 7.2 `atlas/dashboard/proposals.py` (the propose seam + Echo refresh)
- `propose_from_loop(target, *, propose_fn) -> proposal | None` — runs the loop **propose-only**
  (`run_loop(write_soft=False, verify_fn=…, spot_check_fn=None, use_coaches=True)`), and **only if it
  would-accept** (all automated gates passed) emits a normalized `soft_addendum` proposal. The actual
  loop/LLM call is the injected `propose_fn` (default = the real loop path bound to the latest
  scorecard via `diagnose.pick_primary_target`); tests inject a fake that returns a canned would-accept
  result — **no real engine runs**. Writes nothing to persona dirs (asserted). **This is NOT a cheap
  read (F1):** the real `propose_fn` runs the coach LLM to author, **and** `make_script_remeasure`
  re-runs Marlow's engine to re-measure, **plus** the held-out verifier — potentially minutes and real
  spend per call. It must therefore run in the **background** (§7.6), never inside the request, and is
  gated by a cost-aware confirm.
- The proposal's `addendum` carries a **machine-readable band marker** that the dashboard owns — an
  HTML comment `<!-- gstack-band: <band_id> -->` prepended to the note (F3). `merge_addendum` keys on
  THIS marker, never on the human prose header (which is authored by a different module and would
  mis-parse if it drifts — a silent shadow path). `propose_from_loop` stamps the marker when it
  normalizes; the coach's own `## Coach note …` heading stays as readable prose below it.
- `merge_addendum(existing_text, band_id, new_section) -> merged_text` (O2 smart-accumulate) — splits
  `existing_text` on the `<!-- gstack-band: … -->` markers, drops the section whose marker matches
  `band_id`, appends `new_section`, and re-joins. Pure string function (no I/O), unit-tested in
  isolation; the accept endpoint (§4.4) feeds its result to `apply_soft_change`. (Removing a band's
  marker section with an empty `new_section` is how a **revert** works, F8.)
- `refresh_echo(echo_fn, projects_dir, *, cohort_min) -> list[proposal]` — calls the injected
  `echo_fn` (read-only, cohort hypotheses), normalizes, and **drops any cohort smaller than
  `cohort_min`** (the n=1 guard, E10/E17). `cohort_min` comes from the CEO-editable setting
  `defaults.echo_cohort_min` (**default 5**, §7.4). `echo_fn=None` or raising → `[]` (E21), never a
  500 (mirrors `intake`/`publish`).

### 7.3 `atlas/dashboard/data.py` additions (read-only)
- `coach_owned_bands(coach_name) -> list[str]` (§6.1).
- `coaches(projects_dir) -> {coaches:[…], ledger:{…}}` — assembles §6.1/§6.3 for Quill + Flux,
  tolerant, engine-free.

### 7.4 `atlas/dashboard/settings_store.py` addition (the n=1 threshold — CEO-editable)
Add one field to the existing `defaults` block: **`echo_cohort_min` (default `5`)** — the minimum
number of videos a pattern must span before Echo may propose on it (the n=1 guard's actual number,
locked from O3). It is validated as a positive integer, tolerant-defaulted to 5 on missing/garbage
(E13), surfaced in `public_settings`, and rendered as a **number-counter box on the Coaches view's
Echo lane** so the CEO can change it without editing JSON. `refresh_echo` reads it per call. This is
buildable now even though Echo's real data is #7 — the setting + box ship in Slice 6.

### 7.5 `atlas/dashboard/app.py` additions
New `app.state` seams (mirror the established `None`-defaulting pattern):
- `app.state.coach_propose_fn = None`  *(tests inject a fake would-accept proposer; never the LLM)*
- `app.state.echo_fn = None`           *(tests inject canned cohort proposals; real Echo = #7)*
- `app.state.proposals_path = proposals_store.DEFAULT_PATH`

Endpoints:
| Method + path | Tier | Behavior |
|---|---|---|
| `GET /api/coaches` | read | `data.coaches(...)` — identity + owned stages/bands + applied/pending addenda + ledger. |
| `GET /api/proposals` | read | Returns the store + a lazy `refresh_echo` merge; `?status=` filter. Each item is the §3 envelope. Never 500s on a bad `echo_fn` (E21). |
| `POST /api/coaches/{name}/propose` | propose (**async**) | On-demand "ask the coach". The real loop is **expensive** (F1, §7.6), so this **returns immediately** with a `running` job handle and runs `propose_from_loop` in a **background thread**; the resulting pending proposal arrives via an SSE event + the inbox refresh. Cost-aware confirm in the UI (§8). **Writes no persona file.** Validates `name ∈ {editorial_coach, production_coach}` via `safe_segment`. Emits `proposal_started` / `proposal_ready` / `proposal_failed` events (F6). |
| `POST /api/proposals/{id}/accept` | **T4** | The §4.4 writer — the ONLY `apply_soft_change` caller. Refuses non-pending (409), `acceptable:false` (409), and `WriteBoundaryError` (409, nothing written). On success: soft-tier merge-write, status `accepted`, event `{tier:"T4", initiator:"ceo", kind:"proposal_accept"}`. |
| `POST /api/proposals/{id}/reject` | T1-ish | Status `rejected`; discards; **no write**. Emits `proposal_reject` (F6). |
| `POST /api/proposals/{id}/acknowledge` | read | Status `acknowledged` (rubric_contradiction tracking, §5); **no write**. Emits `proposal_acknowledge` (F6). |
| `POST /api/coaches/{name}/revert` | **T4** | Revert an APPLIED addendum (F8): `merge_addendum(existing, band_id, "")` drops that band's marker section, then `apply_soft_change` writes the trimmed file (still soft-tier; same guarded writer, same `WriteBoundaryError` protection). Body carries `band_id`. Emits `addendum_revert` tier `T4` initiator `ceo`. Soft-tier changes are reversible by charter (§5) — this is how. |

All guards reuse `security.resolve_project_dir` / `safe_segment` / the `J()` redact pass exactly as
the existing endpoints do. Every state-changing endpoint records an event on the dispatcher ring so
the §4 audit + Activity feed tell the whole story (propose → ready/failed → accept/reject/revert), not
just the accept (F6).

### 7.6 Background propose execution (F1 — the propose path is expensive)
`POST /api/coaches/{name}/propose` must not block the request for the minutes a real loop run can
take (coach-LLM authoring + Marlow re-measure + held-out verify). It mirrors the **dispatcher
pattern** already in the codebase: kick the work onto a background thread, return a `running` handle,
and surface completion through the existing **SSE event ring** (`proposal_started` →
`proposal_ready` | `proposal_failed`), which the inbox already listens to. A per-coach **in-flight
guard** prevents stacking duplicate expensive runs. A `proposal_failed` event (the loop ran, spent,
and found no would-accept change) is **first-class and visible** — not a silent no-op. Tests inject a
synchronous fake `coach_propose_fn`, so e2e never spawns the real engine.

---

## 8. Frontend — `v-coaches` (additive; reuse the established design system)

- **Rail + view shell** — add `<div class="ic" data-go="v-coaches" data-rail="coaches">` to
  `index.html` and a `v-coaches` `<section>`; add `case "v-coaches": return renderCoaches();` to the
  `loadView` switch in `app.js`. Reuse `.crumb/.phead/.kpis/.card` and the status/plane color tokens.
- **`renderCoaches()`** — `GET /api/coaches` → two coach cards (identity + owned stages/bands chips +
  applied-addenda list + pending count) and the ledger card (reuse the Quality `led` markup).
- **The inbox** — `GET /api/proposals` → cards. Reuse the **`renderProposal` idiom** (Slice 5) for
  the in-card accept/reject lifecycle, with a **new T4 card class** (`.proposal.t4`) carrying the
  `T4 · persona write` chip + the `gr-ack` acknowledgement checkbox before Accept. The
  `rubric_contradiction` variant renders the CEO-INTERVIEW flag with **no Accept** + the lock line (§5).
- **The "ask the coach" button** (O4 locked → keep) — each coach card has a button that posts
  `POST /api/coaches/{name}/propose`. Because the real run is **expensive** (F1), the click first
  shows a **cost-aware confirm** ("this runs the coach + a re-measure — may take a few minutes"), then
  the card enters a **running** state (spinner + "Quill is working…") and resolves when the
  `proposal_ready`/`proposal_failed` SSE event arrives. It **writes nothing** (the write is still only
  on Accept). A `proposal_failed` shows an honest "ran, found nothing in-band" note.
- **Revert applied addendum** (F8) — each *applied* addendum in a coach card has a **Revert** action
  (`POST /api/coaches/{name}/revert` with `band_id`) so the CEO can undo an accepted note; soft-tier
  is reversible by charter (§5).
- **Overview signal** (F4) — the pending-proposal count surfaces on the **Overview** "needs-you" tray
  (and an "Awaiting you" KPI), exactly like gates do, so T4 items — especially a rare
  rubric-contradiction — aren't buried behind a rail click. The count comes from
  `GET /api/proposals?status=pending`.
- **The Echo cohort-threshold box** (O3 locked) — the Echo lane shows a small **number-counter input**
  bound to `defaults.echo_cohort_min` (default 5); changing it PUTs `/api/settings` (T1 reversible)
  so the CEO tunes the n=1 guard without editing JSON.
- **Plane/tier tokens** — extend the existing token set with a T4 accent (distinct from the T1 lilac
  `p-chat` plane and the deterministic gate green). One status language; reuse, don't reinvent.
- **No new modal/drawer system** — the inbox is inline on the view (like the Quality cards), not a
  dialog. (The hard `openDialog`/`openDrawer` systems are for T2/T3; T4 accept is an in-card confirm.)

---

## 9. Echo 📈 — what's planned now vs shelled until #7

| Now (Slice 6) | Shelled (lands with #7) |
|---|---|
| The **injectable `app.state.echo_fn` seam** (read-only, returns cohort hypotheses). | The **real Echo engine + registry entry + adapter** (pulls YouTube Analytics, cohort/observational methodology, §5). |
| The **proposal data contract** for Echo (the §3 envelope: `source:"echo"`, `evidence.cohort`, the `rubric_contradiction` kind). | Real cohort data, the diagnosis-map routing (§5), the performance contract + CEO report. |
| **Normalization + the n=1 guard** (`refresh_echo` drops cohorts below `defaults.echo_cohort_min`, **default 5**, CEO-editable via a counter box, §7.4 — locked). | — (the threshold is a live setting now; only the real cohort *data* is shelled). |
| The **card UI** for both Echo soft proposals and rubric-CONTRADICTION CEO-interview flags. | — |

Echo **proposes, never writes** — identical accept path to a coach `soft_addendum` (the §4.4 guarded
writer); a `rubric_contradiction` is accept-disabled. The seam mirrors `publish.py`'s read-only,
fires-nothing contract: until #7, `echo_fn` is `None` and the Echo lane is empty but fully rendered.

**The raw `echo_fn` seam contract (F7 — frozen now so #7 builds against a fixed target).**
`echo_fn(projects_dir) -> list[dict]`, read-only; each raw dict is normalized by
`normalize_echo_proposal` and dropped if malformed or below `echo_cohort_min`:

```jsonc
{
  "kind": "soft_addendum" | "rubric_contradiction",
  "band_id": "narration:speech_cadence",
  "direction": "<the rubric-decided change string, or '' for a contradiction>",
  "stage": "narration", "owner": "Cadence", "coach": "production_coach" | null,
  "evidence": { "cohort": { "n": 8, "window": "last 30 days", "metric": "avg_view_duration",
                            "stat": "−18% vs channel median", "video_slugs": ["…"] } }
  // n=1 / missing cohort ⇒ dropped (E10/E17). A contradiction names the band reality disagrees with.
}
```
The real Echo engine (#7) emits exactly this; Slice 6's fakes do too.

---

## 10. Edge cases (continuing the master-spec E-numbering)

| # | Scenario | Required behavior |
|---|---|---|
| E16 | A proposal's `soft_path` is tampered to point at the rubric/contracts/spine | Accept calls `apply_soft_change`, which raises `WriteBoundaryError` → **409, nothing written**. The record is never trusted; the guarded writer decides. |
| E17 | Echo emits a single-outcome (n=1) item | Dropped at `normalize_echo_proposal`; **never surfaced** (the §5 n=1 guard at the proposal layer; reaffirms E10). |
| E18 | A `rubric_contradiction` proposal | `acceptable:false`; no Accept button; `POST …/accept` → **409**; Acknowledge tracks, never writes (reaffirms E11). |
| E19 | Accept on an already-accepted/rejected proposal | Idempotent **409 "already resolved"**; never a re-write (mirrors gate idempotency). |
| E20 | `control_room_proposals.json` missing/corrupt | Degrades to empty; parsed in place, never rewritten (mirrors E13). |
| E21 | `echo_fn` unset or raises | Empty Echo lane; **never a 500** (mirrors intake/publish). |
| E22 | Accept a second addendum for a persona that already has an applied `COACH_ADDENDUM.md` | **(O2 smart-accumulate)** `merge_addendum` replaces only the **same-band** section and keeps other-band sections; the merged file is written through the single `apply_soft_change` call. Coaching compounds across metrics; the same metric never piles up duplicates. |
| E23 | A pending proposal's target band already moved (a later render re-scored in band) | **(O1 locked)** On accept, the stored `evidence.verdict` is shown with a **stale** badge and accept still writes the soft tier (it's reversible) — no accept-time re-check, no auto-expiry. |
| E24 | `can_write_rubric()` regression | Asserted true in a negative-safety test; any change that makes it false fails CI. |
| E25 | `POST …/propose` while a run for that coach is already in flight | The per-coach in-flight guard (F1/§7.6) refuses the duplicate; the request returns the running handle, not a second expensive run. |
| E26 | Two near-simultaneous store mutations (accept + echo-refresh, or two tabs) | The write-lock + atomic replace serializes them (F2); no `last-writer-wins` status loss; no duplicate `prop-NNNN` id. |
| E27 | Revert an addendum for a band whose section isn't in the file (already reverted / never applied) | `merge_addendum(existing, band_id, "")` is a no-op on that band; the write is idempotent; **409 "nothing to revert"** if the file/section is absent (F8). |

---

## 11. Testing (injectable seams; no real LLM/engine)

**Unit (mirror `test_settings_api`/`test_chat_api`/`test_publish_api`):**
- `proposals_store`: tolerant load (E20), monotonic id, dedupe, status transitions, normalization
  dropping n=1 (E17) and malformed items.
- `proposals.propose_from_loop` with an injected fake `propose_fn`: emits a pending proposal **and
  writes no persona file** (assert the `soft_path` does **not** exist while pending — the
  no-auto-apply-unreviewed property).
- Accept endpoint: writes a soft-tier file to an **injected temp persona dir** (never the real
  personas); `WriteBoundaryError` → 409 on a tampered denied `soft_path` (E16); `acceptable:false`
  → 409 (E18); non-pending → 409 (E19).
- `data.coaches` / `coach_owned_bands`: correct stage/band ownership, tolerant of absent rubric.
- `proposals.merge_addendum` (O2/F3): keys on the `<!-- gstack-band: … -->` **marker** (not the prose
  header); a new same-band note **replaces** that band's section and **keeps** other-band sections; an
  empty `new_section` **removes** the band (the F8 revert); empty/absent existing file yields just the
  new section; round-trips cleanly.
- **Store concurrency (F2/E26):** two concurrent `set_status`/`upsert` calls serialize via the
  write-lock — no dropped status, no duplicate id (a threaded stress test).
- **Revert (F8/E27):** `POST …/revert` trims the band's marker section via `apply_soft_change`;
  reverting an absent section → 409; the write stays soft-tier.

**Negative-safety (the point of the slice):**
- A T4 accept (and revert) can **only** write soft-tier — assert no write outside the soft dir; assert
  the rubric path is refused.
- **No auto-apply-unreviewed** — generating a proposal writes nothing; the file appears only after
  accept. The **async propose** path (F1) writes nothing even while the background run is in flight.
- **Echo proposes, never writes** — listing/refreshing with an injected `echo_fn` performs no write;
  a `rubric_contradiction` has no accept path.
- **`can_write_rubric()` stays true** (E24).

**e2e (Playwright; `domcontentloaded`; restart server after backend change):**
- `v-coaches` renders both coaches + owned bands + ledger; the inbox is **empty by default** (F9).
- "Ask the coach" (faked synchronous `coach_propose_fn`) shows the cost confirm → running →
  `proposal_ready`; a faked failure shows the honest "found nothing in-band" note (F1/F6).
- Accept a faked `soft_addendum` (apply target = a temp dir) → card resolves, event appears in the
  Activity feed tagged `ceo` / T4; the **Overview pending count** decrements (F4).
- A `rubric_contradiction` card shows **no Accept button**; Acknowledge resolves it.
- Revert an applied addendum → the band's section is gone; the file still validates as soft-tier (F8).
- The Echo lane renders from an injected `echo_fn` and is empty (no crash) when unset.

---

## 12. Build order

**Buildable NOW (coaches view + the full T4 surface):**
1. `proposals_store.py` + the §3 envelope + normalization/n=1 guard (+ unit tests).
2. `data.coach_owned_bands` + `data.coaches` (+ unit tests).
3. `proposals.py` — `propose_from_loop` + `merge_addendum` (marker-keyed) + `refresh_echo` (+ unit
   tests, incl. "writes nothing while pending").
4. `app.py` — the three `app.state` seams + the 7 endpoints + **background propose execution** (§7.6)
   (+ API tests **incl. negative-safety + the store-concurrency stress test**).
5. Frontend — `v-coaches` rail + `renderCoaches` + the T4 inbox cards (accept/reject/acknowledge/
   revert) + the cost-confirm/running states + the Overview pending signal, reusing
   `renderProposal`/`gr-ack` idioms + the token set.
6. Echo lane — the `echo_fn` seam (raw contract §9) + the Echo soft + rubric-contradiction cards
   (shelled data) + the `echo_cohort_min` counter box.
7. e2e — the flows in §11.

**SHELLED until #7 (Echo real data):**
- The real Echo engine + registry entry + adapter + YouTube-Analytics cohort methodology (§5/§9).
- The actual cohort-size threshold for the n=1 guard (O3) — a placeholder constant in Slice 6.
- The **post-render auto-propose wiring** (loop fires propose-only automatically after a scored
  render) — Slice 6 demonstrates the identical path via the on-demand
  `POST /api/coaches/{name}/propose`; auto-firing after scoring is a thin follow-on through the
  same seam + store.

---

## 13. What this slice deliberately does NOT do (YAGNI)

- **No rubric write path of any kind** — a contradiction escalates read-only; the CEO edits
  `rubric.json` by hand if they choose (outside the dashboard).
- **No `loop.py`/`diagnose.py`/`rollup.py`/coach-adapter/registry edits** — every touch is via an
  existing seam.
- **No new dialog/drawer system** — T4 accept is an in-card confirm; the hard modal/drawer machinery
  stays reserved for T2/T3.
- **No chat path to T4** — the chat is T1-only by construction (§8/E7), unchanged. The chat may
  *navigate* to the Coaches view but can never accept a proposal.
- **No Echo engine, no analytics fetch, no registry entry for Echo** — seam + contract + card only.
- **No free-form multi-note pile-ups** — coaching notes accumulate **per band** only (O2
  smart-accumulate); the same metric never stacks duplicate sections.

---

## 14. CEO REVIEW — decisions

### 14.1 Resolved (locked 2026-06-24)
- **O1 — Stale proposals (E23): ✅ Accept anyway, show a "this might be old" hint.** A persona
  addendum is reversible, so a stale accept is low-risk; the card shows the stored verdict with a
  stale badge and still writes the soft tier. No accept-time re-check, no auto-expiry.
- **O3 — The n=1 guard's number: ✅ Default 5, CEO-editable in the dashboard.** Ships as the setting
  `defaults.echo_cohort_min` (default `5`) with a **number-counter box** on the Coaches view's Echo
  lane (§7.4 / §8). `refresh_echo` drops any cohort below it. The *value* is live now; only the real
  cohort *data* waits for #7.
- **O4 — On-demand "ask the coach" button: ✅ Keep it.** `POST /api/coaches/{name}/propose` adds a
  pending proposal and **writes nothing** — the visible, testable path; the write is still only on
  Accept.
- **O5 — Rubric-contradiction notification: ✅ Dashboard only (Activity feed + inbox) for Slice 6.**
  Email/Slack push is explicitly deferred (revisit when Echo is real, #7).

- **O2 — How accepted coaching notes stack per specialist (E22): ✅ Smart accumulate.** A
  specialist's `COACH_ADDENDUM.md` holds one section **per band**, delimited by a dashboard-owned
  machine marker `<!-- gstack-band: <band_id> -->` (F3 — not the prose header, which another module
  authors). On accept, the new note **replaces the section for its band** and **leaves other-band
  sections intact** — so coaching compounds across different metrics without piling up duplicates or
  contradicting itself on the same metric. The merge happens dashboard-side (§7.2); the result is
  still written through the single guarded `apply_soft_change` call (§4.4).
- **O6 — Keep the loop's `write_soft=True` auto-apply primitive: ✅ Keep it, unplugged.** It survives
  only for the loop's own write-boundary unit tests (the crash-test dummy proving the boundary holds
  under the most aggressive setting); nothing in the product can trigger it (§4.3).

### 14.2 Still open
None — all decisions for this slice are locked.

---

## 15. Review provenance (hardening pass, 2026-06-24)

Ran the CEO / eng / design review lenses against the draft. Nine findings folded in (all additive,
none changed the architecture):

| # | Lens | Finding | Where |
|---|---|---|---|
| F1 | Eng | The "ask the coach" path runs the real loop (coach-LLM + Marlow re-measure + held-out verify) — minutes + real spend. Now **background-executed** with a cost confirm + in-flight guard. | §7.2, §7.5, §7.6, §8, E25 |
| F2 | Eng | The proposals store is read-modify-write under multi-tab — `last-writer-wins` race. Now **write-lock + atomic replace**. | §7.1, E26 |
| F3 | Eng | `merge_addendum` keyed on a prose header another module owns (silent drift). Now keys on a **dashboard-owned machine marker**. | §3, §4.4, §7.2, O2 |
| F4 | Design | Pending proposals were buried behind a rail click. Now surfaced on the **Overview** needs-you tray + KPI. | §8 |
| F5 | Eng | Stale-accept × smart-accumulate could silently overwrite a newer same-band note. Now a **`supersedes` warning** on accept. | §3, §4.4, E23 |
| F6 | Eng | Only the accept hit the event ring. Now propose/ready/failed/reject/acknowledge/revert all emit events. | §7.5 |
| F7 | Contract | The raw `echo_fn()` return shape wasn't pinned. Now **frozen** so #7 builds against it. | §9 |
| F8 | Design | No way to undo an accepted addendum. Added a **revert** action (soft-tier, reversible by charter). | §6.2, §7.5, E27 |
| F9 | CEO | The inbox is empty by default until the loop runs / the button is pressed. Stated plainly — not oversold. | §6 |
