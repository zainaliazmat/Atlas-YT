# Herald — the Publisher (#6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Herald 📡 — a terminal `package` → T3 publish-gate → `publish` (upload) pair on the assembly line — so the Control Room can review and publish a finished video to YouTube, scheduled-after-approval, quota-bounded, verification-gated, with everything buildable-now behind fakes and live OAuth/upload shelled behind a seam.

**Architecture:** Additive. Two appended `STAGES` entries + one un-disableable T3 gate via the existing pause-and-resume machinery; one new sibling engine (`publisher/`) + one registry entry + one adapter; two new frozen contracts; a dashboard-owned encrypted secret store; new dashboard endpoints (fire / OAuth / setup) + frontend wiring. The real YouTube SDK lives only in the engine as a pure `upload(package, token)`, injected as a seam so tests never hit the network.

**Tech Stack:** Python 3.10+, FastAPI + uvicorn (dashboard), vanilla JS frontend, `jsonschema` (Draft 2020-12) contracts, `cryptography` (Fernet) for the secret store, `google-auth` + `google-api-python-client` for the real OAuth/upload (engine-only, shelled), pytest + Playwright.

**Spec:** `docs/superpowers/specs/2026-06-24-control-room-herald-publish.md` (read it first — this plan implements it section-by-section).

## Global Constraints

- **Decoupling (§11):** the `publisher/` engine NEVER imports `atlas`/`dashboard`; channel + token + approved package are passed in as args. Atlas stamps `schema_version` + validates at the adapter/pipeline seam.
- **Engines pure + injectable:** every external call (LLM, Glint, YouTube) is a seam passed in or a module-level function tests monkeypatch (mirror `adapters/sage.py:_factcheck_engine`).
- **No keys in tests:** `ANTHROPIC_API_KEY` and any `YOUTUBE_*`/OAuth secret are NEVER set in tests; e2e/unit inject fakes (`uploader`, `oauth_fn`, glint seam). Real OAuth/upload is unreachable under test.
- **Secrets never surface:** no token/client-secret in any HTTP response, log line, SSE event, or the UI. `get_token` is called only by the server-side uploader seam.
- **Status language (one set):** `queued / running / blocked / failed / done / cancelled`. Belt state is rebuildable from `projects/*/project.json`.
- **Additive only:** the existing 10 stages, their producers, and the two existing gates stay byte-for-byte unchanged. Spine touched ONLY by appending `STAGES` entries + one gate constant + opt-in params (the sanctioned seam, like `station_locks`).
- **Test runner:** from `atlas/`, `../venv/bin/python -m pytest …`. Restart the dashboard server after any backend change (no `--reload`). e2e navs use `wait_until="domcontentloaded"` + an explicit `wait_for_selector`.
- **T3 invariants (never weaken):** the publish gate is un-disableable; the upload runs only from the deterministic fire route, only with an on-disk approved package, used exactly (hash-pinned); there is NO publish/upload SDK tool; `publish` is never auto-retried; the upload is idempotent (no duplicate video).

## Canonical interfaces (used across tasks — keep names/types identical)

```text
# engine (publisher/publish_engine.py) — pure, no atlas import
assemble_package(artifacts: dict, *, niche: str, channel: dict | None,
                 defaults: dict) -> dict        # returns package dict (no schema_version)
upload(package: dict, *, token: str) -> dict    # returns receipt dict (no schema_version); REAL videos.insert (M6)

# adapter (atlas/adapters/herald.py)
produce_package(pdir, topic) -> stubs.Artifact          # pipeline producer for the `package` stage
_glint_candidates(pdir, package) -> dict                # module-level seam; tests monkeypatch; default = real Glint adapter
class HeraldAdapter(Adapter): run_job("package_video", **params) -> dict

# pipeline (atlas/pipeline.py)
GATE_PUBLISH = "publish"
_publish_gate(project, pdir, progress) -> dict | None
_run_publish(project, pdir, uploader, station_locks, progress, who, emoji) -> dict | None
produce(..., uploader: Callable | None = None)          # new opt-in kw
# uploader signature: uploader(package: dict, *, channel: dict, token: str) -> dict (receipt)

# dispatcher (atlas/dispatcher.py)
Dispatcher(..., uploader: Callable | None = None)       # stored, passed into produce()

# secret store (atlas/dashboard/secrets_store.py) — a class (paths injectable for tests)
class SecretStore(store_path, key_path):
    save_token(channel_id, refresh_token) / get_token(channel_id) -> str | None
    has_token(channel_id) -> bool / delete_token(channel_id)
    save_client(client_id, client_secret) / get_client() -> dict | None / has_client() -> bool
# app wires one instance: app.state.secrets = SecretStore(<dir>/herald_secrets.json, <dir>/.herald_secret.key)

# quota (atlas/dashboard/publish_quota.py)
reserve(path) -> bool        # atomic check-and-reserve under a lock; False if window full
commit(path) / release(path) # commit a reserved slot (success) / release it (failure)
spent_today(path) -> int / window_reset_iso(path) -> str

# dashboard endpoints (atlas/dashboard/app.py)
POST /api/publish/{slug}/fire          # T3 — the only upload origin
GET  /api/channels/{id}/connect        # OAuth start (state nonce)
GET  /api/oauth/callback               # OAuth exchange (validates state)
GET  /api/broadcast/setup              # Go-Live Setup checklist state
```

---

## Corrections (autoplan review, 2026-06-24) — apply these across the tasks below

A CEO/eng/design auto-review (3 independent Claude subagents; Codex outside-voice unavailable — sandbox
error) verified the plan's code against the real repo. The mechanical correctness fixes below are
**authoritative — apply them wherever the task code conflicts.** They were verified against the actual
files; the original task snippets predate them.

**Critical — the plan would not run as originally written:**
- **C1 — `registry.REGISTRY`, not `registry.ENTRIES`.** The entry list is `REGISTRY` (`registry.py:60`).
  Tasks 3 + 10 (tests) and Task 3 Step 4 ("append to the list") use `REGISTRY` (already corrected in the
  test blocks above; append the Herald `AgentEntry` to `REGISTRY`).
- **C2 — the `belt_server` fixture is e2e-only and yields a dict, not `(client, projects_dir)`.** It
  lives in `dashboard/tests/e2e/conftest.py` and yields `{"base_url","projects_dir"}` for a live uvicorn
  server (Playwright), wired to a fake that never runs publish. The **unit/API** tests in Tasks 4, 9, 10
  must instead use the existing **`client`** fixture (a real `TestClient` from `dashboard/tests/conftest.py`)
  and seed projects via `disposable_projects`/`slugs`. Define `belt_server_with_fake_uploader` (Task 9) as
  a new **TestClient** fixture in the **non-e2e** conftest that sets `app.state.uploader` to the fake and
  seeds a verified channel. Drop every `client, _ = belt_server` unpack.
- **C3 — `J(...)` is a closure inside `create_app`, not importable, and takes no `status_code`.**
  `approve_and_fire` (Task 9) must **return a plain dict / `JSONResponse`** (follow `publish.py`'s existing
  "return plain dict, route wraps" pattern); the route in `app.py` wraps it with the in-scope `J`. Do not
  call `J` from `publish.py`. For non-200s, return a `fastapi.responses.JSONResponse(content=…, status_code=…)`
  from the route, or have `approve_and_fire` return `{"_status": 409, ...}` and let the route translate.

**High:**
- **H1 — thread `uploader` into the cached dispatcher.** Task 8 Step 4 must edit the `Dispatcher(...)`
  construction inside `_get_dispatcher` (`app.py:~414`) to add `uploader=getattr(app.state,"uploader",None)`,
  AND `dispatcher._run`'s single `self._produce(...)` call (`dispatcher.py:~341`) must add `uploader=self._uploader`.
  Because `_get_dispatcher` caches the dispatcher (built on the first `/api/belt`), the **fire route must also
  refresh it**: `disp = _get_dispatcher(app); disp._uploader = getattr(app.state,"uploader",None)` before
  `disp.resume(...)`. Add a test that a `_get_dispatcher`-built dispatcher forwards `app.state.uploader` into `produce`.
- **H3 — gate/branch placement: the plan's Step-4 placement wins.** The `publish` special-case goes **inside**
  the `if st.get("status") != "done":` guard (Task 7 Step 4), because `_run_publish` itself handles the
  already-done/receipt-exists idempotency. The spec's "BEFORE the != done block" prose is wrong — update the
  spec §2.3 to match. The new publish-gate block (`if stage.key == "publish": _publish_gate(...)`) is a sibling
  of the existing `if stage.key == "render":` final-render-gate block, placed in the same pre-run gate region.

**Medium:**
- **M1 — `_glint_candidates` must pass `progress`.** `Adapter.run_job(self, job_name, progress, **params)`
  requires it positionally. Call `adapter.run_job("thumbnail_generate_candidates", None, slug=…)` (real adapters
  tolerate `progress=None`). Without this the real Glint path silently always degrades to the placeholder.
