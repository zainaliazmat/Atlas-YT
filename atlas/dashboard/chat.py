"""The Control Room's agentic chat — read-grounded, and T1-only BY CONSTRUCTION.

The chat panel lets the CEO talk to Atlas inside the control room. It is *agentic*
(the model decides what to do via a tool loop) but it lives on the **LLM plane**, which
the two-plane design (PROJECT_CONTEXT §3) never trusts to satisfy a guarantee. So the
write authority it can reach is fenced to exactly **Tier 1 — reversible internal**
(spec §4): trigger a production, cancel/park a run, change a default setting. Each is a
*proposal* the CEO confirms with one light click; nothing the chat says mutates state on
its own.

The safety property is structural, not prompt-deep:
  * The only action kinds that exist are `T1_ACTION_KINDS`. There is **no** `approve`
    or `publish` kind anywhere in this module — so the chat literally has no control
    that satisfies a T2 spine gate or a T3 publish (§4/§8, edge cases E7/E8).
  * `execute_action()` REJECTS any kind outside `T1_ACTION_KINDS`, so even a crafted
    request (prompt-injection from retrieved corpus, a hand-rolled POST) can't route a
    gate/publish through the chat plane. Every action it runs is tagged `initiator="chat"`
    in the event ring, so the §4 audit shows no T2/T3 write ever came from the LLM plane.

`default_send` is the real LLM implementation (Claude Agent SDK tool loop, T1-only tool
server). It is INJECTABLE via `app.state.chat_fn` so tests fake it — the real LLM never
runs in tests (ANTHROPIC_API_KEY is never set). The deterministic pieces here
(`execute_action`, `ground`) are fully unit-tested; the LLM seam degrades gracefully.
"""
from __future__ import annotations

import pathlib
from typing import Callable

from dashboard import data, settings_store

# The ONLY actions the chat may initiate — all Tier 1 reversible (spec §4). The absence of
# an "approve"/"publish" kind is the point: the LLM plane has no T2/T3 control to reach.
T1_ACTION_KINDS = ("trigger", "cancel", "update_setting")

# Default settings fields the chat may change (reversible; the rest live in the Settings UI).
_SETTABLE_DEFAULTS = ("target_length", "intake_mode", "voice", "style_preset")


def is_t1_action(kind: str | None) -> bool:
    return kind in T1_ACTION_KINDS


# ----------------------------------------------------------------------
# Read grounding — the belt / gates / settings snapshot the chat reasons over.
# Read-only, bounded, tolerant (mirrors the rest of dashboard.data).
# ----------------------------------------------------------------------
def ground(projects_dir: pathlib.Path, settings_path: pathlib.Path | str) -> dict:
    """A compact, read-only snapshot for the chat to reason over: belt counts, the videos
    currently needing the CEO (blocked/failed), any pending gate (which the chat may
    SUMMARISE + navigate to but never satisfy), and the niches/defaults."""
    belt = data.belt(projects_dir)
    needs = [{"slug": v["slug"], "label": v["label"], "belt_state": v["belt_state"],
              "gate": v.get("gate"), "hard_block": v.get("hard_block")}
             for v in belt.get("videos", [])
             if v["belt_state"] in ("blocked", "failed")]
    pub = settings_store.public_settings(settings_path)
    return {
        "counts": belt.get("counts", {}),
        "videos": [{"slug": v["slug"], "label": v["label"],
                    "belt_state": v["belt_state"], "station": v.get("station")}
                   for v in belt.get("videos", [])][:20],
        "needs_you": needs,
        "niches": [n.get("name") for n in pub.get("niches", []) if n.get("name")],
        "defaults": pub.get("defaults", {}),
    }


# ----------------------------------------------------------------------
# The deterministic T1 executor — the safety boundary. This is where a confirmed
# chat action actually runs, and it ONLY ever runs a Tier-1 reversible action.
# ----------------------------------------------------------------------
class NotReversibleError(ValueError):
    """Raised when something asks the chat plane to run a non-T1 action (e.g. approve a
    gate or publish). The chat plane may never satisfy a T2/T3 guarantee — spec §4/§8."""


