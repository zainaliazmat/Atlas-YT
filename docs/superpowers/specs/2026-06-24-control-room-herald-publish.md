# Herald — the Publisher (sub-project #6): publish stage + publish gate

**Date:** 2026-06-24
**Status:** Spec for review (no code written this session) · hardened against a CEO/eng/design
plan-review pass 2026-06-24 — findings C1–C3 (strategy), E1–E8 (correctness/concurrency/security),
D1–D6 (UX) folded inline, tagged "review finding" at each site
**Author:** CEO + Atlas (brainstorming session) · grounded in the real `atlas/` code
**Roadmap ID:** **#6** — depends on **#1** (assembly-line dispatcher) + **#4** (Settings channels) ·
lands **with #8 Glint** (the `package` step delegates to Glint; the CEO picks one thumbnail in the
T3 modal).

> This spec extends the Control Room into its last write surface: **publishing to YouTube**. It is
> the highest-risk write in the whole system — irreversible, external, ToS-governed — so it is the
> **T3** tier of the write-authority model (master design §4) and every safety property is re-earned
> per the rules below. **Read the master design `2026-06-23-control-room-design.md` §4/§9/§12/§13
> first** — this spec assumes it.

**Two CEO decisions locked at design time (2026-06-24):**
1. **Publish-flow entry = "auto-prepare, then park" (Option A).** Every rendered video auto-advances
   into a terminal `package` stage, then **always parks at a hard, un-disableable T3 publish gate**.
   The upload fires **only** on the CEO's sign-off of the exact package. (§2)
2. **No Google credentials exist yet.** Everything buildable-now is built behind fakes; live OAuth +
   the real upload are shelled behind a seam. **Plus** the CEO asked for an in-dashboard **"Go-Live
   Setup" guide** — direct links + step-by-step + live ✅/⬜ checkpoints. That is a first-class part of
   this spec (§7) and is buildable now. (§11, §13)

---

## 1. Goal & one-line identity

**Herald 📡 is the Publisher.** It is the terminal pair of stations on the assembly line: it
**packages** a finished video (title / description / tags + delegates to Glint for thumbnails) and,
**only after the CEO approves the exact package**, **uploads** it to the niche's mapped YouTube
channel — scheduled-after-approval, quota-bounded, verification-gated.

**Herald's SOUL is distribution judgment** (titling, packaging, channel routing); the **upload is just
hands** — a deterministic, injectable seam the engine never reaches around.

**Honest framing of the value split (review finding C1).** The real upload (M5/M6) is **externally
gated** — it depends on Google sensitive-scope verification the CEO has not started, which Google can
delay for weeks or reject. **The plan is deliberately designed to be valuable even if public publishing
never lands:** the buildable-now half — a CEO-reviewed publish package, 3 Glint thumbnails, the
Go-Live Setup guide, the connection state machine, and **real private uploads** (decision #5) — is
useful on its own. Treat M5/M6 as "externally gated, may not arrive," not "shelled, arrives later."

The single hardest fact about Herald: **most of it is buildable and testable today, but the actual
"make it public" step is blocked on Google-side verification the CEO does not yet have** (§9 of the
master design, §11 here). The design makes that boundary explicit and honest rather than hiding it.

---

## 2. The spine change (the central decision — settled: Option A)

Today the line is 10 stages ending at `render`; a video reaches `status: "done"` there
(`pipeline.py:STAGES`, `pipeline.produce()`). The two human gates are `_factcheck_gate` (after its
stage) and `_final_render_gate` (before `render`), both **pause-and-resume via `project.json`**
(`status = "blocked_at_<gate>"` + a `gates{}` entry; the function persists and **returns**, never
blocks mid-tool).

Herald appends **two terminal stages** and **one new gate**, reusing that exact machinery:

```
… → audiomix → render ──► package ──► [T3 GATE_PUBLISH: ALWAYS PARK] ──► publish (upload)
                          (auto)        (un-disableable; cleared only        (fires ONLY with the
                          Herald+Glint   by an approved package on disk)      CEO-approved package)
```

### 2.1 `package` — a normal terminal stage (auto, no external effect)
- Added to `STAGES` as
  `Stage("package", "herald", "packaging for publish", herald.produce_package, "publish_package")`.
- Producer signature is the usual `(pdir, topic) -> Artifact` (mirrors `sage.produce_factcheck`). It
  reads the project's artifacts (`script.json` for title/hook, `style_guide.json`, `project.json` for
  niche) and writes `publish_package.json` (new contract §6). It **delegates to Glint** (§5) for a set
  of 3 thumbnail candidates (`thumbnail_set.json`, written by Glint, referenced by the package).
- **Reversible, deterministic, no network upload** — it is cheap to re-run (`rerun(slug, "package")`).
  Cost note accepted at design time: this spends **one Glint run per rendered video** even if the CEO
  never publishes it. Acceptable at ~2–3 videos in flight / ~6 publishes/day. **A settings toggle
  `package.eager_thumbnails` (default `true`) lets the CEO switch to lazy mode** (decision
  2026-06-24): when `false`, the `package` stage writes the metadata package but **defers the Glint
  run until the T3 modal is first opened** for that video, so thumbnail cost is spent only on videos
  actually being published.
