"""Generate the orchestrator's SDK tools FROM the registry.

For every registry entry we emit:
- one tool per JobSpec   -> e.g. `scout_find_topics`, `sage_research`
- one persona tool       -> e.g. `ask_scout`, `ask_sage`   (if entry.persona)

Because the tools are derived from the registry, a future agent's tools appear the
moment its entry exists — the orchestrator never changes.

Two review-hardened guarantees live HERE, at the tool boundary:
- ERROR CONTAINMENT (never crash the meeting): every job/persona call is wrapped in
  try/except and ALWAYS returns readable text. A sibling exception becomes a tool
  result the orchestrator can narrate, not an unhandled error that kills the REPL.
- JOB TIMEOUT (no silent hang): the sibling engine's `chat()` path has no timeout,
  so we bound each call with `asyncio.wait_for`. A stalled job is reported and the
  meeting continues. (The worker thread can't be force-killed; we abandon it and
  move on — acceptable because jobs run sequentially.)

The synchronous sibling engines spin their OWN event loop (`asyncio.run` inside
`llm.chat`), so we dispatch them with `asyncio.to_thread` — the worker thread has no
running loop, so the sibling's loop is created cleanly with no nesting. (Proven by
probe; asserted by tests.)
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
import sys

# Atlas's modules import each other by bare name (`import projects`, `import boundary`),
# including LAZILY inside tool bodies. Guarantee atlas/ is importable no matter how this
# process was launched (terminal, chainlit, an MCP host, a test) — otherwise a tool call
# can fail with a raw "No module named 'projects'" at call time even though startup
# succeeded. Mirrors the bootstrap web/app.py does; cheap and idempotent.
_ATLAS_DIR = str(pathlib.Path(__file__).resolve().parent)
if _ATLAS_DIR not in sys.path:
    sys.path.insert(0, _ATLAS_DIR)

from claude_agent_sdk import create_sdk_mcp_server, tool

import agency
import boundary
# Imported at MODULE level (not lazily inside tool bodies) ON PURPOSE: resolved ONCE
# here, right after the bootstrap above guarantees atlas/ is importable, then cached in
# sys.modules for the process's life. A call-time `import projects` re-runs the path
# lookup every invocation, so if anything mutates sys.path after startup (a reloader, a
# sibling engine, a fresh-path thread) the tool fails with "No module named 'projects'"
# even though startup succeeded — the live bug Atlas caught. A reference to an
# already-imported module can't fail that way.
import chat_state
import compliance
import contracts
import projects
import studio_bridge

SERVER_NAME = "atlas"
ASK_TIMEOUT = 150   # seconds for a single-turn persona reply

# The Agent SDK builtin tools Atlas is allowed to call directly — web research for
# niche/trend/RPM/policy work. Enabled in the orchestrator's options (tools=...).
BUILTIN_TOOLS = ["WebSearch", "WebFetch"]

# Permanent instrumentation: start_project logs the slug it minted for each video, so
# "Atlas started a weird project" is a one-line log check instead of a transcript hunt.
log = logging.getLogger("atlas.tools")


def configure_logging(path=None):
    """Route the `atlas` logger's INFO records to a FILE (never stdout, so the
    interactive meeting stays clean). Idempotent; call once at chat startup.

    Without this, the permanent start_project arg-logging goes nowhere — Python drops
    sub-WARNING records when no handler is configured. Configuring the parent `atlas`
    logger captures every `atlas.*` child (e.g. `atlas.tools`) via propagation.
    """
    import pathlib
    logger = logging.getLogger("atlas")
    if any(getattr(h, "_atlas_file", False) for h in logger.handlers):
        return logger  # already wired — don't stack duplicate handlers
    path = pathlib.Path(path) if path else (pathlib.Path(__file__).parent / "atlas.log")
    handler = logging.FileHandler(path)
    handler._atlas_file = True
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # keep it off the root/stdout chain
    return logger


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _contain(label: str, handler):
    """Wrap a builtin orchestration tool's handler so ANY unhandled exception becomes
    readable text instead of a raw error surfaced to Atlas. The job/persona tools
    already contain their own errors (so a sibling crash never kills the meeting); this
    extends the same guarantee to the orchestration tools (project status, deletes, the
    agency tools) — a missing import or a filesystem error is narrated, not raw."""
    async def _wrapped(args):
        try:
            return await handler(args)
        except Exception as exc:  # noqa: BLE001 — containment is the whole point
            log.exception("tool %s failed", label)
            return _ok(f"{label} failed: {exc.__class__.__name__}: {exc}. The tool did "
                       "not complete — tell the CEO and continue; don't retry blindly.")
    return _wrapped


def _make_job_tool(adapter, job):
    """Build one async SDK tool that runs `job` on `adapter`, safely + bounded.

    Every job tool carries a uniform `slug` param (the active project from
    `start_project`): the producer jobs read their upstream artifact(s) from
    `projects/<slug>/` and write their output there, so a sequence of delegations
    accumulates ONE video. Intake/standard/coach jobs (Scout, Vera, the coaches) don't
    need a project and simply ignore the slug.
    """
    who = adapter.entry.display

    # A full JSON Schema so we can add `slug` alongside the job's domain params without
    # the SDK force-marking everything required (the {name: type} form does that).
    properties = {name: {"type": "string"} for name in job.params}
    properties["slug"] = {
        "type": "string",
        "description": ("The active project slug from start_project. Production jobs read "
                        "upstream artifacts from projects/<slug>/ and write their output "
                        "there. Omit only for intake/standards/coaching jobs."),
    }
    schema = {"type": "object", "properties": properties,
              "required": list(job.params)}

    async def _fn(args):
        params = {k: (args.get(k) or "") for k in job.params}
        params["slug"] = args.get("slug") or ""
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(adapter.run_job, job.name, adapter.progress, **params),
                timeout=job.timeout)
        except asyncio.TimeoutError:
            adapter.progress.fail(who, f"timed out after {job.timeout}s")
            return _ok(f"{who} timed out after {job.timeout}s (left running in the "
                       "background). Continue without its result, or try again later.")
        except Exception as exc:  # noqa: BLE001 — containment is the whole point
            adapter.progress.fail(who, str(exc))
            return _ok(f"{who} failed: {exc}. The job did not complete — report this "
                       "to the CEO and continue or pause; do not retry blindly.")
        return _ok(result.get("text", "(no result)"))

    return tool(job.tool, job.description, schema)(_fn)


def _make_ask_tool(adapter):
    """Build the persona `ask_<name>` tool for an agent."""
    entry = adapter.entry
    who = entry.display
    name = f"ask_{entry.name}"
    desc = (f"Ask {who} a question directly and get their in-character take "
            f"({entry.blurb.rstrip('.')}.). Use for opinions/direct address, NOT to "
            f"run a job. 'context' is optional background from the meeting.")

    async def _fn(args):
        question = (args.get("question") or "").strip()
        context = (args.get("context") or "").strip()
        if not question:
            return _ok(f"(No question was provided for {who}.)")
        try:
            reply = await asyncio.wait_for(
                asyncio.to_thread(adapter.ask, question, context), timeout=ASK_TIMEOUT)
        except asyncio.TimeoutError:
            return _ok(f"{who} didn't respond within {ASK_TIMEOUT}s — likely a "
                       "rate-limit. Continue without their input for now.")
        except Exception as exc:  # noqa: BLE001
            return _ok(f"Couldn't reach {who}: {exc}. Continue without their input.")
        return _ok(f"{who} says: {reply}")

    return tool(name, desc, {"question": str, "context": str})(_fn)


def _produce_report(slug: str, state: dict) -> str:
    """Narrate a studio run's returned state for the CEO, and — at the final gate —
    file the CEO approval checkpoint so the sign-off lands in the governance queue too."""
    status = state.get("status")
    digest = studio_bridge.status_digest(state)
    if status == "complete":
        video = (state.get("artifacts") or {}).get("video")
        return (f"✅ Production complete — '{slug}'.\n{digest}\n"
                f"Final video: {video}")
    if status == "blocked_at_factcheck":
        g = (state.get("gates") or {}).get("factcheck", {})
        return (f"⛔ FACT-CHECK BLOCKED — '{slug}'. This is the hard gate; I can't sign "
                f"off a block.\n{digest}\nFlagged: {g.get('details', '(see report)')}\n"
                "The script must be revised on the flagged claims, then re-checked — "
                "call approve_gate with gate='factcheck' to re-run the check.")
    if status == "awaiting_final_gate":
        g = (state.get("gates") or {}).get("final", {})
        det = g.get("details", {}) if isinstance(g.get("details"), dict) else {}
        try:
            boundary.request_from_ceo(
                "approval", f"Approve final render of '{slug}'",
                (f"Studio paused at the final gate — motion_ok={det.get('motion_ok')}, "
                 f"review_ok={det.get('review_ok')}, under_budget={det.get('under_budget')}."),
                f"Say the word and I'll run approve_gate('{slug}').")
        except Exception:  # noqa: BLE001 — the checkpoint is best-effort, never fatal
            pass
        return (f"⏸ FINAL GATE — '{slug}' is ready for your sign-off before the final "
                f"render.\n{digest}\nReason: {g.get('reason', 'awaiting approval')}\n"
                "Filed a CEO approval request. Say yes and I'll approve_gate it.")
    if status == "blocked_at_gate":
        g = (state.get("gates") or {}).get("final", {})
        return (f"⛔ QUALITY GATE BLOCKED — '{slug}' (not approvable).\n{digest}\n"
                f"{g.get('details', '')}")
    if status == "render_failed":
        return f"⚠️ Render failed — '{slug}'.\n{digest}"
    return f"Production '{slug}' — status {status}.\n{digest}"


def _make_produce_tool():
    """Produce a video end-to-end through the studio v2 spine — the ONE production path.
    Replaces the legacy start_project + hand-called sibling chain."""
    desc = ("PRODUCE A VIDEO end to end through the studio spine — the ONE way to make a "
            "video. Pass 'topic' (required), optional 'angle', 'channel' (main|explainer; "
            "default main), 'pack', 'voice'. Studio runs research → script → factcheck★ → "
            "storyboard → vo → compose → draft → review → final★ → video.mp4, PAUSING at "
            "its two gates. Returns the run status + slug. On 'awaiting_final_gate' it "
            "files a CEO approval request — call approve_gate after the CEO says yes. On "
            "'blocked_at_factcheck' the script must be revised before it can re-check. "
            "Use project_status to check a run; channel defaults to 'main'.")
    schema = {"type": "object",
              "properties": {
                  "topic": {"type": "string", "description": "The video topic (required)."},
                  "angle": {"type": "string", "description": "Optional focusing angle."},
                  "channel": {"type": "string",
                              "description": "Channel preset: main|explainer (default main)."},
                  "pack": {"type": "string", "description": "Design pack override (optional)."},
                  "voice": {"type": "string", "description": "VO voice override (optional)."}},
              "required": ["topic"]}

    async def _fn(args):
        topic = (args.get("topic") or "").strip()
        if not topic:
            return _ok("Give me a 'topic' to produce.")
        if boundary.kill_switch_active():
            return _ok("Refused: the CEO's STOP kill-switch is set (ceo/STOP). I won't "
                       "start a production while it's active.")
        try:
            slug, state = await asyncio.to_thread(
                studio_bridge.start, topic,
                angle=(args.get("angle") or None),
                channel=(args.get("channel") or None),
                pack=(args.get("pack") or None),
                voice=(args.get("voice") or None))
        except Exception as exc:  # noqa: BLE001 — never crash the meeting
            return _ok(f"Couldn't start the production: {exc.__class__.__name__}: {exc}")
        log.info("produce: slug=%r topic=%r status=%r", slug, topic, state.get("status"))
        return _ok(_produce_report(slug, state))

    return tool("produce", desc, schema)(_fn)


def _make_approve_gate_tool():
    """Approve a paused studio gate and resume the run — the CEO's go-ahead, applied."""
    desc = ("Approve a paused studio gate and RESUME the run. Pass 'slug' and optional "
            "'gate' (final|factcheck; default final). gate='final' renders the final cut "
            "→ video.mp4 (get the CEO's go-ahead first). gate='factcheck' RE-RUNS the "
            "fact-check on a revised script — a block is never approved away, it must "
            "re-earn a pass. Honours the CEO STOP kill-switch.")
    schema = {"type": "object",
              "properties": {
                  "slug": {"type": "string", "description": "The production slug to resume."},
                  "gate": {"type": "string",
                           "description": "Which gate to approve: final|factcheck (default final)."}},
              "required": ["slug"]}

    async def _fn(args):
        slug = (args.get("slug") or "").strip()
        gate = (args.get("gate") or "final").strip().lower()
        if not slug:
            return _ok("Give me the 'slug' to approve (list runs with project_status).")
        if gate not in ("final", "factcheck"):
            return _ok("gate must be 'final' or 'factcheck'.")
        if boundary.kill_switch_active():
            return _ok("Refused: the CEO's STOP kill-switch is set (ceo/STOP).")
        try:
            state = await asyncio.to_thread(studio_bridge.resume, slug, approve={gate})
        except ValueError as exc:
            return _ok(f"Can't resume: {exc}")
        except Exception as exc:  # noqa: BLE001
            return _ok(f"Resume failed for '{slug}': {exc.__class__.__name__}: {exc}")
        log.info("approve_gate: slug=%r gate=%r -> status=%r", slug, gate,
                 state.get("status"))
        return _ok(_produce_report(slug, state))

    return tool("approve_gate", desc, schema)(_fn)


