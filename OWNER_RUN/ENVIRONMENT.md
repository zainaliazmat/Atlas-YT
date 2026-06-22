# ENVIRONMENT.md — what's installed/working vs missing

> Phase §1 deliverable. **Verified** = I ran the check and saw the output. Date: 2026-06-22.

## Summary verdict

The pipeline can run end-to-end **including render** — but **only if the process PATH points at Node 22**, not the system default. The system default `node` is v18, which the current HyperFrames (≥0.6.115) cannot run. This is the single most important environment fact.

## Verified results

| Prereq | Needed | Found | Status |
|---|---|---|---|
| Node (system default) | ≥ 22 | **v18.19.1** (`/usr/bin/node`, apt-pinned) | ❌ too old — HyperFrames crashes (`SyntaxError: 'util' has no export 'styleText'`) |
| Node (via nvm) | ≥ 22 | **v22.18.0** installed in `~/.nvm` | ✅ works — `npx hyperframes --help` runs clean |
| HyperFrames CLI | reachable | **v0.6.121** (auto-installed by npx under Node 22) | ✅ verified `--help` |
| FFmpeg | on PATH | **6.1.1** | ✅ |
| FFprobe | on PATH | **6.1.1** | ✅ |
| venv Python | shared root venv | **3.12.3** at `venv/` | ✅ |
| Kokoro TTS | importable | `import kokoro_onnx, soundfile` → **OK** | ✅ |
| `ANTHROPIC_API_KEY` | must be UNSET | **unset** | ✅ (do not set — bills metered API) |

## The Node 22 requirement — operational note

The audio/composition engines shell out to `npx hyperframes` and inherit whatever `node` is first on `PATH`. To run any render-producing stage, the pipeline MUST be launched with Node 22 active. Before any `python run.py produce …`:

```bash
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use 22
node -v   # must print v22.x before running the pipeline
```

Without this, every stage up to `render` works, but `render`/`tts`/`lint`/`validate`/`inspect` (all HyperFrames-shelled) fail. **Final `video.mp4` cannot be verified under system Node 18.** With nvm Node 22 active, render is expected to work — to be confirmed live in §3/§6.

## Branch reality (differs from the brief)

- The brief & `PROJECT_CONTEXT.md` say active work lives on **`master`** and to leave **`main`** untouched. **Ground truth: the only branch is `main`**, and it IS the active working branch (`git branch -a` → `* main`, `remotes/origin/main`). HEAD is `5b86a8b "first commit"`. All the in-flight work is uncommitted on `main`.
- Decision: I will work on `main` (it is the de-facto active branch here) but create a working branch before committing substantive changes, and never force-push or touch `origin/main` without asking.

## Uncommitted in-flight work (broader than the brief described)

`git status` shows TWO bodies of in-flight work, not one:
1. **Issue #2** (described in brief): `asset-sourcer/source_engine.py` (+test), and brand-chips touching `atlas/registry.py`.
2. **A NEW 8th agent — "Reference Analyst"** (not in the brief): untracked `reference-analyst/`, `atlas/adapters/reference_analyst.py`, `atlas/contracts/reference_rubric.schema.json`, 3 new test files, `atlas/contracts/__init__.py` + `registry.py` edits, and a `ReferanceVideos/` (sic) dir. This appears to be a half-landed agent that learns from reference videos. **Must be assessed in §2** — it may be partially wired and could break registry/contract tests.

## Open environment questions

- Are per-project `requirements.txt` fully installed in the shared venv? (kokoro/soundfile/jsonschema present; full sweep deferred to §2.)
- Does `claude-agent-sdk` subscription path actually authenticate in this environment? (To confirm live in §3 — a real produce run is the test.)
