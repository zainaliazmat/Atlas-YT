# Atlas — web operator UI

A second frontend over the same `AtlasSession` core the terminal REPL drives. The
terminal (`python run.py chat`) keeps working unchanged; this is additive.

## What's here

**Phase A — the streaming meeting room.** Talk to Atlas in the browser and watch a
turn stream live — Atlas's words (including the `🧠 I'm going with …` decision lines)
and the deterministic `🔎 / ✅` status lines as each teammate works. Same summary-only
memory as the REPL: `/new`, `/summary`, `/help`, `/agents` work here, and closing the
tab distills (or safely parks) the meeting so nothing is lost.

**Phase B — the two human gates as buttons.** When a production run pauses at a gate,
the UI shows the artifact inline with **Approve** / **Revise**:
- *Fact-check gate:* the verdict + flagged/unverifiable claims + the script.
- *Final-render gate:* the render plan + the **playable per-scene draft renders**.

**Approve** is a direct, deterministic `pipeline.produce(slug, approve=[gate])` call
(the gate code — including the rule that a `block` verdict can't be approved away —
runs unchanged); the resulting state is recorded into Atlas's memory so its next turn
is coherent, and the next gate (or completion) surfaces immediately. **Revise** is an
open-ended conversational turn back to Atlas — type what to change and the team
revises (re-script, re-research); it is *not* a pipeline call.

**Phase C — roster + per-agent chat.** The profile menu (top-left) lists **Atlas —
Showrunner** plus every teammate. Pick a teammate to talk to them directly (a persona
chat via the shared `adapter.ask` seam — zero sibling changes). Each profile keeps its
own summary-only memory: `/new`, `/summary`, `/help`, `/agents` work per profile.

Switching profiles **resumes** a session rather than cold-starting it: sessions live
in a process-level cache (Chainlit wipes `user_session` on a profile switch), and
disconnect **parks** the backlog without clearing the live transcript — so switching
away and back keeps the conversation in mind, and a project paused at a gate
re-surfaces (gate state is disk-backed). Note: server-side *memory* resumes intact;
Chainlit doesn't re-render past chat bubbles on reconnect (that needs a data layer,
out of scope). Per-agent web memory lives in `web_sessions/` (separate from each
sibling's own terminal `chat_state.json`).

Memory timing changed for the multi-profile world: disconnect now **parks** (no data
loss) instead of distilling, since a profile switch also fires disconnect and must not
clear the transcript. Distillation happens on an explicit `/new` / `/summary`, or on
the next app launch (the parked backlog is folded in). Same no-data-loss guarantee.

**Phase C v2 — Marlow's job-gate as a button.** In Marlow's profile, `/write
<project-slug>` runs the script-writing job, surfacing his `[y/N]` gate as an
**Approve / Deny button**. This works because `scriptwriter/chat.py`'s gate now routes
through an *injectable approver* (default = the terminal `input()` gate, unchanged);
the web injects a button approver. `scriptwriter/chat.py` is loaded via the isolating
loader, so this needs no further sibling changes. It's the reference pattern the other
four specialists' job-gates can copy.

Not yet: the rest of the inline media (palette swatches, storyboard thumbnails).

## Run it

From the `atlas/` directory, with the shared root venv active:

```bash
source ../venv/bin/activate          # the shared root venv
pip install -r requirements-web.txt   # one-time: installs chainlit (free/MIT)
chainlit run web/app.py -w            # -> http://localhost:8000
```

`-w` auto-reloads on file changes (handy while developing; drop it otherwise).
Open <http://localhost:8000>. Atlas's brain is your Claude Code subscription (no API
key), exactly like the terminal.

### Over SSH (if you ever run it on a remote box)

It's a localhost web server, so port-forward and open it locally:

```bash
ssh -L 8000:localhost:8000 you@host
# then on the host:
cd atlas && chainlit run web/app.py --headless
# open http://localhost:8000 on your laptop
```

## Important: shared memory with the terminal

The web UI and the terminal REPL share **one** `chat_state.json` (Atlas's distilled
summary). Writes are atomic so a file can't corrupt, but it's last-writer-wins
logically: **don't run the web UI and the terminal Atlas at the same time** against
the same memory. Single operator, one frontend at a time. (A startup lock is a
possible later nicety; for v1 this is by convention.)

## Dependencies note (not Ollama)

Chainlit → `literalai` → `traceloop-sdk` transitively installs a suite of
`opentelemetry-instrumentation-*` packages, one of which is
`opentelemetry-instrumentation-ollama`. That's a passive OpenTelemetry **shim**, not
the Ollama runtime — `literalai` hard-imports traceloop at module top level, so the
suite can't be removed without breaking `import chainlit`, but the ollama shim is
**dormant** (verified: `import chainlit` loads zero ollama modules). Nothing here runs
or depends on Ollama; the fleet's no-Ollama policy is intact.

## How it stays out of the way

This frontend imports `session.py` and reads the registry; it does **not** touch the
contracts, `pipeline.py`, the gate logic in `tools.py`, the orchestrator's
engine/prompt, or any agent's engine/persona/memory. It's a viewer/driver only.

The streaming bridge (why it stays responsive through multi-minute production runs):
`session.send()` is synchronous and the orchestrator calls `asyncio.run()` inside it,
so it runs in a worker thread via `cl.make_async`; its `on_text` / `on_status`
callbacks marshal each event back to the main event loop with
`loop.call_soon_threadsafe` onto a queue that's drained in Chainlit's own context.
Verified against chainlit 2.11.1.