def _make_project_status_tool():
    """Read a project's checklist so Atlas knows what's produced and can resume."""
    desc = ("Read a studio production's live status: which stages are done, the gate "
            "state (factcheck★ / final★), and the final video path if ready. Pass "
            "'slug'. Omit it to LIST all productions (newest first) with their status.")
    schema = {"type": "object",
              "properties": {"slug": {"type": "string",
                                      "description": "The production slug (omit to list all)."}},
              "required": []}

    async def _fn(args):
        slug = (args.get("slug") or "").strip()
        if not slug:
            items = await asyncio.to_thread(studio_bridge.list_projects)
            if not items:
                return _ok("No productions yet. Start one with produce.")
            lines = ["Productions (newest first):"]
            lines += [f"  · {p['slug']} — {p['topic']} [{p['status']}]"
                      for p in items[:30]]
            return _ok("\n".join(lines))
        state = await asyncio.to_thread(studio_bridge.read_state, slug)
        if state is None:
            return _ok(f"No production named {slug!r}. List them with project_status.")
        return _ok(studio_bridge.status_digest(state))

    return tool("project_status", desc, schema)(_fn)


def _make_delete_project_tool():
    """PERMANENTLY delete one project workspace. Destructive + irreversible, so it is
    fenced by the boundary's delete door (PROJECT tier only) and honours the CEO's
    STOP kill-switch. Atlas lists with project_status, the CEO reviews, then Atlas
    deletes the approved slugs one at a time."""
    desc = ("PERMANENTLY delete ONE production's workspace (its studio/projects/<slug>/ "
            "tree and every artifact in it). Irreversible. Pass the exact 'slug' (use "
            "project_status first to list slugs, and get the CEO's go-ahead before "
            "deleting). Deletes one production per call — there is no bulk delete. Only "
            "production workspaces can be deleted; anything else is refused.")
    schema = {"type": "object",
              "properties": {"slug": {"type": "string",
                                      "description": "The exact production slug to delete."}},
              "required": ["slug"]}

    async def _fn(args):
        slug = (args.get("slug") or "").strip()
        if not slug:
            return _ok("Give me the exact 'slug' to delete (list them with "
                       "project_status first).")
        if boundary.kill_switch_active():
            return _ok("Refused: the CEO's STOP kill-switch is set (ceo/STOP). I won't "
                       "delete anything while it's active.")
        try:
            res = await asyncio.to_thread(studio_bridge.delete, slug)
        except boundary.WriteBoundaryError as exc:
            return _ok(f"Refused: {exc}")
        if not res["deleted"]:
            return _ok(f"No production named {slug!r} — nothing deleted. (List with "
                       "project_status.)")
        return _ok(f"Deleted production {slug!r} ({res['path']}). This is permanent.")

    return tool("delete_project", desc, schema)(_fn)