- Validated against its frozen contract at the boundary like every other stage (`pipeline._run_stage`
  → `contracts.validate("publish_package", …)`). A malformed package fails the stage
  **deterministically** (no retry, §6.4 of the master design) — it never reaches the gate.

### 2.2 `GATE_PUBLISH` — the T3 checkpoint (un-disableable by construction)
- New gate constant `GATE_PUBLISH = "publish"`, added to `project["gates"]` in `_new_project`.
- A new checkpoint `_publish_gate(project, pdir, progress)` mirrors `_final_render_gate` **except it
  ignores `cfg_gates`**: there is **no toggle that turns it off**. `unattended=True` / `gates=False`
  (which `_new_project` applies to the other two gates) **must not** disable it. This is the
  structural form of master-design **E8** ("scheduled-but-unreviewed is impossible by construction"):
  the only way past this gate is an **approved package present on disk**.
- It returns `None` (let the line proceed to `publish`) **only when** `gates[publish].status ==
  "approved"` **and** `gates[publish].approved_package` exists. Otherwise it persists
  `status = "blocked_at_publish"` with the package as `details` and **returns** (parks; holds no
  station — §6.3 of the master design).
- In the `produce()` loop this is wired exactly like the render gate: before running the `publish`
  stage, `if stage.key == "publish": blocked = _publish_gate(...); if blocked is not None: return
  blocked`.

### 2.3 `publish` — the upload (special-cased, injected seam, fires only with the approved package)
- Added to `STAGES` as a terminal entry, but it is **not a generic producer**: the upload needs the
  per-channel OAuth token + the CEO-approved package, neither of which a pure `(pdir, topic)` producer
  (or a decoupled engine) may read globally.
- **Precise control flow (review finding E3 — the `producer=None` trap).** `Stage.producer` is typed
  `Callable` and the generic loop calls `stage.producer(...)` via `_run_stage`. So the `publish` stage
  carries a **sentinel/`None` producer that the generic path must never reach**: `produce()`
  **branches on `stage.key == "publish"` BEFORE the `if st.get("status") != "done": _run_stage(...)`
  block** (exactly as it special-cases the gates by `stage.key`) and calls a dedicated
  `_run_publish(project, pdir, uploader, approved_package, station_locks)` that holds the station lock,
  invokes the injected `uploader` seam, writes `publish_receipt.json`, and runs the receipt contract
  validation. The generic `_run_stage` is never called for `publish`.
- **Exactly-once / no duplicate upload (review finding E1 — the single most important correctness
  rule).** `videos.insert` is **not idempotent**, and stages are "skipped when `done`" while the
  dispatcher auto-retries transient failures — so a naive resume/retry could **upload the same video
  twice** (duplicate public video + double quota). Therefore:
  - `_run_publish` **first checks for an existing `publish_receipt.json` carrying a `video_id`** and
    **short-circuits** (re-validates + marks `done`, never re-uploads) if present — an idempotency
    guard, not a re-run.
  - The `publish` stage is classified so the **dispatcher NEVER auto-retries it** (it is never tagged
    `transient`; a failed upload parks for an explicit, human-initiated re-fire — §8 / H7). A bare
    network error mid-`insert` may have *already created* the video, so silent retry is forbidden.
  - On a transient `insert` error, `_run_publish` records `status:"failed"` + the error and, **before
    any re-fire, the re-entry idempotency check + a best-effort channel lookup for the slug** prevent a
    second upload.
- `produce()` gains **one new opt-in keyword**, `uploader: Callable | None = None`, alongside the
  existing `station_locks` / `should_cancel` hooks. When `None` (CLI / orchestrator / tests that don't
  publish), the line simply **parks at `GATE_PUBLISH` forever** — there is no path to upload without
  an injected uploader. This preserves byte-identical behaviour for every non-dashboard caller, and it
  means **the CLI/orchestrator can never publish** (only the deterministic dashboard fire route can).
- The upload uses the **exact `approved_package`** persisted at approval time — never a re-derived or
  mutated one (E8 / ToS "no post-submission metadata mutation").

### 2.4 What is explicitly NOT changed — and the one ripple to plan for
- The existing 10 stages, their order, their producers, and the two existing gates are **byte-for-byte
  unchanged**. The fact-check `block`-can-never-be-approved-away rule is untouched.
- The change is purely **additive**: two appended `STAGES` entries, one gate constant, one
  un-disableable checkpoint, one special-cased `publish` step, one opt-in `uploader` param.
- **The station-count ripple: 10 → 12 (review finding E4).** `STAGES` grows from 10 to 12 entries.
  Everything that enumerates stages must follow: `data.belt` (`stations[10]` → 12), the frontend
  occupancy strip + the per-video 10-segment spine track (→ 12 segments), `dispatcher._station_locks`
  (built from `STAGES`, auto-grows — OK), the Stage Inspector, and any hard-coded "10 stages" copy.
  This is mechanical but easy to miss; treat it as part of M1's definition of done.

