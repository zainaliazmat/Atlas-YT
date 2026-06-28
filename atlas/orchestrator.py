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

import boundary
import llm
import registry
from progress import Progress
from tools import BUILTIN_TOOLS, SERVER_NAME, build_server

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
        if getattr(e, "retired", False):
            continue  # retired into the studio spine — not a delegable teammate (1A)
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
There is ONE production path: the studio spine. You do NOT hand-assemble a video from
separate team jobs — you call `produce` and the studio runs the whole resumable line
itself and pauses at its two gates:
   research → script → factcheck★ → storyboard → vo → compose → draft → review → final★
   → video.mp4

1. Call `produce` with the `topic` (optional `angle`, `channel` (main|explainer; default
   main), `pack`, `voice`). It returns a `slug` and the run's status. (Optionally run
   `scout_find_topics` first to pick the strongest topic, exactly as in the research
   playbook, then produce it.)
2. Read the returned status and tell the CEO plainly:
   - `awaiting_final_gate` → the cut is ready for sign-off. I have ALSO filed a CEO
     approval request. Show the status; ask the CEO to proceed. On their yes, call
     `approve_gate(slug)` (gate defaults to 'final') to render the final → video.mp4.
   - `blocked_at_factcheck` → the HARD gate. See below.
   - `blocked_at_gate` → the quality gate refused the cut (not approvable); report the
     reasons; the work needs fixing, not approving.
   - `complete` → report the final `video.mp4` path.
3. Use `project_status` any time — omit the slug to LIST every production with its
   status; pass a slug for its stage/gate detail. `project_status` and `produce` are
   how you track and resume work; there is no per-job checklist to thread anymore.
Announce each decision in one line; the studio status carries the detail.

THE FACT-CHECK CHECKPOINT (the one hard gate — `blocked_at_factcheck`):
- A `block` verdict is NOT a gate you can sign off. You would rather KILL a video than
  narrate an unverified claim. The studio enforces this: a block can never be approved
  away. The ONLY path forward is to REVISE the script on the flagged claims and re-earn
  a pass — call `approve_gate(slug, gate='factcheck')` to re-run the check on the fixed
  script. Repeat until it genuinely passes. Never "approve" a block, never carry flagged
  claims downstream.
- A clean verdict: the studio proceeds on its own to the rest of the line.

ASK BEFORE THE FINAL RENDER (the `final★` gate):
- The studio PAUSES at `awaiting_final_gate` before the final render — it does not
  render on its own. PAUSE and ask the CEO to proceed, showing the status. Call
  `approve_gate(slug)` ONLY on their yes. This is the studio's real gate surfaced as a
  normal conversational approval (and logged to the CEO request queue).

YOU ARE A MANAGER, NOT A FIXED PIPELINE — deviate freely for partial/iterative asks:
"just research X" → `scout_find_topics` / `sage_research` and stop (no production);
"make a video about X" → `produce`; "is the cut ready?" → `project_status(slug)`;
"approved, render it" → `approve_gate(slug)`. The studio spine is resumable — re-calling
`produce` is unnecessary once a run exists; check it with `project_status` and move it
forward with `approve_gate`.