def _make_validate_artifact_tool():
    """Optional sanity-check of one produced artifact against its frozen contract."""
    desc = ("Optionally sanity-check one produced artifact against its frozen schema "
            "(e.g. 'script', 'research_brief', 'style_guide', 'storyboard', "
            "'asset_manifest', 'factcheck_report', 'composition_manifest', "
            "'audio_manifest'). Pass 'name' and the project 'slug'. Returns OK or the "
            "validation errors. This is a tool you MAY use — it is not a required gate.")
    schema = {"type": "object",
              "properties": {
                  "name": {"type": "string", "description": "The artifact/contract name."},
                  "slug": {"type": "string", "description": "The project slug."}},
              "required": ["name", "slug"]}

    # artifact/contract name -> the file it lives in under projects/<slug>/
    _FILES = {
        "research_brief": "research_brief.json",
        "script": "script.json",
        "factcheck_report": "factcheck_report.json",
        "creative_treatment": "creative_treatment.json",
        "narrative_intent": "narrative_intent.json",
        "motion_mood_board": "motion_mood_board.json",
        "style_guide": "style_guide.json",
        "storyboard": "storyboard.json",
        "asset_manifest": "asset_manifest.json",
        "narration_transcript": "narration.transcript.json",
        "composition_manifest": "composition_manifest.json",
        "audio_manifest": "audio/audio_manifest.json",
    }

    async def _fn(args):
        name = (args.get("name") or "").strip()
        slug = (args.get("slug") or "").strip()
        pdir = projects.project_dir(slug)
        if pdir is None:
            return _ok(f"No project named {slug!r}. Start one with start_project.")
        rel = _FILES.get(name, f"{name}.json")
        data = chat_state.load_json(pdir / rel, None)
        if data is None:
            return _ok(f"No '{name}' artifact found at {pdir / rel} yet.")
        ok, errors = contracts.validate(name, data)
        if ok:
            return _ok(f"'{name}' is valid against its contract. ✓")
        return _ok(f"'{name}' FAILS its contract: {'; '.join(errors)}")

    return tool("validate_artifact", desc, schema)(_fn)