---

## 3. The Herald agent (one registry entry + one adapter)

Mirrors the off-pipeline registration shape (Vera/Quill/Flux at `registry.py:270–347`) and the adapter
template (`adapters/sage.py`). **The engine never imports Atlas; Atlas stamps `schema_version` +
validates at the seam** (PROJECT_CONTEXT §11).

- **New sibling project `publisher/`** following the fleet skeleton exactly: `publish_engine.py` (pure
  + injectable), `run.py`, `chat.py`, `llm.py`, `chat_state.py`/`compaction.py`, `SKILL.md`, `soul/`
  ({SOUL.md, STYLE.md, examples/}), `tests/`.
- **One `AgentEntry`** `name="herald"`, `display="Herald"`, `emoji="📡"`,
  `adapter_cls=HeraldAdapter`, `role="Publisher"`.
- **Jobs (the LLM may call only the SAFE one):**
  - `package_video` → assemble the package + delegate to Glint → returns the package digest. This is
    a normal delegable JobSpec (generated as an SDK tool) — packaging is T1-ish (reversible, no
    external effect).
  - **There is deliberately NO `publish` / `upload` JobSpec.** The upload is **not** exposed as a
    generated SDK tool, so the orchestrator/chat plane **cannot call it** (the T3 authorizing action
    lives only on the deterministic UI — master design §4/§8). This is a negative-safety invariant
    with a test (§10).
- **`HeraldAdapter`** (`adapters/herald.py`): `run_job("package_video", …)` runs the engine in-process
  via the isolated loader, stamps `schema_version`, validates `publish_package` at the boundary;
  `ask` inherited from `base.Adapter` for the persona. **The uploader is not in the adapter's job
  table** — it is the separate injected seam (§4), reachable only from `_run_publish` / the dashboard
  fire route.

---

## 4. Decoupling, seams & testability (the §11 rule, applied)

Three injectable seams keep the engine pure and keep tests offline. Mirrors the existing
`produce_fn` / `find_topics_fn` / `chat_fn` injection on `app.state`.

| Seam | Real impl (prod) | Fake (tests/e2e) | Where it lives |
|---|---|---|---|
| `uploader(approved_package, *, channel, token) -> receipt` | the **engine's pure `publish_engine.upload(package, token)`** doing the real `videos.insert` + thumbnail set + (optional) `publishAt`, wired as the seam | returns a canned `{video_id, status, …}`; touches no network | `app.state.uploader`, passed by the dispatcher into `produce(uploader=…)` |
| `oauth_fn` (connect/exchange/refresh) | Google OAuth flow → refresh token → `channels.list?mine=true` | returns a canned channelId + token; no network | `app.state.oauth_fn`, used by the connect endpoints (§7) |
| `glint_fn` (thumbnail candidates) | Glint #8 `thumbnail_generate_candidates` via the registry | canned 3-candidate `thumbnail_set` | injected into the `package` stage (defaults to the real Glint adapter) |

**Where the real upload code lives (review finding E5 — pin the boundary).** The YouTube SDK / real
`videos.insert` lives in the **`publisher/` engine** as a pure `upload(package, token) -> receipt`
(token + package **passed in**, never read globally) — NOT in `atlas/dashboard/`. The dashboard owns
only the *wiring*: it decrypts the token (server-side, §6.4) and the dispatcher passes the engine's
`upload` in as the `uploader` seam. This keeps the YouTube dependency out of the dashboard and matches
the §11 decoupling rule (engine pure + injectable; Atlas validates the receipt at the seam).

**Hard rules:**
- The **engine never imports Atlas**, never reads Settings or secrets globally; channel + token +
  approved package are **passed in**. `ANTHROPIC_API_KEY` / any `YOUTUBE_*` key is **never set in
  tests**; e2e injects the fakes and never reaches YouTube (the Slice-5 precedent — handoff gotcha 3).
- **Secrets never appear in any HTTP response, log line, SSE event, or the UI.** The token store (§6.4)
  returns tokens only to the server-side uploader seam; everything else sees a redacted
  connection-state badge.

---

## 5. Glint (#8) integration — the thumbnail set

The `package` stage delegates to **Glint** (spec `2026-06-23-thumbnail-artist-Glint.md`) for **N=3
distinct thumbnail candidates** (local, license-clean, 1280×720 HTML+Chrome stills →
`projects/<slug>/thumbnails/candidate_N.png` + `thumbnail_set.json`). Then:
- The **T3 modal** shows the 3 candidates; the **CEO picks exactly one** (a human T3 decision — Glint
  only produces the set; it satisfies no gate).
- The chosen `candidate_id` becomes part of the **approved package** and is uploaded as the custom
  thumbnail (which itself requires the channel to be **phone-verified** — §9 gauntlet).
- **Graceful degradation:** if Glint misses (no local focal source / a render miss), the package
  carries a **placeholder candidate + a note**; the modal shows the gap honestly and the CEO can still
  publish without a custom thumbnail (YouTube auto-frame), or hold. The `package` stage never crashes
  on a Glint miss (fleet rule).
