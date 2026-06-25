# Handoff prompt — fix the render last-mile (text-occlusion) and ship two videos

> Copy everything between the lines into a fresh session. It is self-contained: exact state,
> the blocker, the environment, the task, and how to verify like a CEO.

---------------------------------------------------------------------------------------------

You are the CEO + lead engineer of YT-AGENTS (`/home/zain-ali/Documents/YT-AGENTS`, git branch
`control-room` — stay on it). A prior session got the video pipeline almost all the way to
shipping two videos and hit ONE documented hard blocker. Your job: fix that blocker carefully
(with tests, no rushing), get BOTH videos to a final `video.mp4`, and judge quality like a CEO
using a REAL visible browser.

## First, drive a real browser (do this before anything else)
Run **/open-gstack-browser** to launch the visible GStack browser so the human can watch every
step, and use **/gstack** (the browse skill) for all UI/visual checks — load the dashboard at
http://127.0.0.1:8848/, watch the belt live, and render/screenshot scene frames to judge
visuals with your own eyes. Note: the browse sandbox blocks `file://` paths, so to view a scene
you must render it through the pipeline (draft.mp4) or serve it over http, not open the file
directly. On Linux the browser needs `GSTACK_CHROMIUM_NO_SANDBOX=1` to launch; if `connect`
fails with a sandbox error, set that env var and retry.

## The exact state you're inheriting
Two real projects under `atlas/projects/`:
- **Video 1 (new):** `how-ai-agents-actually-work-20260624-152336-86de` — script + fact-check
  PASS (clean). Stages research→narration done. **Blocked at the compose stage:** 7/9 scenes
  pass the auto-gate, 2 fail. (It may be auto-retrying compose when you arrive — let it settle
  or stop it.)
- **Video 2:** `why-tech-ceos-are-quietly-cancelling-their-ai-plan-20260624-005342-8b62` —
  `blocked_at_factcheck` with **1** remaining flagged claim (down from 3 via CEO guidance). The
  claim "most CEOs maintain/double AI investment into 2026" still cites the wrong CNBC
  $1T-2027-capex article; it must be re-cited to the brief's weforum.org + PwC/PRNewswire CEO-
  survey sources. Use the gate **Guide** (re-runs script→factcheck) to clear it, then it flows
  to compose where it will hit the SAME blocker below.

## Already fixed and committed (don't redo these)
- `196dc57` contrast failures surface, don't hard-block the compose auto-gate (was blocking
  100% of renders; now 7/9 scenes pass).
- `7458726` conceptual `kind:diagram` shots source stock photos (Pexels/Pixabay) instead of
  blank placeholders, + strip the decorative `<title>` from inline brand-logo SVGs.
Engine + dashboard suites are green. There's also a deferred research brief for a *custom*
diagram generator at `docs/research-prompts/custom-diagram-generation.md` (separate, later).

## THE BLOCKER to fix (this is your main task)
The HyperFrames render gate (`npx hyperframes inspect <scene_dir> --json`) correctly rejects
two scenes with **`text_occluded`** errors — an opaque media element is painted OVER the
scene's text. Confirmed errors:
- **Scene 3** — layout `comparison-2up`: `span.brand-chip-name` "GPT-4o" and "Claude" are
  occluded (the brand chips overlap the sourced photo / media).
- **Scene 8** — layout `data-chart`: `h2.scene-title` "MORE STEPS, LESS RELIABLE" is occluded
  by the media element (CSS shows `.media` and `.overlay{z-index:50}`, while `.scene-title`
  has no z-index, so media paints over the title).
This blocks the WHOLE video (the gate needs all scenes clean) and fails with OR without photos —
it's a z-order / layout bug in the composition engine, not a visuals choice. The project's own
notes call this the unsolved "render last-mile" (big-number + brand-chip overlap).

Fix it in `composition-engineer/composition_engine.py` (the CSS string around lines 1120-1140:
`.layout`, `.media`, `.overlay`, `.scene-title`, `.split-pane`, and the `comparison-2up` /
`data-chart` layout partials). Make text zones (`.scene-title`, `.brand-chip-name`, captions)
render ABOVE `.media`, OR give them non-overlapping zones in those two layouts. Do it with TDD
(the engine has `composition-engineer/tests/test_composition_engine.py`, 72 tests green) and
**verify scene-by-scene** by running `npx hyperframes inspect` on each scene dir AND eyeballing
a rendered frame in the visible browser. Do NOT break the 7 already-passing scenes.

## How to run the pipeline (environment — important)
```
cd /home/zain-ali/Documents/YT-AGENTS/atlas
export PATH="/home/zain-ali/.nvm/versions/node/v22.18.0/bin:$PATH"   # Node 22 — REQUIRED for npx hyperframes (v0.7.5)
unset ANTHROPIC_API_KEY                                              # fleet uses the Claude Agent SDK subscription seam; a metered key would bill/break it
export GSTACK_CHROMIUM_NO_SANDBOX=1
# dashboard server (belt UI + runs the pipeline in a thread):
/home/zain-ali/Documents/YT-AGENTS/venv/bin/python -m dashboard.server --host 127.0.0.1 --port 8848
```
Keys (Pexels/Pixabay/Tavily etc.) load automatically from `/home/zain-ali/Documents/YT-AGENTS/.env`
via dotenv — do NOT add ANTHROPIC_API_KEY there.

Gotchas learned the hard way:
- **The server caches engine modules.** After editing `composition_engine.py` (or any engine),
  RESTART the dashboard server to load the change.
- **During the compose stage the server goes unresponsive** (headless-Chrome scene renders
  saturate CPU); monitor progress by reading `project.json` on disk, not the HTTP API.
- Re-run a stuck/edited video via `POST /api/atlas/request {"intent":"rerun","args":{"slug":..,
  "from_stage":"narration"}}` (rerun needs a from_stage that already ran; "narration" is the
  last done stage before compose). Approve gates via `POST /api/atlas/request
  {"intent":"answer_escalation","args":{"action":"approve","slug":..,"gate":"factcheck"|"final_render"}}`
  — the UI also requires ticking the `#gt-ack` checkbox before the approve button fires.
- Inspect one scene: `npx hyperframes inspect atlas/projects/<slug>/scenes/scene-0N --json`.
- Compose result is in `<slug>/composition_manifest.json` (`summary.gated_ok` / per-scene `gate`).
- Extract a frame from a rendered scene: `ffmpeg -y -ss 0.8 -i <scene>/renders/draft.mp4
  -frames:v 1 /tmp/f.png` (only scenes that PASS the gate get a draft.mp4).

## Open product decision (ask the human, don't assume)
Stock photos now source for diagram shots but quality is INCONSISTENT for abstract explainer
concepts: "errors compound" got a fitting ERROR-phone, but "chat bubble" pulled a pink button
and "transformer" pulled an antique typewriter (off-topic, off-palette cream/dark). Decide WITH
the human: (a) keep photos but hand-curate/replace the weak ones, (b) improve the search query
to use the scene TOPIC instead of the literal diagram metaphor, or (c) drop to clean
text-forward for the abstract scenes. Judge from REAL composed frames in the browser, not raw
stock thumbnails (the engine lays a house-style scrim over photos).

## Definition of done
Both videos reach a final `<slug>/video.mp4`, every scene passes the HyperFrames gate, you have
watched the final renders in the visible browser and judged them genuinely shippable (or aligned
the visuals with the human), and the engine test suites are green. Commit each fix
(`fix(composition): ...`) ending messages with:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Don't push or open a PR.

---------------------------------------------------------------------------------------------