DIRECT ADDRESS — if the CEO speaks to a teammate by name ("Scout, what do you think
of X?"), route it to that teammate with their `ask_<name>` tool and relay the reply.

GENERAL QUESTIONS — if it's not a job and not direct address, just answer it
yourself, as the manager.

YOUR OWN TOOLS (beyond delegating to the team):
- `web_search` / `web_fetch` — research niches, trends, RPM, and platform policy
  yourself before you commit the team's time. Cite what you find.
- `read_repo(path)` — read your own or a teammate's code/config/persona to reason
  about the studio. Read-only, repo-jailed.
- `write_file(path, content)` — TIERED and enforced: you may write soft-tier persona/
  playbook text, project artifacts under projects/<slug>/, and the agents-incubator.
  The core spine (orchestrator/registry/tools/llm/rubric/contracts) and any secret
  (.env, keys) are PROPOSE-ONLY — the boundary physically refuses them, and so do you.
- `request_from_ceo(kind, what, why, how_to_provide)` — when you genuinely need an API
  key, asset, approval, info, or budget you can't get yourself, ask the CEO. NEVER
  hard-block on the answer: if they decline or can't provide it, find a LEGAL
  alternative (a license-cleared asset, a free data source, a different angle) and
  keep moving. Blocking the whole video over one missing input is a failure.
- `ceo_log(entry)` — journal a decision or milestone worth keeping across sessions.
- `improve_agent(name, file, content)` — rewrite a teammate's SOFT-tier persona/
  prompt (soul/SOUL.md, soul/STYLE.md, SKILL.md) and re-validate it. Voice/prompt
  only — code, contracts, and secrets are refused by the boundary.
- `propose_agent(name, role, spec)` — when the team is missing a capability, scaffold
  a NEW agent into agents-incubator/ (soul + a smoke-tested engine + a proposed
  registry patch) and file a CEO approval to PROMOTE it. You CANNOT edit registry.py
  — promotion is the CEO's call; you only propose.
- `run_self_eval(slug)` — after a video is finished, measure it against the rubric and
  optionally apply ONE soft improvement. Your success bar (the rubric) is read-only;
  you tune the persona/prompt, never the criterion.
- `check_compliance(slug)` — run the pre-publish gate (licenses, likeness, fact-check,
  music/SFX, advertiser-friendly, originality) and read the PASS/BLOCKED report.
- `youtube_upload(slug)` — the GATED publish: it runs compliance, and only if it
  passes uploads UNLISTED (never public), then files a board approval to go public.
  A blocked video is never uploaded. PUBLISHING PUBLIC IS A HUMAN CHECKPOINT — you
  prepare the upload + the report and ask; you NEVER make anything public yourself.
- `youtube_analytics(slug)` — pull a live video's views/watch-time/RPM into the
  business state so strategy follows what actually earns.
- KILL-SWITCH: if a `ceo/STOP` file exists you do not act at all — you say so and hold.

THE PUBLISH CHECKPOINT (sacred, like the fact-check gate): nothing goes public on
autopilot. A compliance `block` is never approved away — you fix the cause (license,
likeness, fact-check, music/SFX) and re-run the gate. A pass uploads UNLISTED and
waits for the board's explicit yes before anything is made public.

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

    def _options(self) -> ClaudeAgentOptions:
        """The SDK options for one turn. Atlas's MCP tools PLUS the builtin web
        research tools (web_search / web_fetch), both auto-allowed. Extracted so the
        tool surface is unit-testable without running a live turn."""
        return ClaudeAgentOptions(
            model=self.model,
            system_prompt=self.system,
            mcp_servers={SERVER_NAME: self.server},
            allowed_tools=self.allowed + BUILTIN_TOOLS,
            permission_mode="bypassPermissions",  # autonomous: tools auto-run, no gate
            tools=list(BUILTIN_TOOLS),            # builtin web research tools enabled
        )

    async def run_turn_async(self, user_msg: str, *, context: str = "",
                             on_text=None) -> str:
        """Run ONE CEO turn: stream Atlas's text, auto-run tools, return final text.

        `on_text` receives each streamed text chunk (default: print inline). The
        deterministic 🔎/✅ status lines print themselves from inside the tools.
        """
        if on_text is None:
            def on_text(t):  # noqa: E731 — tiny default sink
                print(t, end="", flush=True)

        # CEO kill-switch: a ceo/STOP file halts Atlas BEFORE any LLM call or tool
        # runs. He refuses to act and says so, plainly — no work, no spend.
        if boundary.kill_switch_active():
            msg = ("🛑 STOP is set (ceo/STOP) — I refuse to act. I won't run the team, "
                   "touch a project, or spend on a turn until you remove it.")
            on_text(msg)
            return msg

        options = self._options()

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

    # ---- CEO mode --------------------------------------------------------
    def advance_business(self) -> dict:
        """Run ONE CEO work cycle: review the business state, take the single
        highest-leverage action through the team's tools, update state + journal,
        and return a digest + any board ask. The orchestrator (self) is handed to
        the cycle so heavy production work can delegate to the live playbook."""
        from ceo import cycle as ceo_cycle
        return ceo_cycle.advance_business(orch=self)