def _make_read_repo_tool():
    """READ-ONLY file read, jailed to the repo root. Atlas reads its own and its
    agents' code/config to reason about the studio — never outside the repo."""
    desc = ("Read a file from THIS repository (read-only). Pass an absolute or "
            "repo-relative 'path'. Use it to inspect your own or a teammate's code, "
            "config, persona, or a project artifact. Paths outside the repo are refused.")
    schema = {"type": "object",
              "properties": {"path": {"type": "string",
                                      "description": "Path to a file inside the repo."}},
              "required": ["path"]}

    async def _fn(args):
        path = (args.get("path") or "").strip()
        if not path:
            return _ok("Give me a 'path' to read.")
        try:
            text = boundary.read_repo(path)
        except boundary.ReadBoundaryError:
            return _ok(f"Refused: {path} is outside the repo. read_repo only reads "
                       "files inside this repository.")
        except (FileNotFoundError, IsADirectoryError, OSError) as exc:
            return _ok(f"Couldn't read {path}: {exc}")
        return _ok(text)

    return tool("read_repo", desc, schema)(_fn)


def _make_list_dir_tool():
    """READ-ONLY directory listing, jailed to the repo root — the companion to
    read_repo (which only reads file contents). This is how Atlas browses the project
    file structure: `list_dir projects` for every project, `list_dir projects/<slug>
    recursive=true` for one video's whole tree."""
    desc = ("List the contents of a DIRECTORY in this repository (read-only). Pass a "
            "repo-relative or absolute 'path' — e.g. 'projects' to see every project "
            "folder, or 'projects/<slug>' to see one video's files. Set 'recursive' to "
            "'true' to walk the entire subtree (build/cache dirs are skipped). Paths "
            "outside the repo are refused. Use read_repo to read a file's CONTENTS.")
    schema = {"type": "object",
              "properties": {
                  "path": {"type": "string",
                           "description": "Directory to list (repo-relative is fine)."},
                  "recursive": {"type": "string",
                                "description": "'true' to list the full subtree."}},
              "required": ["path"]}

    async def _fn(args):
        path = (args.get("path") or "").strip()
        if not path:
            return _ok("Give me a 'path' to list (e.g. 'projects').")
        recursive = str(args.get("recursive") or "").strip().lower() in (
            "1", "true", "yes", "y")
        try:
            entries, truncated = boundary.list_dir(path, recursive=recursive)
        except boundary.ReadBoundaryError:
            return _ok(f"Refused: {path} is outside the repo. list_dir only lists "
                       "directories inside this repository.")
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            return _ok(f"Couldn't list {path}: {exc}")
        if not entries:
            return _ok(f"{path} is empty.")
        n = len(entries)
        head = (f"{path} ({n} entr{'y' if n == 1 else 'ies'}"
                f"{', truncated — narrow the path' if truncated else ''}):")
        return _ok(head + "\n" + "\n".join(f"  {e}" for e in entries))

    return tool("list_dir", desc, schema)(_fn)