def execute_action(dispatcher, settings_path: pathlib.Path | str, kind: str,
                   args: dict | None, *, initiator: str = "chat") -> dict:
    """Run a CEO-CONFIRMED Tier-1 action proposed by the chat. Rejects anything outside
    `T1_ACTION_KINDS` (incl. any `approve`/`publish` attempt) with NotReversibleError —
    the LLM plane has no path to a guarantee. Every action is tagged `initiator` (default
    'chat') for the §4 audit. Returns the executed action's result dict."""
    args = args or {}
    if not is_t1_action(kind):
        raise NotReversibleError(
            f"'{kind}' is not a reversible (T1) action. The chat can trigger, cancel, or "
            "change a setting — it can never approve a gate or publish (that authorising "
            "click lives on the deterministic UI).")

    if kind == "trigger":
        topic = (args.get("topic") or "").strip()
        brief = (args.get("brief") or "").strip()
        if not topic and not brief:
            raise ValueError("a topic or brief is required to start a production")
        niche = args.get("niche") or None
        length = args.get("length")
        if not length and niche:
            length = settings_store.length_for_niche(
                settings_store.load_settings(settings_path), niche)
        out = dispatcher.trigger(
            brief=brief or None, topic=topic or None, length=length, niche=niche,
            gates=bool(args.get("gates", True)), initiator=initiator)
        return {"kind": "trigger", **out}

    if kind == "cancel":
        slug = (args.get("slug") or "").strip()
        if not slug:
            raise ValueError("a slug is required to cancel a run")
        out = dispatcher.cancel(slug, initiator=initiator)
        return {"kind": "cancel", **out}

    # kind == "update_setting": change ONE default field (reversible; re-edit = undo)
    field = (args.get("field") or "").strip()
    if field not in _SETTABLE_DEFAULTS:
        raise ValueError(
            f"'{field}' is not a settable default. Allowed: {', '.join(_SETTABLE_DEFAULTS)}.")
    value = args.get("value")
    settings = settings_store.load_settings(settings_path)
    defaults = dict(settings.get("defaults", {}) or {})
    defaults[field] = value
    settings["defaults"] = defaults
    saved = settings_store.save_settings(settings_path, settings)
    return {"kind": "update_setting", "field": field, "value": value,
            "defaults": (saved or {}).get("defaults", {})}


# ----------------------------------------------------------------------
# The real LLM seam — a constrained Agent SDK tool loop whose ONLY tools are read
# grounding + T1 *proposals*. Injectable via app.state.chat_fn; never runs in tests.
# ----------------------------------------------------------------------
_CHAT_SYSTEM = """You are Atlas, the calm chief-of-staff running a YouTube video agency
from its Control Room. You are talking with the CEO in a side chat.

WHAT YOU CAN DO
- Answer questions about the belt, the fleet, gates, and settings, grounded in the
  read tools (`belt_status`, `gate_status`, `settings_status`). Be brief and concrete.
- PROPOSE a reversible action when the CEO asks for one, using a propose_* tool:
  · `propose_start_production` — start a new video (topic, optional length/niche/gates).
  · `propose_cancel_run` — cancel/park a run by its slug.
  · `propose_update_setting` — change one default (target_length, intake_mode, voice,
    style_preset).
  A proposal does NOT execute — the CEO confirms it with one click. Say what you propose
  in one short line.

WHAT YOU MUST NEVER DO
- You CANNOT approve a fact-check or final-render gate, and you CANNOT publish. There is
  no tool for it on purpose: those are guarantees, and you are not trusted to satisfy a
  guarantee. If the CEO wants to approve a gate or publish, SUMMARISE what's pending and
  tell them the authorising click is on the deterministic gate/publish screen — then
  point them there. Never claim you approved or published anything.
- Never present a proposal as already done.

Keep it human and short. One decision or answer at a time."""


