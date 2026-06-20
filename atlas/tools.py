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

# Permanent instrumentation: every produce_video invocation logs the literal args it
# received (see _make_produce_tool). Turns "Atlas did something weird with the pipeline"
# into a one-line log check instead of a transcript hunt.
log = logging.getLogger("atlas.tools")


def configure_logging(path=None):
    """Route the `atlas` logger's INFO records to a FILE (never stdout, so the
    interactive meeting stays clean). Idempotent; call once at chat startup.

    Without this, the permanent produce_video arg-logging in `_make_produce_tool`
    goes nowhere — Python drops sub-WARNING records when no handler is configured.
    Configuring the parent `atlas` logger captures every `atlas.*` child (e.g.
    `atlas.tools`) via propagation.
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
    """Build one async SDK tool that runs `job` on `adapter`, safely + bounded."""
    who = adapter.entry.display

    async def _fn(args):
        params = {k: (args.get(k) or "") for k in job.params}
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

    return tool(job.tool, job.description, job.params)(_fn)


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


def _make_produce_tool(progress):
    """The Showrunner's production-pipeline tool.

    Runs the deterministic spine (pipeline.py) end-to-end against the specialists,
    enforcing contracts and the two human gates. Gates are PAUSE-AND-RESUME: the
    tool returns a `blocked` result with the gate + details (it never blocks
    mid-tool); Atlas relays it to the CEO and re-invokes with `approve` to resume.
    """
    desc = ("Run (or resume) the full video production pipeline. Stages validate "
            "against frozen contracts and stop at two human gates (fact-check, final "
            "render). To start a NEW video, pass 'brief' only and OMIT 'slug'. To "
            "RESUME after the CEO signs off, pass the 'slug' from the blocked result "
            "plus 'approve'. Set 'unattended' = 'yes' to run straight through.")

    # A FULL JSON Schema (not a {name: type} dict). The SDK passes a schema with
    # type+properties straight through (create_sdk_mcp_server._build_schema), so we
    # control `required` ourselves: NOTHING is required. The {name: type} form the
    # other tools use is force-marked all-required by the SDK — which is exactly the
    # trap that made the LLM fill 'slug' on a fresh call and trip the resume path.
    # Here both valid shapes — {brief} (new) and {slug, approve} (resume) — are
    # schema-valid; the handler enforces "exactly one of" them.
    params = {
        "type": "object",
        "properties": {
            "brief": {
                "type": "string",
                "description": ("The topic/brief for the video. Provide this to start a "
                                "NEW video. Leave empty when resuming."),
            },
            "slug": {
                "type": "string",
                "description": ("RESUME ONLY. The exact existing project directory name "
                                "from a prior blocked result. OMIT entirely to start a "
                                "NEW video — never synthesize a slug from the topic."),
            },
            "approve": {
                "type": "string",
                "description": ("RESUME ONLY. The gate to clear when resuming: "
                                "'factcheck' or 'final_render'. Omit on a new video."),
            },
            "unattended": {
                "type": "string",
                "description": ("Set to 'yes' to run straight through both human gates "
                                "without pausing. Default (empty) honors the gates; use "
                                "only when the CEO explicitly asks for an unattended run."),
            },
        },
        "required": [],
    }

    async def _fn(args):
        import pipeline
        # Permanent INFO instrumentation — the literal args the LLM passed.
        log.info("produce_video args: brief=%r slug=%r approve=%r unattended=%r",
                 args.get("brief"), args.get("slug"), args.get("approve"),
                 args.get("unattended"))
        brief = (args.get("brief") or "").strip() or None
        slug = (args.get("slug") or "").strip() or None
        approve = [g for g in (args.get("approve") or "").replace(",", " ").split() if g]
        unattended = (args.get("unattended") or "").strip().lower() in ("1", "yes", "true")

        # The schema permits any shape; the REAL contract lives here: exactly one of
        # {brief} (new) or {slug (+approve)} (resume). A bare call can neither start
        # nor resume anything — coach instead of spawning a blank project.
        if not brief and not slug and not approve:
            return _ok("Nothing to produce: provide a 'brief' to start a NEW video, a "
                       "'slug' (+ 'approve') to RESUME a specific project, or just "
                       "'approve' to resume the project waiting at that gate.")

        try:
            result = await asyncio.to_thread(
                pipeline.produce, brief, slug=slug, approve=approve or None,
                unattended=unattended, progress=progress)
        except Exception as exc:  # noqa: BLE001 — containment: never crash the meeting
            return _ok(f"The pipeline hit a problem: {exc}. Report it and pause.")
        status = result.get("status")
        if status == "done":
            return _ok(f"Pipeline complete. Video at {result['video']} "
                       f"(project: {result['project_dir']}).")
        if status == "blocked":
            return _ok(f"PAUSED at the {result['gate']} gate. {result.get('reason','')} "
                       f"Details: {result.get('details')}. To resume after the CEO "
                       f"signs off, call produce_video with slug='{result['slug']}' and "
                       f"approve='{result['gate']}'.")
        # A resume whose slug didn't resolve fails BEFORE any stage runs: status
        # 'failed' + stage None + a slug was passed. Key the recovery coaching off
        # that STRUCTURED signal (not the error string) — a mid-pipeline resume
        # failure carries a real stage name and must NOT be told to "retry without
        # slug".
        if status == "failed" and result.get("stage") is None and slug:
            return _ok(f"No project named '{slug}'. To start a NEW video, call "
                       "produce_video again with no slug. To RESUME, pass an existing "
                       "project directory name.")
        # An approve-only resume that couldn't resolve a UNIQUE blocked project (zero
        # candidates, or ambiguous) fails before any stage with no slug — surface the
        # resolver's clear message verbatim so Atlas can relay it / disambiguate.
        if status == "failed" and result.get("stage") is None and not slug:
            return _ok((result.get("errors") or ["Couldn't resume."])[0])
        return _ok(f"Pipeline {status} at stage {result.get('stage')}: "
                   f"{result.get('errors')}.")

    return tool("produce_video", desc, params)(_fn)


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

    # The one non-registry tool: the production pipeline spine.
    sdk_tools.append(_make_produce_tool(progress))
    allowed.append(f"mcp__{SERVER_NAME}__produce_video")

    server = create_sdk_mcp_server(SERVER_NAME, tools=sdk_tools)
    return server, allowed
