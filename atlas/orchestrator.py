"""Atlas's brain — the supervisor that delegates to the managed agents.

Atlas runs on the Claude Agent SDK's `query()` loop. The registry's capabilities are
exposed as in-process tools (generated in tools.py), and Atlas's LLM autonomously
decides which to call and in what order. Tools auto-run (no per-step [y/N] gate) —
Atlas is autonomous but TRANSPARENT: it announces each decision so the CEO can
interject and redirect.

Two channels of transparency, by design:
- STATUS (deterministic): the 🔎/✅/📚 lines printed from inside the tools as work
  happens — true no matter what the LLM says.
- DECISIONS / SYNTHESIS (the LLM's words): streamed as Atlas's text, including the
  required "🧠 I'm going with '<topic>' because <reason>" before it hands to Sage.

The synchronous sibling engines spin their own event loop, so the tools dispatch
them via asyncio.to_thread (see tools.py) — no loop nesting with this query() loop.
"""
from __future__ import annotations

import asyncio
import pathlib

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

import llm
import registry
from progress import Progress
from tools import SERVER_NAME, build_server

HERE = pathlib.Path(__file__).parent
SOUL_DIR = HERE / "soul"


def _read(p: pathlib.Path) -> str:
    try:
        return p.read_text()
    except OSError:
        return ""


# ----------------------------------------------------------------------
# System prompts — orchestration (with the delegation contract) vs. persona
# ----------------------------------------------------------------------
def _team_section() -> str:
    """A generated roster so Atlas knows exactly who maps to which tool."""
    lines = ["## Your team (delegate to them with the tools below)"]
    for e in registry.REGISTRY:
        tool_names = [f"`{j.tool}`" for j in e.jobs]
        if e.persona:
            tool_names.append(f"`ask_{e.name}`")
        lines.append(f"- {e.emoji} **{e.display}** — {e.blurb} "
                     f"Tools: {', '.join(tool_names)}.")
    return "\n".join(lines)


# The delegation + transparency contract. This is the part a pure-persona chat
# prompt deliberately EXCLUDES (so a casual conversation doesn't drag the whole
# orchestration machinery in).
ORCHESTRATION_CONTRACT = """
## How you run the room (your operating contract)
You have tools that delegate to your team. Decide yourself which to call and in what
order — do not ask the CEO to approve each step. But announce every decision clearly
so the CEO can redirect you ("no, research #2 instead").

DEFAULT PLAYBOOK — when the CEO asks for research on a viral topic in a niche:
1. Call `scout_find_topics` with the niche.
2. Read the ranked options. DECIDE the single strongest topic yourself.
3. Before handing off, say one line in this exact shape:
   🧠 I'm going with '<topic>' because <reason>.
4. Call `sage_research` on that topic.
5. Bring the findings back to the CEO as a clear, brief summary — lead with what's
   verified, flag myths and contested claims, and say where the full pack is saved.

PRODUCTION PLAYBOOK — when the CEO wants a VIDEO made (not just research):
Use `produce_video` with the brief. It runs the whole line for you, in order, and
validates every hand-off:
  Researcher → Scriptwriter → Fact-Checker ★GATE → Art Director (style + storyboard)
  → Asset Sourcer ∥ Audio → Composition Engineer ▲auto-gate → Audio mix
  → Final render ★GATE → video.mp4
Five of those specialists are STUBS today (registered slots) — say so plainly; don't
present stub output as finished work. Announce the kickoff in one line, then let the
tool's status lines carry the play-by-play.
To start a NEW video, call `produce_video` with `brief` only and NO `slug`. `slug` is
exclusively for RESUMING an existing project by its directory name — never invent one.

THE TWO GATES ARE SACRED (this is pause-and-resume, never an auto-advance):
- The tool returns PAUSED at a gate with details. Bring those details to the CEO and
  WAIT. Do not approve on their behalf.
  · Fact-check gate: present the flagged/unverifiable claims (or the clean verdict).
    If the verdict is `block`, you CANNOT approve it away. The fix is small and on-
    pipeline: make ONE `scriptwriter_write_script` call to revise the flagged claims
    in place, then RESUME `produce_video` (see below). The pipeline re-runs the fact-
    check on the revised script and only proceeds if it now genuinely passes. Do NOT
    hand-drive Iris/Magpie/Mason/Cadence yourself — that bypasses the gates. The
    pipeline owns the gates, the slug, and the render; your job is the script fix.
    Would rather kill a video than ship an unverified claim.
  · Final-render gate: present the draft + render plan before spending on the render.
- When the CEO signs off, RESUME by calling `produce_video` again with `approve` set
  to that gate's name. You do NOT need to remember the slug — `approve` ALONE resumes
  the project waiting at that gate. (Pass the `slug` too only if several videos are
  mid-flight at the same gate and it asks you to disambiguate.) Only set `unattended`
  if the CEO explicitly wants a fully-unattended run.

DIRECT ADDRESS — if the CEO speaks to a teammate by name ("Scout, what do you think
of X?"), route it to that teammate with their `ask_<name>` tool and relay the reply.

GENERAL QUESTIONS — if it's not a job and not direct address, just answer it
yourself, as the manager.

RULES:
- Run delegated jobs ONE AT A TIME (sequential) — never fan them out in parallel.
- If a teammate fails or times out, the tool tells you so: report it to the CEO
  plainly and continue or pause. Never pretend a failed job succeeded.
- Validate that a niche/topic is real before spending a teammate's time on it.
- Keep the CEO informed without noise: status lines appear on their own; your job is
  the decisions and the synthesis.
"""