def _make_write_file_tool():
    """SCOPED + TIERED write. Every path is classified by the structural boundary;
    only soft-tier persona/playbook text, per-project artifacts, and the incubator
    are writable. Core spine + secrets are propose-only and physically refused."""
    desc = ("Write a file, ENFORCED by the studio's write boundary. Allowed: soft-tier "
            "persona/prompt/playbook .md and soul/ files; project artifacts under "
            "projects/<slug>/; the agents-incubator/. REFUSED (propose-only): the core "
            "spine (orchestrator/registry/tools/llm/rubric/contracts) and anything "
            "secret (.env, keys). Pass 'path' and 'content'.")
    schema = {"type": "object",
              "properties": {
                  "path": {"type": "string", "description": "Destination path in the repo."},
                  "content": {"type": "string", "description": "Full file contents."}},
              "required": ["path", "content"]}

    async def _fn(args):
        path = (args.get("path") or "").strip()
        content = args.get("content") or ""
        if not path:
            return _ok("Give me a 'path' to write.")
        try:
            written = boundary.guarded_write(path, content)
        except boundary.WriteBoundaryError as exc:
            tier = boundary.classify(path, content)
            return _ok(f"Refused ({tier}): {exc}. This is propose-only — describe the "
                       "change to the CEO instead of writing it.")
        tier = boundary.classify(path, content)
        return _ok(f"Wrote {written} ({tier}).")

    return tool("write_file", desc, schema)(_fn)