- **M3 — `window_reset_iso` must not hand-roll `mktime`.** Use
  `datetime.now(timezone.utc).date() + timedelta(days=1)` at `00:00:00Z` (month-boundary safe, UTC-consistent
  with `_today`'s `gmtime`).
- **M4 — Task 6's primary test uses `_publish_gate` directly.** `stubs.make_stub_producer` does not exist;
  the un-disableable-gate property must be proven by building a `project` dict (package `done`, gate pending)
  and asserting `_publish_gate(...)` returns a blocked result, and that an approved package returns `None`.
  Make that the PRIMARY test, not the fallback.
- **L3 (raised to apply) — stamp the routed channel into the approved package.** `assemble_package` is called
  with `channel=None` (Task 3), so `routing.channel_id` is `None` for belt packages. `approve_and_fire` (Task 9)
  must resolve the niche→channel route and set `approved["routing"]["channel_id"]` (+ `channel_title`) **before**
  `_persist_approved`, or the receipt + M6 token lookup get an empty channel.
- **CEO-F3 — single source for the quota ceiling.** `publish_quota.MAX_PER_DAY` must read
  `settings_store.QUOTA["max_uploads_per_day"]`, not a duplicated literal `6`. Gate M3's "~6/day" UI copy on
  M0's confirmed number (it may be ~100/day post-migration).

**Low / good news:**
- **L1 — chat/act safety test shape.** `/api/chat/act` reads `body.get("kind")` (top-level). Task 10's test
  must post `{"kind":"publish","args":{}}` (not nested under `action`) so it proves `"publish"` is rejected,
  not that `None` is.
- **E4 is mostly moot for the backend (verified).** `data.belt()` already builds `stations` by iterating
  `pipeline.STAGES` (`data.py:274`), so 10→12 auto-grows server-side. The ripple is **frontend-only**: Task 4
  Step 5 should `grep static/{app.js,index.html}` for `10`/`range(10)`/`slice(0,10)`/`length===10` and fix the
  per-video spine track + any "10 stages" copy. `STAGE_INPUTS` lacks `package`/`publish` keys but degrades to
  `[]` (acceptable; add them if the inspector should show inputs).

**Design — frontend tasks (15–17) need the additions below before M4 is "done":**
- **D-F3 (high, cheap, protects E1) — UI double-fire guard.** On Publish click, immediately disable the button
  + set an in-flight flag that blocks re-entry and dialog-close until the response resolves. e2e: a second click
  during flight issues no second POST. This is the UI-layer half of the exactly-once invariant.
- **D-F2 (high) — a result-state matrix for the fire** (the `publish_receipt.status` enum has five values):
  `firing` (spinner, modal locked), `uploaded`/`scheduled` (green confirm + resolved go-live + video link if
  public), `queued_for_quota` (neutral "queued, drains at <reset>", NOT an error), `failed`/`409` (re-enable +
  inline reason + allow re-fire). One e2e per non-trivial branch. The quota-queued path returns 200, so
  `approve_and_fire` must short-circuit it before the `!= "done"` → 502 check.
- **D-F4 (medium) — deterministic empty-state.** `publish_package(...)` returns a `setup_state:
  "unconfigured"|"configured"` (from `secrets_store.has_client()` + any connected channel), separate from
  per-video `blockers`. Task 17 branches on that flag (not blocker-count) so the calm "not set up yet → Open
  Setup guide" state never hides a real per-video blocker and never shows for a half-configured account.
- **D-F5 (medium) — schedule picker:** recompute the absolute-time echo from the offset **at the chosen
  instant** (DST-correct; add a cross-DST e2e); **disable** the picker with "available once this channel is
  verified" when the gauntlet isn't green; echo updates on change.
- **D-F6 (medium) — reconnect honesty + dedupe.** Pre-M5 the connection state is manually set (an editable
  `<select>` today), so the "proactive" banner has no real driver until M5's `token_expiry_hint`. State that in
  Task 15, and pick ONE primary surface for the needs-reconnect fact (banner) — don't show banner + tray-chip +
  editable select for the same state.
- **D-F7 (medium) — loading/error states** for `/api/publish/{slug}` (incl. a "preparing thumbnails…" spinner
  for the lazy-Glint render on first open), `/api/broadcast/setup`, and the channels refresh. Replace
  `openPublishModal`'s silent `catch { return; }` (which reads as a dead button) with a visible error + retry.
- **D-F1/F8 (medium) — bring Tasks 15–17 to the plan's standard:** each needs an explicit DOM skeleton, the
  exact copy (lift the strings the spec already wrote), the state list, and per-state e2e. Add a "design-review
  the T3 modal + Setup guide" checkpoint before M4 is called done.
- **D-F9/F10 (low) — a terminal confirmation line** above the fire button recomputing visibility + channel +
  resolved go-live (label the public button "Publish publicly now" / "Schedule public go-live"); treat unset
  COPPA as a first-class blocker row; sticky footer so the blocker summary + fire button stay in view.

**Future-seam note (CEO-F5, record only):** the un-disableable gate + no-publish-tool is correct now, but the
north-star is volume. Leave a documented, test-guarded extension point for a future "trusted private auto-publish"
tier so re-introducing it later isn't a from-scratch re-architecture of the safety-critical path.

---

## Milestone M0 — Verification spike (no app code; unblocks everything real)

### Task 0: Console verification spike + submit the verification request

**Files:**
- Create: `docs/superpowers/notes/2026-06-24-herald-m0-verification-spike.md`

**Interfaces:**
- Produces: confirmed values for `settings_store.QUOTA` (M3) + the Setup-guide copy (M4) + the exact OAuth scopes (M4/M5).

- [ ] **Step 1: Record the quota + verification facts.** In the notes file, capture from the Google Cloud Console / current YouTube Data API docs: (a) the project's actual `videos.insert` quota model — the legacy 10,000 units/day (~6 uploads) vs the migrated separate-bucket (~100/day) — and the real numbers shown in *your* project's Console; (b) the current sensitive-scope verification requirement; (c) the exact scope strings (`https://www.googleapis.com/auth/youtube.upload`, `…/youtube.force-ssl`, `…/yt-analytics.readonly`); (d) the redirect-URI you will register (`http://127.0.0.1:8848/api/oauth/callback`).
- [ ] **Step 2: Submit the sensitive-scope verification request (CEO action — start the clock).** This has multi-day-to-week latency and gates M5/M6 (spec C2). Record the submission date + status in the notes file.
- [ ] **Step 3: Commit the spike notes.**

```bash
git add docs/superpowers/notes/2026-06-24-herald-m0-verification-spike.md
git commit -m "docs(herald): M0 verification spike — quota + scopes + verification submitted"
```

> No automated test — this is a research/account task. Its output feeds the `QUOTA` constant (Task 11) and the Setup-guide copy (Task 16).

---

## Milestone M1 — `package` stage + contracts + Glint delegation (all behind fakes)

### Task 1: The two new frozen contracts (`publish_package`, `publish_receipt`)

**Files:**
- Create: `atlas/contracts/publish_package.schema.json`
- Create: `atlas/contracts/publish_receipt.schema.json`
- Modify: `atlas/contracts/__init__.py:73-85` (add to `SCHEMA_FILES`)
- Test: `atlas/tests/test_contracts_publish.py`

**Interfaces:**
- Produces: contract names `"publish_package"` and `"publish_receipt"` usable via `contracts.validate(name, obj)`.

- [ ] **Step 1: Write the failing test.**

```python
# atlas/tests/test_contracts_publish.py
import contracts


def _pkg(**over):
    base = {"schema_version": "1.0", "slug": "s", "title": "T", "description": "d",
            "tags": ["a"], "thumbnail": {"set_ref": "", "candidates": [],
            "selected_candidate_id": None}, "visibility": "private",
            "schedule": {"publish_at": None, "timezone": "UTC"},
            "routing": {"niche": "ai", "channel_id": None, "channel_title": None},
            "category_id": None, "made_for_kids": False}
    base.update(over)
    return base


def test_publish_package_valid():
    ok, errors = contracts.validate("publish_package", _pkg())
    assert ok, errors


def test_publish_package_rejects_bad_visibility():
    ok, _ = contracts.validate("publish_package", _pkg(visibility="semi-public"))
    assert not ok


def test_publish_receipt_valid():
    r = {"schema_version": "1.0", "slug": "s", "video_id": "abc", "channel_id": "UC1",
         "visibility": "private", "publish_at": None, "published_at": None,
         "status": "uploaded", "quota_units_spent": 1600, "approved_package_hash": "h",
         "error": None, "initiator": "ceo", "ts": 1.0}
    ok, errors = contracts.validate("publish_receipt", r)
    assert ok, errors


def test_publish_receipt_rejects_bad_status():
    ok, _ = contracts.validate("publish_receipt", {"schema_version": "1.0", "slug": "s",
        "channel_id": "UC1", "status": "yolo"})
    assert not ok
```

- [ ] **Step 2: Run it — expect failure.**

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_contracts_publish.py -q`
Expected: FAIL (`KeyError: 'No frozen contract named 'publish_package''`).

- [ ] **Step 3: Create `publish_package.schema.json`.**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "publish_package",
  "type": "object",
  "additionalProperties": true,
  "required": ["schema_version", "slug", "title", "visibility"],
  "properties": {
    "schema_version": {"type": "string"},
    "slug": {"type": "string"},
    "title": {"type": "string", "maxLength": 100},
    "description": {"type": "string"},
    "tags": {"type": "array", "items": {"type": "string"}},
    "thumbnail": {
      "type": "object", "additionalProperties": true,
      "properties": {
        "set_ref": {"type": "string"},
        "candidates": {"type": "array"},
        "selected_candidate_id": {"type": ["string", "null"]}
      }
    },
    "visibility": {"enum": ["private", "unlisted", "public"]},
    "schedule": {
      "type": "object", "additionalProperties": true,
      "properties": {
        "publish_at": {"type": ["string", "null"]},
        "timezone": {"type": "string"}
      }
    },
    "routing": {"type": "object", "additionalProperties": true},
    "category_id": {"type": ["string", "null"]},
    "made_for_kids": {"type": "boolean"}
  }
}
```

- [ ] **Step 4: Create `publish_receipt.schema.json`.**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "publish_receipt",
  "type": "object",
  "additionalProperties": true,
  "required": ["schema_version", "slug", "channel_id", "status"],
  "properties": {
    "schema_version": {"type": "string"},
    "slug": {"type": "string"},
    "video_id": {"type": ["string", "null"]},
    "channel_id": {"type": "string"},
    "visibility": {"enum": ["private", "unlisted", "public"]},
    "publish_at": {"type": ["string", "null"]},
    "published_at": {"type": ["string", "null"]},
    "status": {"enum": ["uploaded", "scheduled", "failed", "queued_for_quota"]},
    "quota_units_spent": {"type": "number"},
    "approved_package_hash": {"type": "string"},
    "error": {"type": ["string", "null"]},
    "initiator": {"type": "string"},
    "ts": {"type": "number"}
  }
}
```

- [ ] **Step 5: Wire both into `SCHEMA_FILES`.** In `atlas/contracts/__init__.py`, add to the `SCHEMA_FILES` dict (after `"reference_rubric"`):

```python
    "publish_package": "publish_package.schema.json",
    "publish_receipt": "publish_receipt.schema.json",
```

- [ ] **Step 6: Run the tests — expect pass.**

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_contracts_publish.py -q`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit.**

```bash
git add atlas/contracts/publish_package.schema.json atlas/contracts/publish_receipt.schema.json atlas/contracts/__init__.py atlas/tests/test_contracts_publish.py
git commit -m "feat(herald): add publish_package + publish_receipt contracts"
```

### Task 2: The `publisher/` engine — `assemble_package` (pure)

