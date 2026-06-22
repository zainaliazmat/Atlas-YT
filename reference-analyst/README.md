# Vera, The Reference Analyst 🔬

Vera ingests one or more **reference videos**, measures their quality properties, and
emits/maintains a **rubric** — a machine-checkable description of "what a good video
looks like" that later pipeline stages can be tuned toward.

She **defines the standard**; she does not generate or improve videos, and she is not
the self-improvement / Coach loop. She is a delegable job + persona, **not** a stage in
the production pipeline — adding her is purely additive (one registry entry + one
adapter + this sibling project + one new contract).

## What she produces

A **rubric**: banded objective **targets** (pacing, motion, color, audio, structure) +
a judged **style profile** (visual style, typography character, motion feel, mood,
layout types), plus the few `open_questions` only taste can answer and the persisted
`ceo_prefs` answers.

- **Objective half** runs offline + deterministically (FFmpeg + OpenCV) — same input,
  same numbers.
- **Judged half** is an injected vision seam; it degrades to a note, never a crash.
- Each target is `{value, band}` — `band` is the acceptable range later tuning aims to
  land inside.
- Feeding **more** references **tightens** the bands toward the references' shared DNA
  (the durable, merging rubric store).

## Layout

| File | Role |
|------|------|
| `reference_engine.py` | the pure, injectable engine (FFmpeg + OpenCV core; vision is a seam) |
| `rubric_store.py` | durable, MERGING rubric memory per named "standard" (atomic writes) |
| `llm.py` | provider seam (`VERA_LLM`: claude/gemini/deepseek) + the vision style-profiler |
| `run.py` | CLI (`rubric`, `chat`) |
| `chat.py` | co-worker REPL (summary-only memory; build a rubric mid-chat behind a `[y/N]` gate) |
| `chat_state.py`, `compaction.py` | crash-safe, summary-only memory (repo pattern) |
| `SKILL.md` | the engine's job method/contract |
| `soul/` | persona bundle — `SOUL.md` (identity), `STYLE.md` (voice), `examples/` |
| `tests/` | offline pytest (no network, no ffmpeg required) |

## CLI

```bash
# Build / extend a named standard from reference video(s). More refs tighten the bands.
python run.py rubric ref1.mp4 ref2.mp4 --standard "my-house-style"
python run.py rubric another.mp4 --standard "my-house-style"     # merges in

# Objective-only (fully offline, no LLM/vision pass):
python run.py rubric ref.mp4 --no-vision

# Talk to Vera (co-worker mode):
python run.py chat
```

The durable rubric is written under `standards/<slug>.json`; `--out PATH` also drops a
copy of this run's rubric. The emitted rubric validates against atlas's frozen
`reference_rubric` contract.

## Provider switch

`VERA_LLM` (read at import): default `claude` (your Claude Code subscription, no API
key — do **not** set `ANTHROPIC_API_KEY`), or `gemini` / `deepseek`. Vision (the style
profile) runs on `claude` or `gemini`; under `deepseek` it degrades to a note.

## How Atlas uses her

- Tool `reference_analyst_build_rubric` (params: `videos` = path or list of paths;
  optional `ceo_prefs`) and persona tool `ask_reference_analyst`.
- The adapter loads this engine in-process, calls `build_rubric`, stamps + **validates**
  the rubric against the frozen contract at the boundary, persists the merged rubric,
  and returns a compact digest (targets summary + the `open_questions`).
- `/agents` lists her with her effective provider.

## Tests

```bash
cd reference-analyst && python -m pytest -q     # offline; no ffmpeg/cv2/network needed
```

The engine's external calls (ffmpeg/cv2) and the vision seam are stubbed; tests assert
the pure plumbing — `_band`, `_t`, `_dig`, `_open_questions`, and the merge that tightens
bands across multiple references.