def _make_request_from_ceo_tool():
    """Escalate a concrete need UP to the CEO (and queue it). Never a hard block —
    if the CEO can't provide it, Atlas finds a legal alternative."""
    desc = ("Ask the CEO for something you can't get yourself — an API key, an asset, "
            "an approval, info, or budget. Logs the ask to ceo/requests.jsonl and "
            "returns the message the CEO sees. 'kind' is one of api_key|asset|approval|"
            "info|budget. Never block on the answer: if they decline, find a legal "
            "alternative.")
    schema = {"type": "object",
              "properties": {
                  "kind": {"type": "string", "enum": list(boundary.REQUEST_KINDS),
                           "description": "api_key | asset | approval | info | budget"},
                  "what": {"type": "string", "description": "What you need, concretely."},
                  "why": {"type": "string", "description": "Why the work needs it."},
                  "how_to_provide": {"type": "string",
                                     "description": "How the CEO can provide it."}},
              "required": ["kind", "what", "why", "how_to_provide"]}

    async def _fn(args):
        try:
            res = boundary.request_from_ceo(
                (args.get("kind") or "").strip(), (args.get("what") or "").strip(),
                (args.get("why") or "").strip(), (args.get("how_to_provide") or "").strip())
        except ValueError as exc:
            return _ok(f"Couldn't file the request: {exc}")
        return _ok(res["message"])

    return tool("request_from_ceo", desc, schema)(_fn)


def _make_ceo_log_tool():
    """Append a line to the CEO's append-only journal (decisions, milestones)."""
    desc = ("Append one line to the CEO journal (ceo/journal.jsonl) — a decision, a "
            "milestone, or a note worth keeping across sessions. Pass 'entry'.")
    schema = {"type": "object",
              "properties": {"entry": {"type": "string",
                                       "description": "The line to journal."}},
              "required": ["entry"]}

    async def _fn(args):
        entry = (args.get("entry") or "").strip()
        if not entry:
            return _ok("Give me an 'entry' to journal.")
        boundary.ceo_log(entry)
        return _ok("Logged to ceo/journal.jsonl.")

    return tool("ceo_log", desc, schema)(_fn)


def _make_improve_agent_tool():
    """SOFT-tier persona/prompt edit of an existing teammate, then re-validate."""
    desc = ("Improve an existing teammate by rewriting one of its SOFT-tier "
            "persona/prompt files (e.g. soul/SOUL.md, soul/STYLE.md, SKILL.md), then "
            "re-validate its persona. Pass 'name' (the agent handle), 'file' (the "
            "persona/prompt file, relative to the agent's dir), and 'content'. Only "
            "soft voice/prompt files are allowed — code/contracts/secrets are refused.")
    schema = {"type": "object",
              "properties": {
                  "name": {"type": "string", "description": "Agent handle, e.g. 'scout'."},
                  "file": {"type": "string", "description": "Persona/prompt file path."},
                  "content": {"type": "string", "description": "Full new file contents."}},
              "required": ["name", "file", "content"]}

    async def _fn(args):
        try:
            res = await asyncio.to_thread(
                agency.improve_agent, (args.get("name") or "").strip(),
                (args.get("file") or "").strip(), args.get("content") or "")
        except agency.AgentError as exc:
            return _ok(f"Can't improve that agent: {exc}")
        except boundary.WriteBoundaryError as exc:
            return _ok(f"Refused: {exc}. improve_agent only edits soft persona/prompt "
                       "text — propose a code change to the CEO instead.")
        v = res["validation"]
        return _ok(f"Improved {res['agent']} ({res['file']}). Wrote {res['written']}. "
                   f"Persona re-validated: {'ok' if v['ok'] else 'WEAK'} "
                   f"(soul {v['soul_chars']} chars, prompt {v['prompt_chars']} chars).")

    return tool("improve_agent", desc, schema)(_fn)