**Files:**
- Create: `publisher/publish_engine.py`
- Create: `publisher/SKILL.md` (one-paragraph job contract; copy the shape of an existing sibling's SKILL.md)
- Create: `publisher/soul/SOUL.md`, `publisher/soul/STYLE.md` (Herald's persona — distribution judgment; short, follow a sibling's soul shape)
- Test: `publisher/tests/test_publish_engine.py`

**Interfaces:**
- Produces: `assemble_package(artifacts, *, niche, channel, defaults) -> dict` (package dict WITHOUT `schema_version` — Atlas stamps that at the seam). Also `upload(package, *, token)` defined here but raising `NotImplementedError` until M6.

- [ ] **Step 1: Write the failing test.**

```python
# publisher/tests/test_publish_engine.py
import publish_engine as eng


def test_assemble_package_from_script():
    artifacts = {"script": {"working_title": "GPT-5 vs Claude", "hook": "Which wins?",
                            "keywords": ["ai", "llm", "ai"]}}
    pkg = eng.assemble_package(artifacts, niche="ai-tools", channel=None,
                               defaults={"visibility": "private"})
    assert pkg["title"] == "GPT-5 vs Claude"
    assert pkg["description"].startswith("Which wins?")
    assert "ai-tools" in pkg["tags"]
    assert "ai" in pkg["tags"] and pkg["tags"].count("ai") == 1   # de-duped
    assert pkg["visibility"] == "private"                          # safe default
    assert pkg["schedule"]["publish_at"] is None                  # never pre-set (E8)
    assert "schema_version" not in pkg                             # engine never stamps


def test_assemble_package_degrades_without_script():
    pkg = eng.assemble_package({}, niche="", channel=None, defaults={})
    assert pkg["title"]            # a non-empty fallback title, not a crash
    assert pkg["visibility"] == "private"
```

- [ ] **Step 2: Run it — expect failure.**

Run: `cd publisher && ../venv/bin/python -m pytest tests/test_publish_engine.py -q`
Expected: FAIL (`ModuleNotFoundError: publish_engine`).

- [ ] **Step 3: Implement `assemble_package` (+ an `upload` stub).**

```python
# publisher/publish_engine.py
"""Herald's engine — distribution judgment (packaging) + the real upload seam.

PURE + injectable: never imports atlas. assemble_package() turns a finished
project's artifacts into a YouTube publish package; upload() does the real
videos.insert (wired M6). Atlas stamps schema_version + validates at the seam.
"""
from __future__ import annotations

MAX_TAGS = 15
MAX_TITLE = 100


def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        k = (x or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(x.strip())
    return out


def assemble_package(artifacts: dict, *, niche: str, channel: dict | None,
                     defaults: dict) -> dict:
    """Build the publish package from project artifacts. No schema_version
    (Atlas stamps it). visibility defaults to the SAFE 'private'; schedule is
    always null here (set only AFTER approval — spec E8)."""
    script = (artifacts or {}).get("script", {}) or {}
    title = (script.get("working_title") or artifacts.get("topic")
             or "Untitled video")[:MAX_TITLE]
    description = (script.get("description") or script.get("hook") or "").strip()
    tags = _dedupe(([niche] if niche else []) + list(script.get("keywords")
                   or script.get("tags") or []))[:MAX_TAGS]
    return {
        "slug": artifacts.get("slug", ""),
        "title": title,
        "description": description,
        "tags": tags,
        "thumbnail": {"set_ref": "", "candidates": [], "selected_candidate_id": None},
        "visibility": defaults.get("visibility", "private"),
        "schedule": {"publish_at": None, "timezone": defaults.get("timezone", "UTC")},
        "routing": {"niche": niche,
                    "channel_id": (channel or {}).get("channel_id"),
                    "channel_title": (channel or {}).get("title")},
        "category_id": None,
        "made_for_kids": False,
    }


def upload(package: dict, *, token: str) -> dict:
    """REAL videos.insert — implemented in M6 (Task 18). Until then this seam
    is never reached in tests (a fake uploader is injected)."""
    raise NotImplementedError("Real upload lands in M6 (needs verified Google creds).")
```

- [ ] **Step 4: Run the tests — expect pass.**

Run: `cd publisher && ../venv/bin/python -m pytest tests/test_publish_engine.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Add the SKILL.md + soul stubs** (short; mirror an existing sibling, e.g. `editorial-coach/SKILL.md` + `soul/`). SOUL.md encodes high-retention titling + honest packaging; STYLE.md a crisp broadcaster voice.

- [ ] **Step 6: Commit.**

```bash
git add publisher/
git commit -m "feat(herald): publisher engine — assemble_package (pure) + upload seam stub + soul"
```

### Task 3: Registry entry + `HeraldAdapter`

**Files:**
- Modify: `atlas/registry.py` (add one `AgentEntry` after the coaches; import `HeraldAdapter`)
- Create: `atlas/adapters/herald.py`
- Test: `atlas/tests/test_herald_adapter.py`

**Interfaces:**
- Consumes: `publish_engine.assemble_package` (Task 2).
- Produces: registry entry `name="herald"`; `adapters.herald.produce_package(pdir, topic)`; `adapters.herald._glint_candidates(pdir, package)` (monkeypatchable seam); `HeraldAdapter.run_job("package_video", slug=…)`.

- [ ] **Step 1: Write the failing test.**

```python
# atlas/tests/test_herald_adapter.py
import json, pathlib
import registry
from adapters import herald


def test_herald_in_registry():
    e = registry.get_entry("herald")
    assert e and e.display == "Herald"
    assert [j.name for j in e.jobs] == ["package_video"]   # no publish/upload job


def test_no_publish_tool_generated():
    # negative safety: there is no upload/publish JobSpec anywhere in the registry
    tools = [j.tool for e in registry.REGISTRY for j in e.jobs]
    assert not any("publish" in t or "upload" in t for t in tools)


def test_produce_package_writes_validated_package(tmp_path, monkeypatch):
    pdir = tmp_path / "vid"
    pdir.mkdir()
    (pdir / "script.json").write_text(json.dumps(
        {"working_title": "T", "hook": "h", "keywords": ["ai"]}))
    (pdir / "project.json").write_text(json.dumps({"slug": "vid", "niche": "ai"}))
    monkeypatch.setattr(herald, "_glint_candidates",
                        lambda pdir, pkg: {"candidates": [{"candidate_id": "c1"}],
                                           "set_ref": "thumbnails/thumbnail_set.json"})
    art = herald.produce_package(pdir, "T")
    import contracts
    ok, errors = contracts.validate("publish_package", art.data)
    assert ok, errors
    assert art.data["thumbnail"]["candidates"][0]["candidate_id"] == "c1"
    assert (pdir / "publish_package.json").exists()
```

- [ ] **Step 2: Run it — expect failure.**

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_herald_adapter.py -q`
Expected: FAIL (`No entry named 'herald'` / `ModuleNotFoundError`).

- [ ] **Step 3: Implement `adapters/herald.py`.**

```python
# atlas/adapters/herald.py
"""Adapter for Herald (publisher) — packaging job + the package-stage producer.

Engine (publisher/publish_engine.py) is pure; Atlas stamps schema_version +
validates at the boundary here. The actual UPLOAD is NOT a job here — it is the
injected uploader seam reachable only from pipeline._run_publish / the dashboard
fire route (spec §3/§4). Glint delegation is a module-level seam tests monkeypatch.
"""
from __future__ import annotations

import pathlib

import chat_state
from adapters.base import Adapter
from adapters.loader import load_engine


def _engine():
    import registry  # lazy: avoid import cycle
    return load_engine(registry.get_entry("herald").project_dir, "publish_engine")


def _glint_candidates(pdir: pathlib.Path, package: dict) -> dict:
    """SEAM (tests monkeypatch): get 3 thumbnail candidates from Glint (#8).

    Default delegates to the Glint adapter via the registry. Degrades to a
    placeholder candidate + note on any miss (never crashes — fleet rule)."""
    try:
        import registry
        glint = registry.get_entry("glint")
        if glint is None:
            raise LookupError("Glint not registered yet")
        adapter = registry.build_adapters()[glint.name]
        out = adapter.run_job("thumbnail_generate_candidates",
                              slug=package.get("slug", ""))
        return out.get("thumbnail_set") or out
    except Exception as exc:  # noqa: BLE001 — graceful degrade
        return {"set_ref": "", "candidates": [
            {"candidate_id": "placeholder", "note": f"Glint unavailable: {exc}"}]}


def produce_package(pdir: pathlib.Path, topic: str):
    """REAL `package` stage producer: assemble the publish package + Glint set,
    stamp schema_version, write publish_package.json. The pipeline validates it."""
    from contracts import CONTRACT_VERSION
    from adapters.stubs import Artifact
    pdir = pathlib.Path(pdir)
    proj = chat_state.load_json(pdir / "project.json", {})
    script = chat_state.load_json(pdir / "script.json", {})
    niche = (proj.get("config", {}) or {}).get("niche") or proj.get("niche") or ""
    artifacts = {"script": script, "slug": proj.get("slug") or pdir.name, "topic": topic}
    pkg = _engine().assemble_package(artifacts, niche=niche, channel=None,
                                     defaults={"visibility": "private"})
    pkg["thumbnail"] = _glint_candidates(pdir, pkg)
    pkg = {"schema_version": CONTRACT_VERSION, **pkg}
    chat_state.atomic_write_json(pdir / "publish_package.json", pkg)
    n = len(pkg["thumbnail"].get("candidates") or [])
    return Artifact("publish_package.json", "publish_package", pkg,
                    f"packaged '{pkg['title']}' · {n} thumbnail candidate(s)")


class HeraldAdapter(Adapter):
    module_name = "publish_engine"

    def run_job(self, job_name: str, progress, **params) -> dict:
        if job_name != "package_video":
            return {"ok": False, "text": f"Herald has no job named {job_name!r}."}
        slug = (params.get("slug") or "").strip()
        import pipeline
        pdir = pipeline.PROJECTS_DIR / slug
        if not (pdir / "project.json").exists():
            return {"ok": False, "text": f"No project {slug!r} to package."}
        art = produce_package(pdir, params.get("topic") or "")
        return {"ok": True, "text": art.summary, "saved": str(pdir / "publish_package.json")}
```

- [ ] **Step 4: Add the registry entry.** In `atlas/registry.py`, import the adapter near the other adapter imports, then append to the `ENTRIES` list (after `production_coach`):

```python
    AgentEntry(
        name="herald",
        display="Herald",
        emoji="📡",
        blurb="Packages a finished video (title/description/tags + thumbnails) and publishes it to the niche's YouTube channel after your sign-off. Scheduled-after-approval, quota-bounded.",
        project_dir=str(_ROOT / "publisher"),
        adapter_cls=HeraldAdapter,
        role="Publisher",
        jobs=[JobSpec(
            name="package_video",
            tool="herald_package_video",
            description=("Assemble the YouTube publish package (title, description, "
                         "tags) for a finished video and request 3 thumbnail "
                         "candidates from Glint. Pass 'slug'. Returns a package "
                         "digest. Does NOT publish — publishing is a human T3 "
                         "action on the deterministic UI."),
            params={"slug": str},
            timeout=300,
        )],
    ),
```

- [ ] **Step 5: Run the tests — expect pass.**

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_herald_adapter.py -q`
Expected: PASS (3 tests). The `_glint_candidates` default path is exercised later; here it's monkeypatched.

- [ ] **Step 6: Commit.**

```bash
git add atlas/registry.py atlas/adapters/herald.py atlas/tests/test_herald_adapter.py
git commit -m "feat(herald): registry entry + HeraldAdapter (package_video; no upload tool)"
```

### Task 4: Append the `package` stage to the spine (auto, after render)

**Files:**
- Modify: `atlas/pipeline.py:61-101` (append `package` to `STAGES`; import `herald`)
- Modify: `atlas/pipeline.py:139-145` (`_new_project` — add the publish gate placeholder, prep for Task 5)
- Test: `atlas/tests/test_pipeline_package.py`

**Interfaces:**
- Consumes: `adapters.herald.produce_package` (Task 3).
- Produces: a `package` stage that runs after `render` and writes a validated `publish_package.json`.

- [ ] **Step 1: Write the failing test.**

```python
# atlas/tests/test_pipeline_package.py
import json, pathlib
import pipeline


def test_package_stage_is_appended_after_render():
    keys = [s.key for s in pipeline.STAGES]
    assert keys[-1] == "package"          # publish appended in M2; package is last for now
    assert keys.index("render") < keys.index("package")


def test_package_stage_role_is_herald():
    pkg = next(s for s in pipeline.STAGES if s.key == "package")
    assert pkg.role == "herald" and pkg.contract == "publish_package"
```

- [ ] **Step 2: Run it — expect failure.**

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_pipeline_package.py -q`
Expected: FAIL (`'package'` not in keys).

- [ ] **Step 3: Append the stage.** In `atlas/pipeline.py`, add `herald` to the existing adapters import (lines 38-39: `from adapters import (art_director, asset_sourcer, audio, composition_engineer, herald, sage, scriptwriter)`), then append to `STAGES` (after the `render` Stage):

```python
    # REAL package stage: Herald assembles the publish package (title/desc/tags)
    # and delegates to Glint for thumbnail candidates. Auto, reversible, no
    # external effect — the line ALWAYS parks at the publish gate next (M2).
    Stage("package", "herald", "packaging for publish", herald.produce_package,
          "publish_package"),
```

- [ ] **Step 4: Run the tests — expect pass.**

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_pipeline_package.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Update the belt view for the new station count (review finding E4 — 10 → 12).** `STAGES` now has 11 entries (12 after Task 7 adds `publish`). Anything that hard-codes 10 must follow. Add a test + fix:

```python
# atlas/dashboard/tests/test_belt_api.py (addition)
def test_belt_has_all_stages(belt_server):
    client, _ = belt_server
    stations = client.get("/api/belt").json()["stations"]
    assert len(stations) == len([s for s in __import__("pipeline").STAGES])  # not hard-coded 10
    assert stations[-1]["key"] in ("package", "publish")
```

In `atlas/dashboard/data.py` `belt()`, confirm `stations` is built by iterating `STAGES` (not a literal `[...10...]`); if any `[:10]` / `range(10)` / `"10 stations"` copy exists in `data.py` or `static/{app.js,index.html}`, replace with the live `STAGES` length. The frontend per-video spine track must render `len(stations)` segments, not a fixed 10.

- [ ] **Step 6: Run the full pipeline + belt suite — confirm no regression.**

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_pipeline*.py tests/test_dispatcher.py dashboard/tests/test_belt_api.py -q`
Expected: PASS (existing stages unchanged; a fresh produce run now also runs `package`; the belt shows the real station count).

- [ ] **Step 7: Commit.**

```bash
git add atlas/pipeline.py atlas/dashboard/data.py atlas/dashboard/static/ atlas/tests/test_pipeline_package.py atlas/dashboard/tests/test_belt_api.py
git commit -m "feat(herald): append the package stage to the spine; belt renders live station count (E4)"
```

### Task 5: Eager/lazy thumbnail toggle (`package.eager_thumbnails`)

**Files:**
- Modify: `atlas/dashboard/settings_store.py:48-55` (add `package` defaults block) + `validate_settings`
- Modify: `atlas/adapters/herald.py` (`produce_package` — skip Glint when lazy; the modal triggers it later)
- Test: `atlas/dashboard/tests/test_settings_api.py` (extend) + `atlas/tests/test_herald_adapter.py` (extend)

**Interfaces:**
- Produces: settings `package.eager_thumbnails: bool` (default `True`); `produce_package` honors it.

- [ ] **Step 1: Write the failing test** (add to `test_herald_adapter.py`):

```python
def test_produce_package_lazy_skips_glint(tmp_path, monkeypatch):
    pdir = tmp_path / "vid"; pdir.mkdir()
    (pdir / "script.json").write_text('{"working_title":"T"}')
    (pdir / "project.json").write_text('{"slug":"vid","config":{"package":{"eager_thumbnails":false}}}')
    called = {"glint": False}
    def _spy(pdir, pkg): called["glint"] = True; return {}
    monkeypatch.setattr(herald, "_glint_candidates", _spy)
    art = herald.produce_package(pdir, "T")
    assert called["glint"] is False
    assert art.data["thumbnail"]["candidates"] == []        # deferred to the modal
```

- [ ] **Step 2: Run it — expect failure** (Glint is called unconditionally today).

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_herald_adapter.py::test_produce_package_lazy_skips_glint -q`
Expected: FAIL.

- [ ] **Step 3: Honor the toggle in `produce_package`.** Replace the `pkg["thumbnail"] = _glint_candidates(...)` line with:

```python
    eager = (((proj.get("config", {}) or {}).get("package", {}) or {})
             .get("eager_thumbnails", True))
    if eager:
        pkg["thumbnail"] = _glint_candidates(pdir, pkg)
```

- [ ] **Step 4: Add the settings default.** In `settings_store.DEFAULT_SETTINGS`, add `"package": {"eager_thumbnails": True}`; in `validate_settings`, coerce it (default `True` on bad input); surface it in `public_settings`.

- [ ] **Step 5: Run the tests — expect pass.**

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_herald_adapter.py dashboard/tests/test_settings_api.py -q`
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add atlas/adapters/herald.py atlas/dashboard/settings_store.py atlas/tests/test_herald_adapter.py atlas/dashboard/tests/test_settings_api.py
git commit -m "feat(herald): package.eager_thumbnails toggle (lazy defers Glint to the modal)"
```

---

## Milestone M2 — T3 publish gate + fire route (fake uploader)

### Task 6: The un-disableable publish gate (`GATE_PUBLISH` + `_publish_gate`)

**Files:**
- Modify: `atlas/pipeline.py` (add `GATE_PUBLISH`; add it to `_new_project` gates; add `_publish_gate`; check it in the `produce()` loop before the `publish` stage)
- Test: `atlas/tests/test_publish_gate.py`

**Interfaces:**
- Produces: `pipeline.GATE_PUBLISH = "publish"`; `_publish_gate(project, pdir, progress) -> dict | None`; a project that always parks at `blocked_at_publish` after `package` unless an approved package is on disk.

- [ ] **Step 1: Write the failing test.**

```python
# atlas/tests/test_publish_gate.py
import pipeline


def test_unattended_run_parks_at_publish_never_fires(tmp_path, monkeypatch):
    # Fake every producer so the spine runs end-to-end offline; unattended=True
    # disables the two normal gates but MUST still park at publish.
    monkeypatch.setattr(pipeline, "PROJECTS_DIR", tmp_path)
    from adapters import stubs
    for s in pipeline.STAGES:
        if s.key != "publish":
            monkeypatch.setattr(s, "producer", stubs.make_stub_producer(s)
                                if hasattr(stubs, "make_stub_producer") else s.producer)
    res = pipeline.produce(brief="t", unattended=True, root=tmp_path)
    assert res["status"] == "blocked"
    assert res["gate"] == "publish"      # parked at publish despite unattended


def test_publish_gate_not_in_default_toggle():
    assert pipeline.GATE_PUBLISH not in pipeline.DEFAULT_GATES
```

> If `stubs.make_stub_producer` does not exist, the test instead asserts the gate logic directly: build a `project` dict with `package` done and call `pipeline._publish_gate(project, pdir, Progress())`; assert it returns a blocked result and that approving (setting `gates[publish] = {"status":"approved","approved_package":{...}}`) returns `None`. Use whichever matches the codebase; keep the un-disableable assertion.

- [ ] **Step 2: Run it — expect failure.**

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_publish_gate.py -q`
Expected: FAIL (`AttributeError: GATE_PUBLISH`).

- [ ] **Step 3: Add the gate constant + project placeholder.** In `atlas/pipeline.py` near `GATE_FINAL_RENDER`:

```python
GATE_PUBLISH = "publish"   # T3 — un-disableable; NOT in DEFAULT_GATES (spec E8)
```

In `_new_project`, add to the `gates` dict:

```python
                  GATE_PUBLISH: {"status": "pending", "details": None},
```

- [ ] **Step 4: Add `_publish_gate` (un-disableable).** Add near `_final_render_gate`:

```python
def _publish_gate(project: dict, pdir: pathlib.Path, progress: Progress) -> dict | None:
    """T3 publish checkpoint — ALWAYS parks unless an approved package is on disk.

    Unlike the other gates it ignores cfg_gates: unattended / gates=False can
    NEVER disable it (spec E8 — no auto-fire-unreviewed). The only way past is an
    approved package set by the deterministic fire route (dashboard)."""
    g = project["gates"].get(GATE_PUBLISH, {})
    if g.get("status") == "approved" and g.get("approved_package"):
        return None
    details = chat_state.load_json(pdir / "publish_package.json", {})
    project["gates"][GATE_PUBLISH] = {"status": "blocked", "details": details}
    project["status"] = f"blocked_at_{GATE_PUBLISH}"
    _log(project, GATE_PUBLISH, "awaiting CEO publish sign-off (T3)")
    _save(project, pdir)
    progress.emit("⏸️  Packaged — awaiting your publish sign-off (T3). Nothing "
                  "uploads until you approve the exact package.")
    return _result(project, pdir, status="blocked", gate=GATE_PUBLISH,
                   reason="Awaiting CEO sign-off before publishing.", details=details)
```

- [ ] **Step 5: Check the gate in the loop.** In `produce()`, inside the `for stage in STAGES:` loop, add a check before the `publish` stage runs (mirror the render-gate block):

```python
        if stage.key == "publish":
            blocked = _publish_gate(project, pdir, progress)
            if blocked is not None:
                return blocked
```

(Place it alongside the existing `if stage.key == "render":` final-render-gate block.)

- [ ] **Step 6: Run the tests — expect pass.**

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_publish_gate.py -q`
Expected: PASS.

- [ ] **Step 7: Commit.**

```bash
git add atlas/pipeline.py atlas/tests/test_publish_gate.py
git commit -m "feat(herald): un-disableable T3 publish gate (parks; never auto-fires — E8)"
```

### Task 7: The `publish` stage + `_run_publish` (idempotent, special-cased)

**Files:**
- Modify: `atlas/pipeline.py` (append the `publish` Stage with a sentinel producer; add `produce(uploader=…)`; special-case `_run_publish`)
- Test: `atlas/tests/test_run_publish.py`

**Interfaces:**
- Consumes: `GATE_PUBLISH`, `_publish_gate` (Task 6).
- Produces: `_run_publish(project, pdir, uploader, station_locks, progress, who, emoji)`; `produce(uploader=…)`; the exactly-once idempotency guard (E1).

- [ ] **Step 1: Write the failing test.**

```python
# atlas/tests/test_run_publish.py
import json, pathlib, time
import pipeline
from progress import Progress


def _approved_project(tmp_path):
    pdir = tmp_path / "vid"; pdir.mkdir()
    pkg = {"schema_version": "1.0", "slug": "vid", "title": "T", "visibility": "private",
           "routing": {"channel_id": "UC1"}}
    proj = {"slug": "vid", "stages": {s.key: {"status": "done"} for s in pipeline.STAGES},
            "gates": {pipeline.GATE_PUBLISH: {"status": "approved", "approved_package": pkg}},
            "history": [], "artifacts": {}, "config": {"gates": {}}}
    proj["stages"]["publish"] = {"status": "pending"}
    (pdir / "publish_package.json").write_text(json.dumps(pkg))
    (pdir / "project.json").write_text(json.dumps(proj))
    return pdir, proj, pkg


def test_run_publish_uploads_once(tmp_path):
    pdir, proj, pkg = _approved_project(tmp_path)
    calls = []
    def fake_uploader(package, *, channel, token):
        calls.append(package["title"])
        return {"video_id": "vid123", "status": "uploaded", "quota_units_spent": 1600}
    res = pipeline.produce(slug="vid", root=tmp_path, uploader=fake_uploader,
                           progress=Progress())
    assert res["status"] == "done"
    assert calls == ["T"]
    rec = json.loads((pdir / "publish_receipt.json").read_text())
    assert rec["video_id"] == "vid123"


def test_run_publish_idempotent_no_double_upload(tmp_path):
    pdir, proj, pkg = _approved_project(tmp_path)
    # a receipt with a video_id already exists → re-entry must NOT upload again
    (pdir / "publish_receipt.json").write_text(json.dumps(
        {"schema_version": "1.0", "slug": "vid", "channel_id": "UC1",
         "status": "uploaded", "video_id": "already"}))
    proj["stages"]["publish"] = {"status": "pending"}
    (pdir / "project.json").write_text(json.dumps(proj))
    calls = []
    def fake_uploader(package, *, channel, token):
        calls.append(1); return {"video_id": "SECOND", "status": "uploaded"}
    res = pipeline.produce(slug="vid", root=tmp_path, uploader=fake_uploader,
                           progress=Progress())
    assert calls == []                      # E1: no second upload
    assert res["status"] == "done"


def test_no_uploader_parks_forever(tmp_path):
    pdir, proj, pkg = _approved_project(tmp_path)
    # approved but no uploader injected → cannot publish; must not crash on None()
    res = pipeline.produce(slug="vid", root=tmp_path, uploader=None, progress=Progress())
    assert res["status"] in ("blocked", "failed")    # never calls None(...)
    assert not (pdir / "publish_receipt.json").exists()
```

- [ ] **Step 2: Run it — expect failure.**

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_run_publish.py -q`
Expected: FAIL (`produce() got an unexpected keyword 'uploader'`).

- [ ] **Step 3: Append the `publish` Stage with a sentinel producer.** In `STAGES`, after `package`:

```python
    # Terminal publish stage: the UPLOAD. NOT a generic producer — produce()
    # special-cases stage.key == "publish" and calls _run_publish with the
    # injected uploader seam. The sentinel producer must never be called.
    Stage("publish", "herald", "publishing to YouTube", None, "publish_receipt"),
```

> `Stage.producer` is typed `Callable`; set it to `None` and rely on the special-case. (If the dataclass/type checker complains, set `producer=_publish_sentinel` where `def _publish_sentinel(*a, **k): raise RuntimeError("publish is special-cased")`.)

- [ ] **Step 4: Add the `uploader` param + special-case in `produce()`.** Change the signature to add `uploader: Callable | None = None`. In the loop, replace the generic run block so `publish` is handled specially:

```python
        if st.get("status") != "done":
            if stage.key == "publish":
                failed = _run_publish(project, pdir, uploader, station_locks,
                                      progress, who, emoji)
            else:
                with _station(station_locks, stage.key):
                    failed = _run_stage(stage, st, project, pdir, topic, progress, who, emoji)
            if failed is not None:
                return failed
```

- [ ] **Step 5: Implement `_run_publish` (idempotent; never auto-retried).**

```python
def _run_publish(project, pdir, uploader, station_locks, progress, who, emoji):
    """Run the upload via the injected uploader seam — exactly once.

    E1 idempotency: if a receipt with a video_id already exists, short-circuit
    (mark done, NEVER re-upload). No uploader → cannot publish (park as failed
    with a clear reason; the None sentinel producer is never called)."""
    st = project["stages"]["publish"]
    receipt = chat_state.load_json(pdir / "publish_receipt.json", None)
    if isinstance(receipt, dict) and receipt.get("video_id"):
        st["status"] = "done"; st["artifact"] = "publish_receipt.json"
        _save(project, pdir)
        return None
    if uploader is None:
        st["status"] = "failed"
        project["status"] = "failed"
        _save(project, pdir)
        return _result(project, pdir, status="failed", stage="publish",
                       errors=["No uploader wired — publishing is unavailable."],
                       failure_kind="deterministic")   # deterministic → never auto-retried
    pkg = project["gates"][GATE_PUBLISH]["approved_package"]
    channel = pkg.get("routing", {}) or {}
    with _station(station_locks, "publish"):
        progress.emit(f"{emoji} {who} is publishing to YouTube…")
        try:
            out = uploader(pkg, channel=channel, token="__injected__")
        except Exception as exc:  # noqa: BLE001
            st["status"] = "failed"; project["status"] = "failed"; _save(project, pdir)
            return _result(project, pdir, status="failed", stage="publish",
                           errors=[str(exc)], failure_kind="deterministic")  # never retry
    from contracts import CONTRACT_VERSION
    import hashlib, json as _json
    receipt = {"schema_version": CONTRACT_VERSION, "slug": project["slug"],
               "channel_id": channel.get("channel_id") or "", "initiator": "ceo",
               "approved_package_hash": hashlib.sha256(
                   _json.dumps(pkg, sort_keys=True).encode()).hexdigest(),
               "ts": time.time(), **out}
    ok, errors = contracts.validate("publish_receipt", receipt)
    if not ok:
        st["status"] = "failed"; project["status"] = "failed"; _save(project, pdir)
        return _result(project, pdir, status="failed", stage="publish",
                       errors=errors, failure_kind="deterministic")
    chat_state.atomic_write_json(pdir / "publish_receipt.json", receipt)
    st["status"] = "done"; st["artifact"] = "publish_receipt.json"
    project["artifacts"]["publish"] = "publish_receipt.json"
    _save(project, pdir)
    return None
```

- [ ] **Step 6: Run the tests — expect pass.**

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_run_publish.py -q`
Expected: PASS (3 tests).

- [ ] **Step 7: Run the full spine + dispatcher suites — no regression.**

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_pipeline*.py tests/test_publish_gate.py tests/test_run_publish.py tests/test_dispatcher.py -q`
Expected: PASS.

- [ ] **Step 8: Commit.**

```bash
git add atlas/pipeline.py atlas/tests/test_run_publish.py
git commit -m "feat(herald): publish stage + _run_publish (exactly-once idempotent upload — E1)"
```

### Task 8: Dispatcher — thread the uploader + never auto-retry publish

**Files:**
- Modify: `atlas/dispatcher.py` (`__init__` gains `uploader`; pass into `produce()`; `_on_result` never retries a `publish` failure)
- Modify: `atlas/dashboard/app.py:38-44` (`app.state.uploader = None`) + `_get_dispatcher` (pass it)
- Test: `atlas/tests/test_dispatcher.py` (extend)

**Interfaces:**
- Consumes: `produce(uploader=…)` (Task 7).
- Produces: `Dispatcher(uploader=…)`; `resume(slug, "publish", …)` runs the upload; a failed publish never auto-retries.

- [ ] **Step 1: Write the failing test** (add to `test_dispatcher.py`):

```python
def test_dispatcher_passes_uploader_and_never_retries_publish(tmp_path):
    import dispatcher as dmod
    seen = {}
    def fake_produce(**kw):
        seen["uploader"] = kw.get("uploader")
        return {"status": "failed", "stage": "publish", "failure_kind": "deterministic",
                "errors": ["boom"]}
    up = lambda pkg, **k: {"video_id": "x"}
    d = dmod.Dispatcher(projects_dir=tmp_path, produce_fn=fake_produce, uploader=up,
                        max_retries=3)
    d._on_result("vid", {"status": "failed", "stage": "publish",
                         "failure_kind": "deterministic", "errors": ["boom"]})
    # publish failure must NOT schedule a retry even with budget
    assert d._retries.get("vid", 0) == 0
```

- [ ] **Step 2: Run it — expect failure.**

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_dispatcher.py::test_dispatcher_passes_uploader_and_never_retries_publish -q`
Expected: FAIL (`Dispatcher() got an unexpected keyword 'uploader'`).

- [ ] **Step 3: Add `uploader` to the dispatcher.** In `Dispatcher.__init__`, add `uploader: Callable | None = None` and `self._uploader = uploader`. In `_run`, add `uploader=self._uploader` to the `self._produce(...)` call. In `_on_result`, guard the retry branch so a `publish` stage never auto-retries:

```python
        if status == "failed":
            kind = result.get("failure_kind", "transient")
            if result.get("stage") == "publish":
                kind = "deterministic"   # E1: publishing is never auto-retried
            attempts = self._retries.get(slug, 0)
            if kind == "transient" and attempts < self.max_retries:
                ...
```

- [ ] **Step 4: Wire `app.state.uploader`.** In `app.py` add `app.state.uploader = None` near the other seams, and in `_get_dispatcher` pass `uploader=getattr(app.state, "uploader", None)`.

- [ ] **Step 5: Run the tests — expect pass.**

Run: `cd atlas && ../venv/bin/python -m pytest tests/test_dispatcher.py -q`
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add atlas/dispatcher.py atlas/dashboard/app.py atlas/tests/test_dispatcher.py
git commit -m "feat(herald): dispatcher threads uploader; publish never auto-retries"
```

### Task 9: The fire route (`POST /api/publish/{slug}/fire`) + package thumbnails

**Files:**
- Modify: `atlas/dashboard/publish.py` (surface thumbnail candidates + an `approve_and_fire` helper that persists the approved package)
- Modify: `atlas/dashboard/app.py` (add the `POST /api/publish/{slug}/fire` route)
- Test: `atlas/dashboard/tests/test_publish_api.py` (extend)

**Interfaces:**
- Consumes: `dispatcher.resume(slug, "publish", wait=True)` (existing) now runs the upload (Task 7/8).
- Produces: `POST /api/publish/{slug}/fire` returning the receipt or a 409 with a reason.

- [ ] **Step 1: Write the failing test** (add to `test_publish_api.py`):

```python
def test_fire_refuses_when_blocked(belt_server):
    client, projects_dir = belt_server
    # a project that is not render-ready / no channel → blockers present
    slug = _seed_done_project(projects_dir, verified=False)   # helper in the test module
    r = client.post(f"/api/publish/{slug}/fire", json={"title": "T", "visibility": "private"})
    assert r.status_code == 409
    assert "blocker" in r.json()["error"].lower() or r.json().get("blockers")


def test_fire_uploads_when_clear(belt_server_with_fake_uploader):
    client, projects_dir = belt_server_with_fake_uploader
    slug = _seed_done_project(projects_dir, verified=True)
    r = client.post(f"/api/publish/{slug}/fire",
                    json={"title": "T", "description": "d", "tags": ["ai"],
                          "selected_candidate_id": "c1", "visibility": "private",
                          "schedule": None, "made_for_kids": False})
    assert r.status_code == 200
    assert r.json()["status"] in ("uploaded", "scheduled")
    assert r.json().get("video_id")
```

> Add a `belt_server_with_fake_uploader` fixture in `conftest.py` that sets `app.state.uploader = lambda pkg, **k: {"video_id": "vidX", "status": "uploaded", "quota_units_spent": 1600}` and seeds a verified channel in settings. `_seed_done_project` writes a `done` project + `video.mp4` + `publish_package.json`.

- [ ] **Step 2: Run it — expect failure.**

Run: `cd atlas && ../venv/bin/python -m pytest dashboard/tests/test_publish_api.py -q`
Expected: FAIL (route 405 / missing).

- [ ] **Step 3: Add the fire route in `app.py`.**

```python
    @app.post("/api/publish/{slug}/fire")
    async def publish_fire(slug: str, request: Request):
        body = await _json_body(request)
        return publish.approve_and_fire(app, slug, body)
```

- [ ] **Step 4: Implement `publish.approve_and_fire`.** In `publish.py`:

```python
def approve_and_fire(app, slug: str, body: dict):
    """T3 — the ONLY upload origin. Re-validate blockers + quota, persist the
    EXACT approved package, then resume the publish stage through the belt."""
    projects_dir = app.state.projects_dir
    pkg = publish_package(projects_dir, slug, app.state.settings_path)
    if pkg is None:
        return J({"error": "no such project"}, status_code=404)
    if not pkg["would_publish"]:
        return J({"error": "publish is blocked", "blockers": pkg["blockers"]},
                 status_code=409)
    # build the approved package from the reviewed body, bounded to the shell shape
    approved = dict(pkg["package"])
    for k in ("title", "description", "tags", "visibility", "made_for_kids"):
        if k in body:
            approved[k] = body[k]
    approved["thumbnail"] = {**approved.get("thumbnail", {}),
                             "selected_candidate_id": body.get("selected_candidate_id")}
    approved["schedule"] = {"publish_at": (body.get("schedule") or {}).get("publish_at"),
                            "timezone": (body.get("schedule") or {}).get("timezone", "UTC")}
    # quota reserve happens in Task 11; here assume a slot
    _persist_approved(projects_dir, slug, approved)         # writes gates[publish].approved_package
    out = _get_dispatcher(app).resume(slug, "publish", initiator="ceo", wait=True) or {}
    if out.get("status") != "done":
        return J({"result": "failed", "slug": slug, **out}, status_code=502)
    receipt = data.read_json(projects_dir / slug / "publish_receipt.json", {})
    return J({"result": "published", "slug": slug, **receipt})
```

> `_persist_approved` loads `project.json`, sets `gates[publish] = {"status": "approved", "approved_package": approved, "approved_ts": time.time()}` + an audited history entry, and atomically writes it. `J` / `_get_dispatcher` / `data` are imported in `app.py`; move the helper there if cleaner, or pass `_get_dispatcher(app)` in.

- [ ] **Step 5: Surface thumbnails in `publish_package(...)`.** Read `publish_package.json`'s `thumbnail.candidates` (written by the package stage) into the returned `package["thumbnail"]`, so the modal can render the 3 candidates. If lazy mode left it empty, trigger `herald.produce_package` once on first modal open (a `?withthumbs=1` query or a small `POST /api/publish/{slug}/package` — keep it T1).

- [ ] **Step 6: Emit the audit events (review finding E8).** `approve_and_fire` records the named events on the dispatcher's `EventRing` (with `initiator="ceo"`) so the E8/ToS trail is first-class: `events.emit("publish_fired", slug=slug, initiator="ceo")` before the resume; on the outcome, `events.emit("publish_succeeded", slug=slug, video_id=…, visibility=…, publish_at=…)` or `events.emit("publish_failed", slug=slug, message=…)`; the quota path (Task 11) emits `events.emit("quota_queued", slug=slug, reset_ts=…)`. The package stage (Task 4) emits `package_ready`; the connection state machine (Task 15) emits `channel_disconnected`. Add a test asserting a `publish_fired` then `publish_succeeded` appears on `/api/events` after a clean fire.

- [ ] **Step 7: Run the tests — expect pass.**

Run: `cd atlas && ../venv/bin/python -m pytest dashboard/tests/test_publish_api.py -q`
Expected: PASS.

- [ ] **Step 8: Commit.**

```bash
git add atlas/dashboard/publish.py atlas/dashboard/app.py atlas/dashboard/tests/test_publish_api.py atlas/dashboard/tests/conftest.py
git commit -m "feat(herald): T3 fire route — approve_and_fire persists exact package + resumes upload"
```

### Task 10: Negative-safety tests (no T3 backdoor)

**Files:**
- Test: `atlas/dashboard/tests/test_security.py` (extend) + `atlas/dashboard/tests/e2e/test_chat_e2e.py` (extend)

**Interfaces:**
- Asserts (no production code): the only upload origin is the fire route; chat/orchestrator cannot publish; the gate is un-disableable.

- [ ] **Step 1: Write the safety tests.**

```python
# atlas/dashboard/tests/test_security.py (additions)
def test_chat_act_rejects_publish_kind(belt_server):
    client, _ = belt_server
    r = client.post("/api/chat/act", json={"action": {"kind": "publish", "slug": "x"}})
    assert r.status_code == 400        # NotReversibleError — chat is T1-only


def test_no_publish_sdk_tool_exists():
    import registry
    tools = [j.tool for e in registry.REGISTRY for j in e.jobs]
    assert not any(("publish" in t or "upload" in t) for t in tools)
```

- [ ] **Step 2: Run — expect pass** (the chat already rejects non-T1; this locks it).

Run: `cd atlas && ../venv/bin/python -m pytest dashboard/tests/test_security.py -q`
Expected: PASS.

- [ ] **Step 3: Add an e2e negative test** in `test_chat_e2e.py`: the chat panel exposes no control that POSTs to `/fire`; the T3 modal's fire button is the only element bound to it (assert by DOM + that a rogue `approve`/`publish` action from the chat done-frame is dropped, mirroring the existing Slice-5 negative tests).

- [ ] **Step 4: Commit.**

```bash
git add atlas/dashboard/tests/test_security.py atlas/dashboard/tests/e2e/test_chat_e2e.py
git commit -m "test(herald): negative-safety — no T3 backdoor (chat/orchestrator cannot publish)"
```

---

## Milestone M3 — Quota back-pressure (reserve-then-spend)

### Task 11: `publish_quota.py` — atomic reserve/commit/release + drain

**Files:**
- Create: `atlas/dashboard/publish_quota.py`
- Modify: `atlas/dashboard/publish.py` (`approve_and_fire` reserves a slot; queues if full) + `atlas/dashboard/settings_store.py` (confirm `QUOTA` numbers from M0)
- Test: `atlas/dashboard/tests/test_publish_quota.py`

**Interfaces:**
- Produces: `reserve(path) -> bool`, `commit(path)`, `release(path)`, `spent_today(path) -> int`, `window_reset_iso(path) -> str`; keyed by the UTC reset day; lock-guarded.

- [ ] **Step 1: Write the failing test.**

```python
# atlas/dashboard/tests/test_publish_quota.py
import publish_quota as q


def test_reserve_caps_at_ceiling(tmp_path, monkeypatch):
    path = tmp_path / "publish_quota.json"
    monkeypatch.setattr(q, "MAX_PER_DAY", 2)
    assert q.reserve(path) and q.reserve(path)     # 2 slots
    assert not q.reserve(path)                      # 3rd refused (back-pressure)


def test_release_returns_a_slot(tmp_path, monkeypatch):
    path = tmp_path / "publish_quota.json"
    monkeypatch.setattr(q, "MAX_PER_DAY", 1)
    assert q.reserve(path)
    q.release(path)
    assert q.reserve(path)                           # slot freed on failure


def test_commit_persists_spend(tmp_path, monkeypatch):
    path = tmp_path / "publish_quota.json"
    monkeypatch.setattr(q, "MAX_PER_DAY", 5)
    q.reserve(path); q.commit(path)
    assert q.spent_today(path) == 1
```

- [ ] **Step 2: Run it — expect failure.**

Run: `cd atlas && ../venv/bin/python -m pytest dashboard/tests/test_publish_quota.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `publish_quota.py`.**

```python
# atlas/dashboard/publish_quota.py
"""Shared YouTube upload quota — reserve-then-spend under a lock (spec §8/E2).

Project-wide ~6 uploads/day SHARED across ALL channels. The check-and-reserve is
atomic so two near-simultaneous fires can't both pass. Keyed by the UTC reset day;
a new day resets the counter. State lives in a small dashboard-owned JSON."""
from __future__ import annotations

import json
import pathlib
import threading
import time

import chat_state

MAX_PER_DAY = 6          # confirmed at M0; mirror settings_store.QUOTA["max_uploads_per_day"]
_LOCK = threading.Lock()


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _load(path) -> dict:
    d = chat_state.load_json(pathlib.Path(path), {}) or {}
    if d.get("day") != _today():
        d = {"day": _today(), "reserved": 0, "committed": 0}
    return d


def reserve(path) -> bool:
    with _LOCK:
        d = _load(path)
        if d["reserved"] >= MAX_PER_DAY:
            return False
        d["reserved"] += 1
        chat_state.atomic_write_json(pathlib.Path(path), d)
        return True


def commit(path) -> None:
    with _LOCK:
        d = _load(path)
        d["committed"] = d.get("committed", 0) + 1
        chat_state.atomic_write_json(pathlib.Path(path), d)


def release(path) -> None:
    with _LOCK:
        d = _load(path)
        d["reserved"] = max(0, d.get("reserved", 0) - 1)
        chat_state.atomic_write_json(pathlib.Path(path), d)


def spent_today(path) -> int:
    return _load(path).get("committed", 0)


def window_reset_iso(path) -> str:
    t = time.gmtime()
    return time.strftime("%Y-%m-%dT00:00:00Z",
                         time.gmtime(time.mktime((t.tm_year, t.tm_mon, t.tm_mday + 1,
                                                  0, 0, 0, 0, 0, 0))))
```

- [ ] **Step 4: Wire reserve/commit/release into `approve_and_fire`.** Before the resume: `if not publish_quota.reserve(quota_path): write a queued_for_quota receipt + park + return a 200 with status "queued_for_quota"`. After the resume: `commit` on success, `release` on failure.

- [ ] **Step 5: Run the tests — expect pass.**

Run: `cd atlas && ../venv/bin/python -m pytest dashboard/tests/test_publish_quota.py dashboard/tests/test_publish_api.py -q`
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add atlas/dashboard/publish_quota.py atlas/dashboard/publish.py atlas/dashboard/tests/test_publish_quota.py
git commit -m "feat(herald): shared upload quota — reserve-then-spend + back-pressure (E2/E9)"
```

---

## Milestone M4 — Connection state machine + secret store + Go-Live Setup guide (faked OAuth)

> **Effort note (decision T3, 2026-06-24):** the Fernet-at-rest encryption (Task 12) and the OAuth `state`
> CSRF nonce (Task 14) are **hardening, not critical-path**, for a single-user localhost dashboard — the
> real leak vector (a committed token) is covered by `.gitignore` alone. Keep them, but if M1–M4 run long
> they are the sanctioned place to defer: ship the `SecretStore` abstraction + `.gitignore` first, add the
> Fernet body + CSRF nonce as a fast follow. Do NOT defer them silently — note it in the commit if you do.

### Task 12: The encrypted secret store

**Files:**
- Create: `atlas/dashboard/secrets_store.py`
- Modify: `atlas/dashboard/requirements.txt` (add `cryptography`)
- Modify: `.gitignore` (ignore `atlas/dashboard/.herald_secret.key` + `atlas/dashboard/herald_secrets.json`)
- Test: `atlas/dashboard/tests/test_secrets_store.py`

**Interfaces:**
- Produces: `save_token / get_token / has_token / delete_token / save_client / get_client / has_client`. Keyfile auto-generated `0600`; `HERALD_SECRET_KEY` env override.

- [ ] **Step 1: Write the failing test.**

```python
# atlas/dashboard/tests/test_secrets_store.py
import secrets_store as s


def test_token_roundtrip_encrypted(tmp_path):
    st = s.SecretStore(tmp_path / "secrets.json", tmp_path / "key")
    st.save_token("UC1", "refresh-abc")
    assert st.get_token("UC1") == "refresh-abc"
    raw = (tmp_path / "secrets.json").read_text()
    assert "refresh-abc" not in raw          # ciphertext on disk, never plaintext


def test_missing_token_is_none(tmp_path):
    st = s.SecretStore(tmp_path / "secrets.json", tmp_path / "key")
    assert st.get_token("nope") is None and st.has_token("nope") is False


def test_keyfile_is_0600(tmp_path):
    st = s.SecretStore(tmp_path / "secrets.json", tmp_path / "key")
    st.save_token("UC1", "x")
    import stat
    mode = (tmp_path / "key").stat().st_mode
    assert stat.S_IMODE(mode) == 0o600
```

- [ ] **Step 2: Run it — expect failure.**

Run: `cd atlas && ../venv/bin/python -m pytest dashboard/tests/test_secrets_store.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `secrets_store.py`.**

```python
# atlas/dashboard/secrets_store.py
"""Encrypted, dashboard-owned secret store for Herald (spec §6.4).

Per-channel refresh tokens + the OAuth client secret, encrypted at rest (Fernet).
The master key is a local 0600 keyfile (auto-generated) or HERALD_SECRET_KEY.
get_token is called ONLY by the server-side uploader seam — never over HTTP.

Threat model (honest): key + ciphertext sit in the same dir, so this protects
against casual git/backup leakage, NOT a local-read attacker. Fine for a
single-user local dashboard."""
from __future__ import annotations

import json
import os
import pathlib

from cryptography.fernet import Fernet

import chat_state


class SecretStore:
    def __init__(self, store_path, key_path):
        self.store_path = pathlib.Path(store_path)
        self.key_path = pathlib.Path(key_path)
        self._fernet = Fernet(self._load_or_make_key())

    def _load_or_make_key(self) -> bytes:
        env = os.environ.get("HERALD_SECRET_KEY")
        if env:
            return env.encode()
        if self.key_path.exists():
            return self.key_path.read_bytes()
        key = Fernet.generate_key()
        self.key_path.write_bytes(key)
        os.chmod(self.key_path, 0o600)
        return key

    def _load(self) -> dict:
        return chat_state.load_json(self.store_path, {}) or {}

    def _save(self, d: dict) -> None:
        chat_state.atomic_write_json(self.store_path, d)

    def save_token(self, channel_id: str, refresh_token: str) -> None:
        d = self._load()
        d.setdefault("tokens", {})[channel_id] = \
            self._fernet.encrypt(refresh_token.encode()).decode()
        self._save(d)

    def get_token(self, channel_id: str) -> str | None:
        tok = self._load().get("tokens", {}).get(channel_id)
        return self._fernet.decrypt(tok.encode()).decode() if tok else None

    def has_token(self, channel_id: str) -> bool:
        return channel_id in self._load().get("tokens", {})

    def delete_token(self, channel_id: str) -> None:
        d = self._load(); d.get("tokens", {}).pop(channel_id, None); self._save(d)

    def save_client(self, client_id: str, client_secret: str) -> None:
        d = self._load()
        d["client"] = {"client_id": client_id,
                       "client_secret": self._fernet.encrypt(client_secret.encode()).decode()}
        self._save(d)

    def get_client(self) -> dict | None:
        c = self._load().get("client")
        if not c:
            return None
        return {"client_id": c["client_id"],
                "client_secret": self._fernet.decrypt(c["client_secret"].encode()).decode()}

    def has_client(self) -> bool:
        return "client" in self._load()
```

- [ ] **Step 4: Run the tests — expect pass.**

Run: `cd atlas && ../venv/bin/python -m pytest dashboard/tests/test_secrets_store.py -q`
Expected: PASS (3 tests). (`pip install cryptography` into the venv first if missing.)

- [ ] **Step 5: Commit.**

```bash
git add atlas/dashboard/secrets_store.py atlas/dashboard/requirements.txt .gitignore atlas/dashboard/tests/test_secrets_store.py
git commit -m "feat(herald): encrypted secret store (Fernet, 0600 keyfile; secrets never surfaced)"
```

### Task 13: Settings channel fields (oauth refs + state-machine hints)

**Files:**
- Modify: `atlas/dashboard/settings_store.py` (`_coerce_channel` adds `oauth_client_ref`, `last_connected_ts`, `token_expiry_hint`; top-level `oauth_client` flag derived in `public_settings`)
- Test: `atlas/dashboard/tests/test_settings_api.py` (extend)

- [ ] **Step 1: Write the failing test** (assert a saved channel keeps the new fields + that no secret is ever returned by `public_settings`). **Step 2:** run → fail. **Step 3:** extend `_coerce_channel` to keep the new bounded string/number fields; `public_settings` adds `"oauth_client": secrets.has_client()` (boolean only). **Step 4:** run → pass. **Step 5:** commit `feat(herald): settings channel oauth refs + state-machine hints`.

### Task 14: OAuth connect/callback endpoints (state CSRF; faked seam)

**Files:**
- Modify: `atlas/dashboard/app.py` (add `GET /api/channels/{id}/connect` + `GET /api/oauth/callback`; `app.state.oauth_fn = None`)
- Create: `atlas/dashboard/oauth.py` (the seam wrapper: state-nonce mint/verify + `connect_url` + `exchange`; real Google flow shelled, fake injected)
- Test: `atlas/dashboard/tests/test_oauth_api.py`

**Interfaces:**
- Produces: connect returns a consent URL (real) or a canned one (fake); callback validates `state`, calls `oauth_fn.exchange`, stores the token via `secrets_store`, sets `connection_status="connected"`, reads back verification flags.

- [ ] **Step 1: Write the failing tests.**

```python
# atlas/dashboard/tests/test_oauth_api.py
def test_callback_rejects_bad_state(oauth_server):
    client, _ = oauth_server
    r = client.get("/api/oauth/callback?code=abc&state=forged")
    assert r.status_code == 400        # CSRF guard (E6)


def test_connect_then_callback_stores_token(oauth_server):
    client, ctx = oauth_server         # fixture injects app.state.oauth_fn (fake)
    start = client.get("/api/channels/UC1/connect")
    state = start.json()["state"]
    r = client.get(f"/api/oauth/callback?code=abc&state={state}")
    assert r.status_code in (200, 302)
    # the fake exchange returns channel_id UC1 + a token; assert it's stored + never echoed
    assert ctx["secrets"].has_token("UC1")
    assert "refresh" not in r.text.lower()
```

- [ ] **Step 2–4:** run → fail; implement `oauth.py` (mint a per-session `state` set in a server-side dict, `connect_url(channel_id, state)`, `exchange(code) -> {channel_id, refresh_token, project_verified, channel_phone_verified}` delegating to `app.state.oauth_fn` when set else the real Google flow — shelled `NotImplementedError` until M5); the callback verifies `state`, stores the token, updates settings; run → pass.
- [ ] **Step 5: Commit** `feat(herald): OAuth connect/callback with state CSRF (faked seam; real flow shelled)`.

### Task 15: Connection-state UI + proactive disconnect banner (frontend)

**Files:**
- Modify: `atlas/dashboard/static/app.js` (Channels bay: render `connection_status` badge, a Connect/Reconnect button hitting `/api/channels/{id}/connect`, and a persistent "Channel X needs reconnect" banner when state ∈ `needs-reconnect|expired|revoked`)
- Modify: `atlas/dashboard/static/index.html` + `styles.css` (the banner + badge styles, reuse status tokens)
- Test: `atlas/dashboard/tests/e2e/test_broadcast_e2e.py`

- [ ] **Step 1:** Write a Playwright e2e: seed a channel in `needs-reconnect`; assert the banner shows + a Reconnect button is present; clicking it calls connect (faked). **Step 2:** run → fail. **Step 3:** implement the frontend. **Step 4:** run e2e (`wait_until="domcontentloaded"`) → pass. **Step 5:** commit `feat(herald): channels connection-state UI + proactive reconnect banner (D3)`.

### Task 16: The Go-Live Setup guide

**Files:**
- Modify: `atlas/dashboard/app.py` (add `GET /api/broadcast/setup`)
- Create: `atlas/dashboard/broadcast.py` (compute the per-channel 8-step checklist from `settings_store` + `secrets_store` — booleans only)
- Modify: `static/{index.html,app.js,styles.css}` (a `v-broadcast` surface: per-channel accordion + roll-up + step links + ✅/⬜ + the honest "private until verified" banner)
- Test: `atlas/dashboard/tests/test_broadcast_api.py` + `e2e/test_broadcast_e2e.py` (extend)

**Interfaces:**
- Produces: `GET /api/broadcast/setup -> {channels:[{channel_id, title, steps:[{n, label, link, done, why}], ready}], rollup}`.

- [ ] **Step 1: Write the failing API test.**

```python
# atlas/dashboard/tests/test_broadcast_api.py
def test_setup_checklist_reflects_flags(broadcast_server):
    client, ctx = broadcast_server     # seeds: client creds present, channel not phone-verified
    r = client.get("/api/broadcast/setup")
    assert r.status_code == 200
    steps = {s["n"]: s["done"] for s in r.json()["channels"][0]["steps"]}
    assert steps[4] is True            # oauth client present
    assert steps[6] is False           # phone-verify not done
    assert r.json()["channels"][0]["ready"] is False
    # never leaks a secret
    assert "client_secret" not in r.text
```

- [ ] **Step 2–4:** run → fail; implement `broadcast.py` (map each of the 8 steps to a boolean from `secrets_store.has_client()`, `channel.project_verified`, `channel.channel_phone_verified`, `connection_status`, niche map; include the direct links from the spec §7.3 table) + the endpoint + the frontend accordion; run → pass.
- [ ] **Step 5: Commit** `feat(herald): Go-Live Setup guide (per-channel checklist, links, live checkpoints — D5)`.

### Task 17: Wire the T3 modal to the fire route + hierarchy/empty-state/schedule guards (frontend)

**Files:**
- Modify: `atlas/dashboard/static/app.js` (`openPublishModal`): render the 3 thumbnails (pick one), editable title/desc/tags, visibility, a schedule picker (reject past; echo resolved absolute time), the forced `made_for_kids` choice, the blocker list, and a single gated **Publish** button that POSTs to `/api/publish/{slug}/fire`; the "not set up yet" empty state routes to the Setup guide
- Modify: `static/styles.css` (modal hierarchy)
- Test: `atlas/dashboard/tests/e2e/test_publish_e2e.py`

- [ ] **Step 1:** e2e: open the modal for a verified-fake project; pick a thumbnail; set a future schedule; assert a past time is rejected and the resolved absolute time is shown; click Publish; assert the (faked) receipt renders. A second e2e: zero-creds project shows the calm "not set up yet → Open Setup guide" state, not a red wall. **Step 2:** run → fail. **Step 3:** implement. **Step 4:** run (domcontentloaded) → pass. **Step 5:** commit `feat(herald): T3 modal wired to fire route — hierarchy, empty state, schedule guards, COPPA (D1/D2/D4/D6)`.

---

## Milestone M5 — Live OAuth (needs verified Google creds; shelled behind the seam)

### Task 18: Real `oauth_fn` (Google consent → refresh token → channels.list)

**Files:**
- Create: `publisher/youtube_oauth.py` (real `google-auth-oauthlib` flow — engine-side, pure given client creds + redirect URI)
- Modify: `atlas/dashboard/oauth.py` (default `oauth_fn` = the real flow when client creds exist)
- Modify: `publisher/requirements.txt` (`google-auth`, `google-auth-oauthlib`, `google-api-python-client`)

**Interfaces:**
- Produces: `exchange(code, *, client, redirect_uri) -> {channel_id, refresh_token, project_verified, channel_phone_verified}` reading `channelId` via `channels.list?mine=true`.

- [ ] **Step 1:** Implement the real flow behind the same seam signature the fake used (Task 14), reading the client creds from `secrets_store.get_client()`. No automated test hits Google — gate behind `HERALD_LIVE_OAUTH=1` + manual verification against one real channel; record the result in the M0 notes file.
- [ ] **Step 2:** Manual smoke: connect one channel, confirm `connection_status="connected"` + the real `channel_id` stored encrypted, token never in any response/log.
- [ ] **Step 3: Commit** `feat(herald): live OAuth connect (real Google flow behind the seam; manual-verified)`.

> **Gated on M0 verification.** Until the Cloud project is verified, the consent screen is in "Testing" mode → tokens expire every 7 days (the state machine + reconnect banner from Task 15 handle this).

---

## Milestone M6a — Real PRIVATE upload (needs only a connected channel; NO verification)

> **Split out (decision T2, 2026-06-24):** private/unlisted uploads need only OAuth (M5) — **not** Google
> sensitive-scope verification. So the first real end-to-end upload is reachable **weeks before** the
> verification gauntlet clears. This is the cheapest real win in the whole plan; sequence it immediately
> after M5. Public/scheduled (the verification-gated half) is M6b.

### Task 19: Real `publish_engine.upload` — private/unlisted only

**Files:**
- Modify: `publisher/publish_engine.py` (`upload`: build the `videos.insert` body from the package, set `status.privacyStatus` to `private`/`unlisted`, `status.selfDeclaredMadeForKids`; set the custom thumbnail via `thumbnails.set` ONLY when the channel is phone-verified; return the receipt fields)
- Modify: `atlas/dashboard/app.py` (default `app.state.uploader` = the engine's `upload` wired with the decrypted token, when a channel is connected)
- Test: a single OPT-IN integration test gated behind `HERALD_LIVE_UPLOAD=1` (kept OUT of the offline suite)

**Interfaces:**
- Consumes: `secrets_store.get_token(channel_id)` (server-side only), the approved package.
- Produces: a real `video_id`; `status:"uploaded"` (private/unlisted).

- [ ] **Step 1: Implement `upload`** using `google-api-python-client`, authenticating with the channel's refresh token. `privacyStatus="private"`/`"unlisted"` works the moment a channel is connected — no verification. A custom thumbnail still requires the channel to be phone-verified; skip it (with a note) when it isn't.
- [ ] **Step 2: Wire `app.state.uploader`** to call the engine's `upload` with the decrypted token (the dashboard does the decrypt; the engine stays pure). Remember the H1 fix: refresh `disp._uploader` on the cached dispatcher at fire time.
- [ ] **Step 3: Manual smoke (private):** fire a real PRIVATE upload for one finished project; confirm the receipt `video_id`, the video is PRIVATE on the channel, quota committed once, **no duplicate on a re-fire (E1)** + **no second POST on a UI double-click (D-F3)**. Record in the M0 notes.
- [ ] **Step 4: Commit** `feat(herald): real PRIVATE videos.insert upload (no verification needed)`.

> **Gated on M5 (a connected channel) only.** Reachable as soon as the CEO has any OAuth client — the
> 7-day Testing-mode token expiry applies (the reconnect machinery handles it), but private uploads work.

---

## Milestone M6b — PUBLIC / scheduled upload (needs the verification gauntlet)

### Task 20: Public + scheduled (`privacyStatus=public` + `publishAt`)

**Files:**
- Modify: `publisher/publish_engine.py` (`upload`: allow `privacyStatus="public"` + `status.publishAt` when scheduled)

**Interfaces:**
- Produces: `status:"uploaded"` (public) or `"scheduled"` (public + `publishAt`) — **only** when both `project_verified` + `channel_phone_verified` are green.

- [ ] **Step 1: Extend `upload`** to set `privacyStatus="public"` + `publishAt` (from the approved package's schedule), **gated on both verification flags**. Degrade to private + a clear surfaced note when the gauntlet isn't green (never silently downgrade a requested public — the modal already states this, §9 / D-F5).
- [ ] **Step 2: Manual smoke (public, after verification):** schedule one public go-live; confirm it lands at the resolved time; confirm a requested-public-but-unverified attempt blocks/degrades honestly.
- [ ] **Step 3: Commit** `feat(herald): public + scheduled upload (gated on sensitive-scope + phone verification)`.

> **Gated on M0 verification.** Impossible until the Cloud project is sensitive-scope verified AND the
> channel is phone-verified (spec §9). This is the milestone that waits on Google.

> **Gated on M0 verification.** Public/scheduled is impossible until the project is sensitive-scope verified and the channel phone-verified (spec §9). Private uploads are the early end-to-end smoke test (decision #5).

---

## Final verification (run before declaring done)

- [ ] **Full unit suite green:**

```bash
cd atlas && ../venv/bin/python -m pytest tests/ -q
cd atlas && ../venv/bin/python -m pytest dashboard/tests/ --ignore=dashboard/tests/e2e -q
cd publisher && ../venv/bin/python -m pytest tests/ -q
```

- [ ] **e2e green (two batches if contended):**

```bash
cd atlas && ../venv/bin/python -m pytest dashboard/tests/e2e/ -q
```

- [ ] **Manual belt smoke:** restart the server (no `--reload`), run a video to `done`, confirm it auto-packages, parks at the T3 publish gate, the modal reviews the exact package + thumbnails, a fake/private fire produces a receipt, and the belt shows 12 stations.
- [ ] **Negative safety re-confirmed:** no publish SDK tool; chat cannot fire; unattended parks at publish; a re-fire never double-uploads.

---

## Autoplan review report (2026-06-24)

Three independent Claude review subagents (CEO / Eng / Design) verified this plan against the real repo.
Codex outside-voice unavailable (sandbox `bwrap` network error) → eng ran subagent-only.

| Phase | Verdict | Findings (C/H/M/L) |
|---|---|---|
| CEO (strategy/sequencing) | SOUND WITH CONCERNS | 0 / 2 / 3 / 2 |
| Eng (code-grounded, vs real files) | NEEDS REVISION → corrected | 3 / 3 / 5 / 3 |
| Design (frontend tasks 15–17) | NEEDS DESIGN WORK → corrected | 0 / 3 / 5 / 2 |

**Auto-decided (mechanical, applied above in "Corrections"):** all 3 eng criticals (registry symbol,
test fixture, `J` closure), the uploader-threading + gate-placement highs, the Glint-progress / quota-date /
gate-test / channel-stamp mediums, the chat-act test shape, the E4-is-frontend-only correction, and the
full design frontend hardening set (double-fire guard, result-state matrix, deterministic empty state,
schedule DST/gating, reconnect honesty, loading states, task expansion). Principles: P1 completeness +
P6 bias-to-action for correctness bugs; P5 explicit for the UI guards.

**CEO taste decisions (resolved at the gate 2026-06-24 — option A, all my recs):**
- **T1 — Glint / now-value claim → down-stated (no Glint pulled into scope).** Until Glint (#8) ships,
  `_glint_candidates` returns ONE placeholder, so this plan's honest now-value is a **private-upload pipe
  + connection/setup machinery + a review modal**, NOT a 3-thumbnail pick-one surface. The "3 thumbnails"
  value lands when #8 ships (its own spec) — the seam already calls it, no Herald change needed. Do not
  inherit the spec's optimistic "pick 1 of 3" framing while building the placeholder path.
- **T2 — M6 split into M6a (private, post-M5) / M6b (public, post-verification)** — applied below; the
  cheapest real win (a real private upload) now lands weeks before verification.
- **T3 — secret store + OAuth CSRF marked deferrable hardening** (note on M4) — kept, but cut-able if
  M1–M4 run long; ship the abstraction + `.gitignore` first.

**Decision audit trail:**

| # | Phase | Decision | Class | Principle | Outcome |
|---|---|---|---|---|---|
| 1 | Eng | `registry.ENTRIES`→`REGISTRY` | Mechanical | P1 | Fixed inline + Corrections |
| 2 | Eng | unit tests use `client` not e2e `belt_server` | Mechanical | P1 | Corrections C2 |
| 3 | Eng | `approve_and_fire` returns dict, route wraps `J` | Mechanical | P5 | Corrections C3 |
| 4 | Eng | thread `uploader` into cached dispatcher | Mechanical | P1 | Corrections H1 |
| 5 | Eng | publish branch inside `!= done` guard; fix spec | Mechanical | P5 | Corrections H3 |
| 6 | Eng | `_glint_candidates` pass `progress` | Mechanical | P1 | Corrections M1 |
| 7 | Eng | `window_reset_iso` use datetime/timedelta | Mechanical | P1 | Corrections M3 |
| 8 | Eng | Task 6 test via `_publish_gate` directly | Mechanical | P3 | Corrections M4 |
| 9 | Eng | stamp `routing.channel_id` before persist | Mechanical | P1 | Corrections L3 |
| 10 | Eng | chat/act test posts top-level `kind` | Mechanical | P1 | Corrections L1 |
| 11 | Eng | E4 is frontend-only (data.belt already dynamic) | Mechanical | P3 | Corrections / Task 4 |
| 12 | CEO | quota ceiling single-sourced from `QUOTA` | Mechanical | P4 | Corrections CEO-F3 |
| 13 | CEO | record future trusted-auto-publish seam | Mechanical | P6 | Corrections note |
| 14 | Design | UI double-fire guard (protects E1) | Mechanical | P1 | Corrections D-F3 |
| 15 | Design | fire result-state matrix (5 states) | Mechanical | P1 | Corrections D-F2 |
| 16 | Design | deterministic `setup_state` empty state | Mechanical | P5 | Corrections D-F4 |
| 17 | Design | schedule DST/gating + loading states + task expansion | Mechanical | P1 | Corrections D-F5/F7/F8 |
| T1 | CEO | Glint dependency / now-value claim | TASTE | — | Gate |
| T2 | CEO | split M6 → private (M6a) vs public (M6b) | TASTE | — | Gate |
| T3 | CEO | secret-store + CSRF effort level | TASTE | — | Gate |

## Reviewer Concerns / open carry-overs

- **M0 is a real-world dependency, not code.** M5/M6 cannot be automatically tested and are gated on Google verification the CEO must complete. The plan keeps them last and behind seams so M1–M4 ship independently and stay green.
- **Glint (#8) must exist for non-placeholder thumbnails.** Until Glint's registry entry lands, `_glint_candidates` returns the placeholder candidate (graceful). If Glint ships after M1, no Herald change is needed — the seam already calls it.
- **`stubs.make_stub_producer`** referenced in Task 6's test may not exist; the task notes the direct-`_publish_gate` alternative. Confirm the offline-stub helper name in `adapters/stubs.py` when writing that test.
