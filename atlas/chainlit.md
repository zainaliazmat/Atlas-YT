# Atlas — the meeting room (web)

You're the CEO. Talk to **Atlas**, your chief-of-staff, and it runs the studio's
team of agents — finding topics, researching, and (when you ask) producing a video
end to end.

This is the same Atlas that runs in the terminal (`python run.py chat`), now with a
live, streaming view: you'll see Atlas's decisions (the **🧠** lines) stream as it
thinks, and the deterministic **🔎 / ✅** status lines as each teammate works.

**Commands**

- `/agents` — who's on the team and what each does
- `/summary` — distill the meeting so far and show what Atlas remembers
- `/new` — fold this meeting into memory and start a fresh thread
- `/help` — show the commands

Closing the tab automatically saves (distills) the meeting — nothing is lost.

When a production run pauses at a **gate**, you'll see the artifact inline (the
fact-check report + script, or the render plan + playable draft renders) with
**Approve** / **Revise** buttons — Approve resumes the pipeline directly; Revise sends
it back to Atlas to fix.

> The roster sidebar, per-agent chat, and the rest of the inline media (palette
> swatches, storyboard thumbnails) arrive in the following phases.
