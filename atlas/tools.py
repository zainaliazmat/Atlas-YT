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

from claude_agent_sdk import create_sdk_mcp_server, tool

SERVER_NAME = "atlas"
ASK_TIMEOUT = 150   # seconds for a single-turn persona reply

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


def _make_start_project_tool():
    """Mint a new project workspace + checklist manifest, returning the slug Atlas
    threads through every subsequent job for this video."""
    desc = ("Start a NEW video: create its project workspace and lightweight checklist "
            "manifest, and return the 'slug'. Pass 'brief' (the topic/angle). Call this "
            "FIRST, then pass the returned slug to every production job so all artifacts "
            "accumulate in one workspace.")
    schema = {"type": "object",
              "properties": {"brief": {"type": "string",
                                       "description": "The topic/brief for the video."}},
              "required": ["brief"]}

    async def _fn(args):
        import projects
        brief = (args.get("brief") or "").strip()
        if not brief:
            return _ok("Give me a brief to start a project (the topic/angle for the video).")
        info = await asyncio.to_thread(projects.start_project, brief)
        log.info("start_project: slug=%r brief=%r", info["slug"], brief)
        return _ok(f"Started project '{info['slug']}' at {info['project_dir']}. "
                   f"Pass slug='{info['slug']}' to every job for this video. "
                   "Next in the playbook: research (sage_research).")

    return tool("start_project", desc, schema)(_fn)


def _make_project_status_tool():
    """Read a project's checklist so Atlas knows what's produced and can resume."""
    desc = ("Read a project's checklist manifest: which artifacts are produced (done/"
            "pending) and the fact-check verdict. Use to resume a video without re-doing "
            "or skipping a step. Pass 'slug'. Omit it to list all known projects.")
    schema = {"type": "object",
              "properties": {"slug": {"type": "string",
                                      "description": "The project slug (omit to list all)."}},
              "required": []}

    async def _fn(args):
        import projects
        slug = (args.get("slug") or "").strip()
        if not slug:
            items = await asyncio.to_thread(projects.list_projects)
            if not items:
                return _ok("No projects yet. Start one with start_project.")
            lines = ["Known projects (newest first):"]
            lines += [f"  · {p['slug']} — {p['topic']}" for p in items[:20]]
            return _ok("\n".join(lines))
        return _ok(await asyncio.to_thread(projects.status_text, slug))

    return tool("project_status", desc, schema)(_fn)


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
        import chat_state
        import contracts
        import projects
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
        for job in adapter.entry.jobs:
            sdk_tools.append(_make_job_tool(adapter, job))
            allowed.append(f"mcp__{SERVER_NAME}__{job.tool}")
        if adapter.entry.persona:
            sdk_tools.append(_make_ask_tool(adapter))
            allowed.append(f"mcp__{SERVER_NAME}__ask_{adapter.entry.name}")

    # The non-registry orchestration tools: project workspace + checklist + optional
    # contract check. Atlas runs the production PLAYBOOK by calling the agent job tools
    # in sequence against one slug; these manage the workspace it accumulates into.
    for name, make in (("start_project", _make_start_project_tool),
                       ("project_status", _make_project_status_tool),
                       ("validate_artifact", _make_validate_artifact_tool)):
        sdk_tools.append(make())
        allowed.append(f"mcp__{SERVER_NAME}__{name}")

    server = create_sdk_mcp_server(SERVER_NAME, tools=sdk_tools)
    return server, allowed