def _make_propose_agent_tool():
    """Scaffold a NEW agent into the incubator + file a CEO promotion request."""
    desc = ("Propose a NEW agent: scaffold SOUL.md + STYLE.md + a minimal engine + a "
            "PROPOSED registry patch into agents-incubator/<name>/, smoke-test it in "
            "isolation, and file a CEO approval request to PROMOTE it. Pass 'name' "
            "(lowercase handle), 'role', and 'spec' (what it does). You CANNOT edit "
            "registry.py — promotion is a human/CORE change; you only propose it.")
    schema = {"type": "object",
              "properties": {
                  "name": {"type": "string", "description": "Lowercase handle, e.g. 'glint'."},
                  "role": {"type": "string", "description": "Its production role/title."},
                  "spec": {"type": "string", "description": "What the new agent does."}},
              "required": ["name", "role", "spec"]}

    async def _fn(args):
        try:
            res = await asyncio.to_thread(
                agency.propose_agent, (args.get("name") or "").strip(),
                (args.get("role") or "").strip(), (args.get("spec") or "").strip())
        except agency.AgentError as exc:
            return _ok(f"Can't propose that agent: {exc}")
        smoke = "passed" if res["smoke"]["ok"] else f"FAILED ({res['smoke'].get('error')})"
        return _ok(f"Scaffolded '{res['agent']}' in {res['dir']} (smoke test: {smoke}). "
                   f"Filed an approval request for the CEO to promote it — registry.py "
                   f"untouched.\n\n{res['promotion_message']}")

    return tool("propose_agent", desc, schema)(_fn)


def _make_run_self_eval_tool():
    """Measure a finished video and optionally apply ONE soft improvement."""
    desc = ("Self-evaluate a finished video: measure project <slug> against the "
            "CEO-owned rubric and report the scorecard + the single best soft-tier "
            "improvement target. Set 'apply' to 'true' to also persist that ONE soft "
            "improvement through the guarded loop (the rubric stays read-only). Set "
            "'judged' to 'true' for the (costly) LLM-judge pass. Pass 'slug'.")
    schema = {"type": "object",
              "properties": {
                  "slug": {"type": "string", "description": "The project slug."},
                  "apply": {"type": "string", "description": "'true' to apply one soft tweak."},
                  "judged": {"type": "string", "description": "'true' to run the LLM judge."}},
              "required": ["slug"]}

    def _truthy(v):
        return str(v or "").strip().lower() in ("1", "true", "yes", "y")

    async def _fn(args):
        slug = (args.get("slug") or "").strip()
        try:
            res = await asyncio.to_thread(
                agency.run_self_eval, slug, apply=_truthy(args.get("apply")),
                judged=_truthy(args.get("judged")))
        except agency.AgentError as exc:
            return _ok(f"Can't self-eval: {exc}")
        except Exception as exc:  # noqa: BLE001 — eval never crashes the meeting
            return _ok(f"Self-eval of {slug!r} failed: {exc}. Continue or retry.")
        lines = [f"Self-eval of {slug!r}: overall {res['overall']}, "
                 f"quality_score {res['quality_score']}."]
        if res["target"]:
            lines.append(f"Top soft-tier target: {res['target']['band_id']} "
                         f"(value {res['target']['measured_value']}).")
        else:
            lines.append("No clean soft-tier improvement target — nothing to tune.")
        if res["applied"]:
            lines.append(f"Applied a soft tweak → {res['applied']['soft_path']}.")
        lines.append(f"Rubric stayed read-only: {res['rubric_read_only']}.")
        return _ok(" ".join(lines))

    return tool("run_self_eval", desc, schema)(_fn)


def _make_check_compliance_tool():
    """Run the pre-publish compliance gate on a project and return the report."""
    desc = ("Run the pre-publish COMPLIANCE GATE on project <slug>: license manifest "
            "(CC0/PD/CC-BY/CC-BY-SA + attribution + local file), no real-person "
            "likeness, fact-check passed, music/SFX licensed, advertiser-friendly + "
            "originality. Returns a human-readable PASS/BLOCKED report. Read-only.")
    schema = {"type": "object",
              "properties": {"slug": {"type": "string", "description": "The project slug."}},
              "required": ["slug"]}

    async def _fn(args):
        slug = (args.get("slug") or "").strip()
        pdir = projects.project_dir(slug)
        if pdir is None:
            return _ok(f"No project named {slug!r}.")
        rep = await asyncio.to_thread(compliance.check, pdir)
        return _ok(compliance.format_report(rep))

    return tool("check_compliance", desc, schema)(_fn)


def _make_youtube_upload_tool():
    """Gate, then upload UNLISTED + ask the board to approve going public. No auto-publish."""
    desc = ("Prepare a GATED publish for project <slug>: run the compliance gate, and "
            "ONLY if it passes, upload the video to YouTube as UNLISTED (never public), "
            "write the compliance report, and file a board approval request to go "
            "public. A blocked video is NOT uploaded. Publishing public always needs "
            "the human's yes — this tool never makes anything public.")
    schema = {"type": "object",
              "properties": {"slug": {"type": "string", "description": "The project slug."}},
              "required": ["slug"]}

    async def _fn(args):
        import publish
        slug = (args.get("slug") or "").strip()
        try:
            res = await asyncio.to_thread(publish.prepare_publish, slug)
        except publish.PublishError as exc:
            return _ok(f"Can't publish: {exc}")
        if not res["passed"]:
            reasons = "\n  ".join(res["reasons"][:8])
            return _ok(f"⛔ '{slug}' BLOCKED by compliance — NOT uploaded:\n  {reasons}\n"
                       f"Full report: {res['report_path']}. Fix and re-run.")
        state = "uploaded" if res["uploaded"] else "prepared (awaiting OAuth creds)"
        return _ok(f"✅ '{slug}' passed compliance and is {state} as {res['privacy']} "
                   f"({res['video_id']}). Filed a board approval to go PUBLIC — nothing "
                   f"goes live without your yes. Report: {res['report_path']}.")

    return tool("youtube_upload", desc, schema)(_fn)