- **Routing decision flagged (Glint §10):** when thumbnails later enter the eval loop,
  `coach_for_stage` needs to route punch-text→Quill vs visual-craft→Flux. **Flag, don't build** here.

---

## 6. New artifacts, contracts & stores

### 6.1 `publish_package.json` (new contract — additive)
`atlas/contracts/publish_package.schema.json` (Draft 2020-12, `additionalProperties: true`, requires
`schema_version`). Written by the `package` stage; reviewed in the T3 modal; the **approved** copy is
what uploads.
```
{ schema_version, slug, title, description, tags[ ],
  thumbnail: { set_ref: "thumbnails/thumbnail_set.json", candidates[ ], selected_candidate_id|null },
  visibility: "private"|"unlisted"|"public",
  schedule: { publish_at: <iso8601>|null, timezone },     # null until approved (E8)
  routing: { niche, channel_id|null, channel_title|null },
  category_id|null, made_for_kids: false }
```

### 6.2 `publish_receipt.json` (new contract — additive)
`atlas/contracts/publish_receipt.schema.json`. Written by `_run_publish` from the uploader's result —
the audited record of what actually went out.
```
{ schema_version, slug, video_id|null, channel_id, visibility,
  publish_at|null, published_at|null, status: "uploaded"|"scheduled"|"failed"|"queued_for_quota",
  quota_units_spent, approved_package_hash, error|null, initiator: "ceo", ts }
```
- `approved_package_hash` pins the receipt to the exact reviewed package (tamper-evidence for the E8
  audit).

