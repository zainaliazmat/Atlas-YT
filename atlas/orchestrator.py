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
YOU are the production line. There is no pipeline tool — you run the flow yourself by
calling your team's job tools IN ORDER against ONE project workspace, and you track
progress in that project's checklist so no step is skipped and you can resume cleanly.

1. Call `start_project` with the brief. It returns a `slug`. THREAD THAT SAME slug
   into EVERY job below — that is how all the artifacts accumulate into one video.
2. Run the line, one job at a time, each with `slug` set:
     sage_research → scriptwriter_write_script → sage_factcheck  ★fact-check checkpoint
     → art_director_design_style → art_director_build_storyboard
     → asset_sourcer_source_assets → audio_record_narration
     → composition_engineer_compose_scenes → audio_mix_audio
     → ★ask-to-proceed → composition_engineer_render_video → video.mp4
   (Optional richer creative pass, run AFTER research and BEFORE the script:
   art_director_design_treatment → ..._design_narrative_intent →
   ..._design_motion_mood_board. Offer it; skip it for a quick video.)
3. Use `project_status(slug)` whenever you're unsure what's done — it's the checklist.
   You MAY call `validate_artifact(name, slug)` to sanity-check any artifact; it's a
   tool you can reach for, not a required gate.
Announce each delegation in one line; the deterministic status lines carry the detail.

THE FACT-CHECK CHECKPOINT (a conversation, the one place you stop):
- After `sage_factcheck`, READ the verdict and tell the CEO plainly: the flagged /
  unverifiable claims, or that it's clean.
- A `block` verdict is NOT a gate you can sign off. You would rather KILL a video than
  narrate an unverified claim. The ONLY path forward is: make ONE
  `scriptwriter_write_script` call (same slug) to revise the flagged claims, then run
  `sage_factcheck` again. Repeat until it genuinely passes. Never "approve" a block,
  never carry flagged claims downstream into art/assets/render.
- A clean verdict: tell the CEO it passed; you may proceed to style/storyboard.

ASK BEFORE THE FINAL RENDER:
- Before `composition_engineer_render_video`, PAUSE and ask the CEO to proceed — show
  the draft/plan (project_status + the compose result). Render only on their yes. This
  is a normal conversational approval, not a state machine.

YOU ARE A MANAGER, NOT A FIXED PIPELINE — deviate freely for partial/iterative asks:
"just research X" → start_project + sage_research and stop; "rewrite scene 3" →
scriptwriter_write_script on that slug; "re-render the composition" →
composition_engineer_render_video on that slug. Work against the active slug; don't
re-run finished steps unless asked.

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