def build_orchestrator_system() -> str:
    """Atlas's full operating prompt: identity + voice + roster + the contract."""
    soul = _read(SOUL_DIR / "SOUL.md").strip() or "You are Atlas, the YT Manager."
    style = _read(SOUL_DIR / "STYLE.md").strip()
    parts = [soul]
    if style:
        parts.append("# HOW YOU TALK (voice & style)\n\n" + style)
    parts.append(_team_section())
    parts.append(ORCHESTRATION_CONTRACT.strip())
    return "\n\n".join(parts)


def build_chat_system() -> str:
    """Atlas's PERSONA prompt — identity + voice only, NO orchestration contract.

    Used where the delegation machinery is irrelevant (e.g. describing what Atlas
    remembers, or distillation framing). Kept deliberately free of the playbook so
    those uses stay on-voice and small.
    """
    soul = _read(SOUL_DIR / "SOUL.md").strip() or "You are Atlas, the YT Manager."
    style = _read(SOUL_DIR / "STYLE.md").strip()
    parts = [soul]
    if style:
        parts.append("# HOW YOU TALK (voice & style)\n\n" + style)
    parts.append(
        "## Right now: a live meeting\n"
        "You're talking with the CEO directly. Be the calm, decisive chief-of-staff: "
        "brief, clear, and human.")
    return "\n\n".join(parts)


# ----------------------------------------------------------------------
# The orchestrator
# ----------------------------------------------------------------------
class Orchestrator:
    """Holds the team + the generated tools, and runs one CEO turn at a time."""

    def __init__(self, progress: Progress | None = None, *, model: str | None = None):
        self.progress = progress or Progress()
        self.adapters = registry.build_adapters()
        self.server, self.allowed = build_server(self.adapters, self.progress)
        self.system = build_orchestrator_system()
        self.model = model or llm.ORCH_MODEL

    def _build_prompt(self, user_msg: str, context: str = "") -> str:
        if context.strip():
            return (f"[What you remember / the meeting so far]\n{context.strip()}\n\n"
                    f"[The CEO just said]\n{user_msg.strip()}")
        return user_msg.strip()

    async def run_turn_async(self, user_msg: str, *, context: str = "",
                             on_text=None) -> str:
        """Run ONE CEO turn: stream Atlas's text, auto-run tools, return final text.

        `on_text` receives each streamed text chunk (default: print inline). The
        deterministic 🔎/✅ status lines print themselves from inside the tools.
        """
        if on_text is None:
            def on_text(t):  # noqa: E731 — tiny default sink
                print(t, end="", flush=True)

        options = ClaudeAgentOptions(
            model=self.model,
            system_prompt=self.system,
            mcp_servers={SERVER_NAME: self.server},
            allowed_tools=self.allowed,
            permission_mode="bypassPermissions",  # autonomous: tools auto-run, no gate
            tools=[],                             # no builtin tools (Read/Bash/ToolSearch)
        )

        async def _input():
            yield {"type": "user",
                   "message": {"role": "user",
                               "content": self._build_prompt(user_msg, context)}}

        parts: list[str] = []
        result = None
        async for message in query(prompt=_input(), options=options):
            if isinstance(message, AssistantMessage):
                if getattr(message, "error", None) is not None:
                    raise RuntimeError(f"Atlas hit an error: {message.error}")
                for block in message.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
                        on_text(block.text)
            elif isinstance(message, ResultMessage):
                result = message

        if result is not None and result.subtype != "success":
            raise RuntimeError(
                f"The turn ended with '{result.subtype}'. If this is a rate-limit, "
                "you've hit your subscription's rolling cap — wait and retry.")
        return (result.result if result and result.result else "".join(parts)).strip()

    def ask(self, user_msg: str, *, context: str = "", on_text=None) -> str:
        """Synchronous one-shot wrapper around run_turn_async."""
        return asyncio.run(self.run_turn_async(user_msg, context=context,
                                               on_text=on_text))