def default_send(message: str, *, history: list[dict] | None = None,
                 on_text: Callable[[str], None] | None = None,
                 projects_dir: pathlib.Path, settings_path: pathlib.Path | str) -> dict:
    """The real chat turn: a constrained Claude Agent SDK loop with a T1-only tool server.
    Streams Atlas's words via `on_text`; returns {"reply": str, "action": {kind,args}|None}.

    Degrades gracefully (a friendly note, no action) if the SDK/subscription is
    unavailable — the panel must never hard-crash. The real LLM never runs under tests;
    app.state.chat_fn is injected there."""
    try:
        import asyncio

        from claude_agent_sdk import (ClaudeAgentOptions, create_sdk_mcp_server, query,
                                      tool)
        from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock
    except Exception:  # noqa: BLE001 — no SDK in this environment
        if on_text:
            on_text("(Chat needs the Claude Agent SDK + your subscription to run here.)")
        return {"reply": "(chat unavailable — SDK not present)", "action": None}

    snap = ground(projects_dir, settings_path)
    pending: dict = {"action": None}

    def _ok(text: str) -> dict:
        return {"content": [{"type": "text", "text": text}]}

    @tool("belt_status", "Read the live belt: counts, videos, and what needs the CEO.", {})
    async def belt_status(args):  # noqa: ANN001
        return _ok(str(snap))

    @tool("gate_status", "Summarise any gate awaiting the CEO (read-only — you cannot "
          "approve it; the authorising click is on the gate screen).", {})
    async def gate_status(args):  # noqa: ANN001
        if not snap["needs_you"]:
            return _ok("Nothing is waiting at a gate right now.")
        return _ok("Awaiting the CEO (review on the deterministic gate screen): "
                   + str(snap["needs_you"]))

    @tool("settings_status", "Read the niches and default settings.", {})
    async def settings_status(args):  # noqa: ANN001
        return _ok(f"niches={snap['niches']} defaults={snap['defaults']}")

    @tool("propose_start_production",
          "Propose starting a new video (reversible; the CEO confirms).",
          {"topic": str, "length": str, "niche": str, "gates": bool})
    async def propose_start(args):  # noqa: ANN001
        pending["action"] = {"kind": "trigger", "args": {
            "topic": args.get("topic") or "", "length": args.get("length") or None,
            "niche": args.get("niche") or None,
            "gates": bool(args.get("gates", True))}}
        return _ok("Proposed a new production — the CEO will confirm it.")

    @tool("propose_cancel_run", "Propose cancelling/parking a run by slug (reversible).",
          {"slug": str})
    async def propose_cancel(args):  # noqa: ANN001
        pending["action"] = {"kind": "cancel", "args": {"slug": args.get("slug") or ""}}
        return _ok("Proposed cancelling that run — the CEO will confirm it.")

    @tool("propose_update_setting", "Propose changing one default setting (reversible).",
          {"field": str, "value": str})
    async def propose_setting(args):  # noqa: ANN001
        pending["action"] = {"kind": "update_setting", "args": {
            "field": args.get("field") or "", "value": args.get("value")}}
        return _ok("Proposed the setting change — the CEO will confirm it.")

    server = create_sdk_mcp_server("control_room_chat", tools=[
        belt_status, gate_status, settings_status,
        propose_start, propose_cancel, propose_setting])
    allowed = ["mcp__control_room_chat__belt_status",
               "mcp__control_room_chat__gate_status",
               "mcp__control_room_chat__settings_status",
               "mcp__control_room_chat__propose_start_production",
               "mcp__control_room_chat__propose_cancel_run",
               "mcp__control_room_chat__propose_update_setting"]

    convo = ""
    for t in (history or [])[-8:]:
        who = "CEO" if t.get("role") == "user" else "Atlas"
        convo += f"{who}: {t.get('content', '')}\n"
    prompt = (f"[The chat so far]\n{convo}\n[The CEO just said]\n{message}"
              if convo else message)

    async def _run() -> str:
        options = ClaudeAgentOptions(
            system_prompt=_CHAT_SYSTEM,
            mcp_servers={"control_room_chat": server},
            allowed_tools=allowed,
            permission_mode="bypassPermissions",
            tools=[])

        async def _input():
            yield {"type": "user", "message": {"role": "user", "content": prompt}}

        parts: list[str] = []
        result = None
        async for msg in query(prompt=_input(), options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
                        if on_text:
                            on_text(block.text)
            elif isinstance(msg, ResultMessage):
                result = msg
        return (result.result if result and result.result else "".join(parts)).strip()

    try:
        reply = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — containment; the panel must not crash
        if on_text:
            on_text(f"(Atlas hit a problem: {exc})")
        return {"reply": f"(chat error: {exc})", "action": None}
    return {"reply": reply, "action": pending["action"]}