def _make_youtube_analytics_tool():
    """Pull a published video's performance into ceo/state.json (strategy adapts)."""
    desc = ("Fetch a published video's performance (views, watch-time, RPM, est. "
            "revenue) for project <slug> and feed it into the business state so "
            "strategy adapts to what actually earns. Needs the video to be uploaded.")
    schema = {"type": "object",
              "properties": {"slug": {"type": "string", "description": "The project slug."}},
              "required": ["slug"]}

    async def _fn(args):
        import publish
        slug = (args.get("slug") or "").strip()
        try:
            m = await asyncio.to_thread(publish.ingest_analytics, slug)
        except publish.PublishError as exc:
            return _ok(f"No analytics yet: {exc}")
        except Exception as exc:  # noqa: BLE001
            return _ok(f"Couldn't fetch analytics for {slug!r}: {exc}.")
        return _ok(f"'{slug}': {m.get('views')} views, {m.get('watch_time_min')} watch-min, "
                   f"RPM ${m.get('rpm_usd')}, ~${m.get('estimated_revenue_usd')} est. "
                   "Folded into ceo/state.json; strategy follows the data.")

    return tool("youtube_analytics", desc, schema)(_fn)


def build_server(adapters: dict, progress):
    """Build the in-process MCP server + the list of allowed tool names.

    `adapters` is {name: Adapter}. `progress` is the run's status sink; we attach it
    to each adapter so the generated tools emit deterministic lines as they run.
    Returns (server, allowed_tool_names).
    """
    sdk_tools = []
    allowed: list[str] = []
    for entry_name, adapter in adapters.items():
        adapter.progress = progress  # the run's status sink, used inside run_job
        if getattr(adapter.entry, "retired", False):
            continue  # studio owns this role now — no job/persona tools for Atlas (1A)
        for job in adapter.entry.jobs:
            sdk_tools.append(_make_job_tool(adapter, job))
            allowed.append(f"mcp__{SERVER_NAME}__{job.tool}")
        if adapter.entry.persona:
            sdk_tools.append(_make_ask_tool(adapter))
            allowed.append(f"mcp__{SERVER_NAME}__ask_{adapter.entry.name}")

    # The non-registry orchestration tools: project workspace + checklist + optional
    # contract check. Atlas runs the production PLAYBOOK by calling the agent job tools
    # in sequence against one slug; these manage the workspace it accumulates into.
    for name, make in (# The ONE production path: studio's resumable spine + its gates.
                       ("produce", _make_produce_tool),
                       ("approve_gate", _make_approve_gate_tool),
                       ("project_status", _make_project_status_tool),
                       ("delete_project", _make_delete_project_tool),
                       # Atlas's agency tools, fenced by the structural write boundary.
                       ("read_repo", _make_read_repo_tool),
                       ("list_dir", _make_list_dir_tool),
                       ("write_file", _make_write_file_tool),
                       ("request_from_ceo", _make_request_from_ceo_tool),
                       ("ceo_log", _make_ceo_log_tool),
                       # Atlas improving + creating agents, and self-eval.
                       ("improve_agent", _make_improve_agent_tool),
                       ("propose_agent", _make_propose_agent_tool),
                       ("run_self_eval", _make_run_self_eval_tool),
                       # Close the loop to GATED publishing.
                       ("check_compliance", _make_check_compliance_tool),
                       ("youtube_upload", _make_youtube_upload_tool),
                       ("youtube_analytics", _make_youtube_analytics_tool)):
        built = make()
        # Same error-containment guarantee the job/persona tools have: an unhandled
        # exception (e.g. a bad import or a filesystem error) becomes readable text the
        # meeting can narrate, never a raw error that kills the REPL.
        built.handler = _contain(name, built.handler)
        sdk_tools.append(built)
        allowed.append(f"mcp__{SERVER_NAME}__{name}")

    server = create_sdk_mcp_server(SERVER_NAME, tools=sdk_tools)
    return server, allowed