Both are wired into `contracts/SCHEMA_FILES` + `validate()` / `version_for()` like the others.
(`thumbnail_set` arrives with Glint #8.)

### 6.3 Settings extension (channels — extend, don't replace)
`settings_store.py` already models channels (`connection_status` ∈ `CONNECTION_STATES`,
`project_verified`, `channel_phone_verified`, `scopes`, `niche_id`) + the shared `QUOTA` ceiling +
`length_for_niche`. Herald **adds, behind the same tolerant `validate_settings`**:
- `channels[].oauth_client_ref` (a pointer into the secret store — **never the secret itself**),
- `channels[].last_connected_ts` / `channels[].token_expiry_hint` (for the state machine UI),
- a top-level `oauth_client` presence flag (client_id/secret live in the secret store, not here).
No secret value is ever stored in `control_room_settings.json` (it is gitignored, but still
plaintext) — that file keeps only **references + non-secret flags**.

### 6.4 The encrypted secret store (new — dashboard-owned)
`atlas/dashboard/secrets_store.py`: per-channel refresh tokens + the OAuth client secret, **encrypted
at rest** (`cryptography` Fernet/AES-GCM). **The master key is a local `0600` keyfile** under
`atlas/dashboard/` (gitignored), auto-generated on first run (decision 2026-06-24 — simplest for a
single-user setup; an env-var override `HERALD_SECRET_KEY` is honored if set). API: `save_token(channel_id, refresh_token)`,
`get_token(channel_id)`, `delete_token`, `has_token`, `save_client(...)`. **`get_token` is only ever
called by the server-side uploader seam** — never surfaced over HTTP. Keyed by the **real `channelId`**
(from `channels.list?mine=true`), never the user's label.

**Honest threat model (review finding E7).** The key file sits next to the encrypted tokens under
`atlas/dashboard/`, so encryption protects against **casual leakage** — a token accidentally committed
to git, copied in a backup, or read from `control_room_settings.json` — **not** against an attacker who
already has filesystem read on the box (they'd have both the lockbox and the key). That is an accepted
trade for a **single-user local dashboard**; the spec states it rather than implying defence-in-depth
it doesn't provide. The `HERALD_SECRET_KEY` env override exists for anyone who wants to move the key
off-disk.

---

## 7. The dashboard surfaces (extend `atlas/dashboard/`)

### 7.1 The T3 publish-confirm modal — wire the existing shell to a real fire
The Slice-5 shell already exists: `openPublishModal` (`static/app.js`), `GET /api/publish/{slug}`
(`publish.py`, read-only), a **HARD dialog** (no Escape/backdrop close), the niche→channel routing +
the two verification blockers, `schedule: null`, `fire_enabled: False`, and **no fire route** (POST →
405). Herald turns it real:
- **`publish.publish_package(...)`** gains the **selected thumbnail candidates** (from Glint) and keeps
  computing `would_publish` / `blockers` from the live verification flags + quota.
- **NEW endpoint `POST /api/publish/{slug}/fire` (T3).** Body = the **exact reviewed package**:
  `{title, description, tags, selected_candidate_id, visibility, schedule}`. It:
  1. reloads the package + re-checks `would_publish` (verification + render-ready) and **live quota**;
  2. **refuses** (409 + reason) if any blocker holds — verification missing, no render, quota spent
     (→ §8 back-pressure), or a `block`-class state;
  3. **persists the approved package** to `project.json` `gates[publish] = {status:"approved",
     approved_package, approved_ts}` + an audited `history` entry (`initiator:"ceo"`);
  4. calls **`dispatcher.resume(slug, "publish", initiator="ceo", wait=True)`** — which runs the
     `publish` stage through the belt's station locks with the injected `uploader`, exactly as the T2
     gate-approve already calls `dispatcher.resume(slug, gate, wait=True)` today (`app.py:_approve_gate`);
  5. returns the **receipt** (`video_id` / scheduled go-live / status), read back from disk via
     `dispatcher._disk_outcome`-style logic.
- This **fire route is the single origin of an upload** in the entire system. Chat has no equivalent
  (T1-only); the orchestrator has no publish tool (§3). Negative-safety tests assert both (§10).
- **Visibility default stays the safe `private`** (the shell's `DEFAULT_VISIBILITY`); public/scheduled
  is offered only when the gauntlet (§9) is clear.
- **Schedule timezone = the CEO's local timezone** by default (decision 2026-06-24): the modal's
  date-time picker reads as local time; the package stores `publish_at` as an absolute ISO-8601 with
  offset so the upload is unambiguous, and the `timezone` field records the chosen zone.
- **Modal visual hierarchy (review finding D1).** The modal is dense (3 thumbnails + editable
  title/desc/tags + visibility + schedule + COPPA + blockers + fire). Fixed top-to-bottom order so a
  one-way-door action reads clearly: **(1) pick a thumbnail → (2) review/edit title·description·tags →
  (3) set visibility & schedule & the COPPA `made_for_kids` choice → (4) blockers resolve → (5) the
  single, gated fire button** (disabled until no blocker remains). One primary action; no competing CTAs.
- **"Not set up yet" empty state (review finding D2).** With zero credentials `publish_package`
  returns ~5 blockers; the modal must NOT read as an error/broken wall of red. When the blocker set is
  "you have no working channel/verification at all," render a calm **"You're not set up to publish yet"**
  state whose primary action is **Open the Go-Live Setup guide** (§7.3) — not a stack of red rows.
- **Schedule guardrails (review finding D4 — scheduling is a one-way door).** The picker **rejects
  past times** and **echoes the resolved absolute go-live** back to the CEO ("goes live Fri Jun 27,
  2:00 PM your time · 18:00 UTC") before fire, so a timezone slip can't silently publish at the wrong
  hour. A scheduled time inside an exhausted quota window surfaces the §8 back-pressure note.
- **COPPA is an explicit choice (review finding D6).** `made_for_kids` (YouTube's required
  `selfDeclaredMadeForKids`) is a **forced yes/no** in the modal — never silently defaulted — because
  it is a legal declaration on every `videos.insert`.

### 7.2 The channels "broadcast bay" — wire the state machine to real connect/reconnect
The Settings → Channels shell (Slice 4) renders the connection-state badge + the two verification flags
+ the shared ~6/day quota banner + the disabled "Connect — arrives with Herald" affordance. Herald
wires:
- **`GET /api/channels/{id}/connect`** → starts OAuth via `oauth_fn` (real: redirect to Google consent
  with the sensitive scopes `youtube.upload` + `youtube.force-ssl` + `yt-analytics.readonly`).
- **`GET /api/oauth/callback`** → `oauth_fn` exchanges the code → refresh token → `channels.list?mine
  =true` → store the token in `secrets_store` keyed by the **real channelId** → set
  `connection_status = "connected"` + read back `project_verified` / `channel_phone_verified`.
- **OAuth CSRF protection (review finding E6).** `connect` mints a single-use `state` nonce (stored
  server-side, bound to the session); `callback` **rejects any response whose `state` doesn't match**.
  Without it the callback is CSRF-open (an attacker could graft their channel's token onto the CEO's
  session). Note the **redirect-URI constraint**: the callback URI (e.g. `http://127.0.0.1:8848/api/
  oauth/callback`) must be registered in the Cloud OAuth client; the local-only dashboard makes this
  straightforward (a Setup-guide step).
- **One-click reconnect** for the `needs-reconnect | expired | revoked` states. The state machine
  (`CONNECTION_STATES`) drives the badge; the **7-day "Testing"-mode expiry, 6-month idle, 100-token
  cap, and revocation** are all surfaced as the badge + a reconnect CTA (master design §9).
- **Proactive disconnect surface (review finding D3).** A token can die silently (the 7-day Testing
  expiry hits weekly). The CEO must **not** discover it only when opening the publish modal on a
  time-sensitive video. The connection-state machine drives a **persistent "Channel X needs reconnect"
  banner** in the broadcast bay (and a needs-you-tray chip), surfaced before any publish attempt.
- All of this is **faked in tests** (`oauth_fn` returns canned values); **shelled for real until creds
  exist** (§11). The token value is never returned to the browser.

### 7.3 The "Go-Live Setup" guide (the CEO's ask — buildable now)
A new guided-checklist surface (a `v-broadcast` rail, or an expandable panel atop Settings → Channels)
that walks the CEO through the **entire** Google setup, with **direct links** and **live ✅/⬜
checkpoints** read from real state:

| # | Step | Direct link | Checkpoint source (✅ when…) |
|---|---|---|---|
| 1 | Create a Google Cloud project | `console.cloud.google.com/projectcreate` | `oauth_client` present / CEO marks done |
| 2 | Enable **YouTube Data API v3** | `console.cloud.google.com/apis/library/youtube.googleapis.com` | CEO marks done |
| 3 | Configure the **OAuth consent screen** + add the 3 scopes | `console.cloud.google.com/apis/credentials/consent` | scopes present on the channel row |
| 4 | Create an **OAuth client** + paste client_id/secret | (same console) | `secrets_store.has_client()` |
| 5 | Submit for **sensitive-scope verification** | console verification page | `channel.project_verified` |
| 6 | **Phone-verify** each channel | `youtube.com/verify` | `channel.channel_phone_verified` |
| 7 | **Connect** each channel (OAuth) | the §7.2 connect button | `connection_status == "connected"` |
| 8 | **Map** niche → channel | Settings → Niches | the niche row's `channel_id` set |

- **`GET /api/broadcast/setup`** computes the checklist state per channel from `settings_store` +
  `secrets_store` (no secrets leaked — only booleans). Each step shows **done / remaining**, the next
  action, and *why it matters* (e.g. "until step 5, every upload is forced PRIVATE").
- Honest banner at the top: **"Public/scheduled publishing is impossible until steps 5 + 6 are green.
  Until then Herald can only upload as PRIVATE."** (The §9 reality, stated plainly.)
- Buildable **now**: the checklist + links + flag-driven checkpoints need no Google account; only steps
  4/7 (real OAuth) are shelled behind `oauth_fn`.
- **Multi-channel view (review finding D5).** With several channels at different stages (A verified, B
  not), render a **per-channel accordion** — each channel a collapsible row showing its 8-step
  checklist — under a **roll-up status** line ("1 of 2 channels ready to publish"). Avoids a confusing
  single flat list when the steps differ per channel.

---

## 8. Quota back-pressure (E9)

`videos.insert` costs **1600 units** against a **project-wide 10,000/day** → **~6 uploads/day SHARED
across ALL channels** (`settings_store.QUOTA`; adding channels does **not** add quota). Herald:
- shows the **shared** ceiling + units-spent-today in the modal + the channels bay;
- **reserve-then-spend, not read-then-act (review finding E2).** The fire path must **reserve** a quota
  slot under a lock **before** the upload and **commit** the spend on success (or release on failure) —
  not merely read a counter and hope. A persisted daily counter (`projects/`-level or a dashboard-owned
  `publish_quota.json`, keyed by the UTC reset day) plus a `threading.Lock` makes the check
  **check-and-reserve atomic**, so two near-simultaneous fires (H8) cannot both pass and both exceed the
  ceiling. The `publish` station is also single-occupancy via the belt lock, which serialises the
  uploads themselves; the counter lock guards the *decision*.
- on a fire when quota is spent (no slot to reserve), **does not upload** — it writes a `publish_receipt`
  with `status:"queued_for_quota"`, parks the project at `blocked_at_publish` with a "queued for next
  window" detail + the reset timestamp, and shows it in the needs-you tray;
- a lightweight **drain** (checked on the next fire attempt / a periodic dispatcher tick) releases
  queued publishes when the daily window resets — FIFO, single quota pool.
- **Strategic ceiling, stated honestly (review finding C3).** The belt can render far more than ~6
  videos/day, but Herald can publish only ~6/day **shared across all channels**. That production↔publish
  throughput mismatch is a real ceiling on the "agency at volume" goal, not just a transient back-
  pressure detail — surface it in the channels bay so it is never a surprise.
- **Build-time caveat carried (master design §9):** Google is mid-migration to a separate-bucket model
  that may instead cap `videos.insert` at ~100/day. **Verify the project's actual Console quota at
  build time** (the M0 spike, §11) and make `QUOTA` reflect reality.

---

## 9. The verification gauntlet + honest degradation (the #1 feasibility risk)

Public/scheduled publishing is **impossible** until **BOTH**:
- **(a)** the Cloud **project** passes Google **sensitive-scope verification** — else every
  `videos.insert` is **forced PRIVATE** (`publishAt` cannot make it public), and
- **(b)** each **channel** is **phone-verified** — else **no custom thumbnail, no scheduling, no
  >15-min videos** (phone verification is rationed to 2 channels/number/year).

Herald **gates the fire action on both flags** (already surfaced as `project_verified` /
`channel_phone_verified` blockers in `publish.py`) and **degrades honestly**:
- both green → public/scheduled offered;
- project verified, channel **not** phone-verified → **private-only** (no schedule, no custom
  thumbnail), stated in the modal;
- project **not** verified → **private-only regardless** + a link to step 5 of the Setup guide;
- the modal **never silently downgrades** a requested public→private without saying so.

---

## 10. Write-authority mapping (§4) & negative-safety invariants

| Action | Tier | Guard |
|---|---|---|
| `package_video` (assemble package + Glint) | **T1** | reversible, re-runnable; an SDK tool; no external effect |
| Connect / reconnect a channel (OAuth) | **T1** | reversible internal; light confirm; secrets never surfaced |
| Edit channels / niche map / settings | **T1** | existing `PUT /api/settings` (validated, reversible) |
| **Fire a publish** (`POST /api/publish/{slug}/fire`) | **T3** | hard structured confirm + enforced review; the **only** upload origin; `initiator:"ceo"`; un-disableable gate; approved-package-exact; audited |

**Negative-safety invariants (each a test, §11):**
- There is **no `publish`/`upload` SDK tool** → the orchestrator/chat plane cannot call the upload.
- `GATE_PUBLISH` is **un-disableable** → `unattended`/`gates=False` parks, never fires (E8).
- The upload runs **only** from the deterministic fire route, **only** with an `approved_package` on
  disk, and uses it **exactly** (`approved_package_hash`) → no post-submission mutation (ToS).
- The chat `POST /api/chat/act` already rejects any non-T1 kind (`NotReversibleError` → 400) — publish
  is not added to `T1_ACTION_KINDS`; chat may navigate to the modal but never satisfy it.
- Tokens never appear in any response/log/SSE/UI.

**Audit / observability events (review finding E8).** The `EventRing` (with its `initiator` plane —
the §4 audit property) carries named publish events so the E8/ToS trail is a first-class deliverable,
not implied: `package_ready`, `publish_fired{initiator:"ceo"}`, `publish_succeeded{video_id,
visibility, publish_at}`, `publish_failed{error}`, `quota_queued{reset_ts}`, and
`channel_disconnected{channel_id, state}`. Together with the on-disk `publish_receipt`
(`approved_package_hash`) they form the tamper-evident record that no upload originated from the LLM
plane and that what shipped equals what was approved.

---

## 11. Build order — what's buildable NOW vs needs real Google creds

**Buildable & fully testable now (all behind fakes — no Google account):**
- **M0 — verification spike (do first).** Confirm the project's actual Console quota model (~6/day vs
  ~100/day), the current sensitive-scope verification requirement, and the exact scopes. Feeds `QUOTA`
  + the Setup guide copy. *(Reads docs/Console; no build dependency.)* **And — review finding C2 —
  M0's first action is to SUBMIT the sensitive-scope verification request**, because it has multi-day-
  to-week external latency and gates all of M5/M6. Start that clock on day 0, in parallel with M1–M4;
  do not wait until the code is done.
- **M1 — the `package` stage + contracts + Glint delegation.** Append the `package` stage; add
  `publish_package` / `publish_receipt` contracts; wire `glint_fn` (fake set in tests). Output reviewed
  in the existing modal.
- **M2 — the T3 gate + fire route (faked uploader).** `GATE_PUBLISH` (un-disableable) + `_publish_gate`
  + the special-cased `publish` step + `produce(uploader=…)` + `POST /api/publish/{slug}/fire` →
  `dispatcher.resume(slug,"publish",wait=True)` with a **fake uploader** returning a canned `video_id`.
  All negative-safety tests (§10) green here.
- **M3 — quota back-pressure + queue (E9).** Shared-ceiling display + refuse-and-queue + window drain.
- **M4 — connection-state machine UI + encrypted secret store + the Go-Live Setup guide (§7.2/7.3).**
  Real `secrets_store` (encryption real; tokens fake), the checklist + links + flag-driven
  checkpoints, reconnect CTAs. OAuth itself faked via `oauth_fn`.

**Shelled until the CEO has real Google credentials (behind the seam):**
- **M5 — live OAuth connect.** Replace `oauth_fn`'s fake with the real consent → refresh-token →
  `channels.list?mine=true` flow; store the encrypted token.
- **M6 — the real upload.** Replace the fake `uploader` with real `videos.insert` (+ thumbnail set +
  `publishAt`). **Private-first** (works the moment a channel connects), then **public/scheduled** only
  after the gauntlet (§9) is green. Verify against one real channel end-to-end.

Each milestone ships **tested** (unit + e2e with fakes), keeping the existing 353 unit + 39 e2e green
and adding Herald's own suites + negative-safety Playwright tests.

---

## 12. Edge cases & failure modes (Herald-specific; extends the master E-table)

| # | Scenario | Required behaviour |
|---|---|---|
| H1 (E8) | A scheduled time arrives but the package was never reviewed | Impossible: `GATE_PUBLISH` is un-disableable; the upload uses only an on-disk `approved_package` (§2.2). |
| H2 (E9) | Daily quota spent at fire time | Refuse the upload; write `queued_for_quota`; queue + drain at the next window; show the shared ceiling (§8). |
| H3 | Cloud project not verified | Public/scheduled blocked; offer **private-only**; link to Setup step 5; never silently downgrade (§9). |
| H4 | Channel not phone-verified | No custom thumbnail / no schedule; private-only; link to Setup step 6 (§9). |
| H5 | Refresh token expired / revoked / 7-day Testing expiry | Connection badge flips to `needs-reconnect`/`expired`/`revoked`; one-click reconnect; the fire route refuses until reconnected (§7.2). |
| H6 | Glint miss (no local focal / render fail) | Placeholder candidate + note; CEO may publish w/o custom thumbnail or hold; `package` never crashes (§5). |
| H7 | `videos.insert` fails mid-fire (network/5xx) | Receipt `status:"failed"` + error; **no quota double-spend assumed** (verify units actually consumed before re-charging); retryable from the modal; no partial public exposure. |
| H7b (E1) | A transient error / crash leaves the upload's success ambiguous, then a resume or retry fires | **No duplicate upload.** `publish` is never auto-retried; `_run_publish` re-entry first checks for an existing `publish_receipt` with a `video_id` (+ a best-effort channel lookup for the slug) and short-circuits if the video already exists. Exactly-once is enforced by the idempotency guard, not by luck (§2.3). |
| H8 (E2) | Two videos fire near-simultaneously | Reserve-then-spend under a lock: the second either reserves the remaining slot or queues; it can never both pass the check and exceed the ceiling. The `publish` station is also single-occupancy via the belt lock (§8). |
| H9 | Niche has no mapped channel | Modal blocks with "map a channel in Settings → Niches" (existing `publish.py` blocker). |
| H10 | CEO edits title/desc in the modal then fires | The edited values ARE the approved package; the receipt's `approved_package_hash` pins them; no later mutation (ToS). |
| H11 | Settings/secret store malformed or missing | Tolerant defaults (E13 precedent); a missing token = `disconnected`, not a crash; a pure engine never reads either globally. |

---

## 13. What's shelled / deferred

- **Live OAuth (M5) + the real `videos.insert` upload (M6)** — behind `oauth_fn` / `uploader`; need
  the CEO's real Google credentials. Until then: fakes in tests, the Setup guide tracks the gap.
- **Public/scheduled publishing** — blocked on the §9 gauntlet (project verification + phone
  verification); **private-only works first**.
- **Echo 📈 (#7) Analytics + the loop closure** — Herald only publishes; reading back real-world
  performance is #7.
- **Glint's real render** — its own spec (`2026-06-23-thumbnail-artist-Glint.md`); Herald consumes its
  candidate set via `glint_fn`.
- **Thumbnail eval-loop routing** (punch-text→Quill vs craft→Flux) — flagged, not built (§5).
- **A general dead-letter / retry subsystem** — reuse the existing parked-failure + `retry`/`rerun`
  machinery; no new subsystem.

---

## DECISIONS (resolved 2026-06-24) & remaining CEO action

The design questions are **settled** (CEO: "go with your picks"). Recorded here so the builder treats
them as locked, not open:

| # | Decision | Where in spec |
|---|---|---|
| 2 | **M0 verification spike runs FIRST** — confirm the real Console quota model (~6/day shared vs the migrating ~100/day bucket), the current sensitive-scope verification requirement, and the exact scopes, before any quota/upload code. | §8, §11 (M0) |
| 3 | **Persona = "Herald 📡"** (kept). | §1, §3 |
| 4 | **Default visibility = `private`**; public/scheduled only when the gauntlet is green. **Schedule uses the CEO's local timezone** (modal picker reads local; `publish_at` stored as absolute ISO-8601 + offset). | §7.1, §6.1, §9 |
| 5 | **Real PRIVATE uploads enabled early** — the moment one channel connects (pre-verification), Herald supports a real private/unlisted upload as an end-to-end smoke test (M6 is private-first); public/scheduled stays blocked on the gauntlet. | §11 (M6), §9 |
| 6 | **Encryption master key = a local `0600` keyfile** under `atlas/dashboard/` (auto-generated; env override honored). | §6.4 |
| 7 | **Thumbnail count = N=3** (kept). | §5 |
| 9 | **Auto-package by default, with a `package.eager_thumbnails` toggle** — `true` (default) prepares thumbnails for every rendered video; `false` defers the Glint run until the T3 modal is opened, so thumbnail cost is spent only on videos actually published. | §2.1 |

**Still requires CEO action (not a code decision — account work only the CEO can do):**

1. **The verification gauntlet — the #1 blocker.** Public/scheduled publishing is **impossible** until
   you (a) pass Google **sensitive-scope verification** on a Cloud project and (b) **phone-verify** each
   channel. **None of it exists yet.** No code can do this — it is Google-account paperwork. **The plan:
   build everything behind fakes (M1–M4) and ship the in-dashboard "Go-Live Setup" guide (§7.3) as the
   canonical checklist that walks you through it; you do the Google paperwork in parallel whenever.**
   Even once a channel connects, **only PRIVATE uploads work until verification is green.**
2. **Which channels & niches exist first (decision #8 — deferred, not blocking).** No specific channel
   named yet, so the build stays **flexible**: the channels bay + niche→channel map (already in
   `settings_store`) let you add channels/niches anytime, and the Setup guide renders a per-channel
   checklist for whatever you add. Tell me a first channel + niche mapping whenever you have one and the
   first real connect targets it.
