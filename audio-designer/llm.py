"""Cadence's brain — one swappable chat() function, plus a converse() chat seam.

Cadence's ENGINE is deterministic in everything that touches the MIX — gains,
ducking, the manifest, the master render are a recipe, not taste. The ONE place the
engine reaches for a brain is the LLM-assisted curation seam (mood→search query,
candidate pick, SFX intent), and even there it degrades to a deterministic fallback
when no brain is reachable. The other LLM users are the co-worker REPL ("Talk to
Cadence", chat.py) and the cross-session memory distiller.

`chat(system, user) -> str` is the ONLY text-completion call those make. To swap LLM
providers you change ONE place: the `PROVIDER` constant below (or set the AUDIO_LLM
env var). Everything else is provider-agnostic.

DEFAULT provider: **Claude on your Claude Code SUBSCRIPTION** via claude_agent_sdk
— no env var required, NO API key. It draws from your Pro/Max plan, NOT the
pay-per-token Anthropic API, and does NOT use ANTHROPIC_API_KEY (if that key is
set, the SDK silently switches to the metered API — so we warn if we see it).

Swappable alternatives, all behind the same one switch:
- "gemini"   — Google Gemini (free tier). Activate with AUDIO_LLM=gemini and a
  GEMINI_API_KEY in .env (free key at https://aistudio.google.com -> Get API key).
- "deepseek" — DeepSeek (OpenAI-compatible, raw requests). Activate with
  AUDIO_LLM=deepseek and a DEEPSEEK_API_KEY in .env.

Keys are NEVER hardcoded — they come from a .env file via python-dotenv.

NOTE ON CHAT: the co-worker REPL does NOT use chat(). It uses converse() at the
bottom of this file, which always runs on the Claude Agent SDK because the persona
chat needs the SDK's in-process tool + approval gate. That is a deliberate,
separate seam (configurable in one spot: CHAT_MODEL).
"""
import asyncio
import time
import os
import warnings

from dotenv import load_dotenv

load_dotenv()  # pulls GEMINI_API_KEY / DEEPSEEK_API_KEY out of .env

# ======================================================================
# THE ONE SWITCH — which brain answers chat()
# ======================================================================
# Default (unset) = "claude" (subscription). Export AUDIO_LLM=gemini / deepseek to swap.
PROVIDER = os.environ.get("AUDIO_LLM", "claude").strip().lower()

# Per-use Claude models (subscription). The distiller + curation are light jobs;
# chat is fine on a fast Sonnet-class brain and keeps the heavier rate limit free.
CLAUDE_MODEL = "claude-sonnet-4-6"     # distiller / curation brain
CHAT_MODEL = "claude-sonnet-4-6"       # fast chat brain (converse) — change here only.
CHAT_TIMEOUT_SEC = 180                  # a chat turn beyond this is stalled, not thinking

# Alternative-provider models (only used when AUDIO_LLM selects them).
GEMINI_MODEL = "gemini-2.5-flash"      # free, fast; "gemini-2.5-pro" for harder calls
DEEPSEEK_MODEL = "deepseek-v4-flash"   # current general-chat model


def _warn_if_metered() -> None:
    """The subscription seam needs ANTHROPIC_API_KEY UNSET — warn if it's set."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        warnings.warn(
            "ANTHROPIC_API_KEY is set, so the Agent SDK will bill the metered API "
            "rather than your subscription. Unset it to use your subscription.",
            stacklevel=2,
        )


# ----------------------------------------------------------------------
# Provider 1 (DEFAULT) — Claude via the Agent SDK (subscription auth, no API key)
# ----------------------------------------------------------------------
def _chat_claude(system: str, user: str) -> str:
    _warn_if_metered()
    # Retry transient API hiccups (server_error/overloaded/5xx/connection) with backoff
    # so one blip doesn't fail a whole pipeline stage. Rate-limit caps are NOT retried
    # away aggressively — a few spaced attempts at most.
    _TRANSIENT = ('server_error', 'overloaded', 'connection', 'timeout',
                  '500', '502', '503', '529')
    last = None
    for attempt in range(4):
        try:
            return asyncio.run(_claude_chat_async(system, user))
        except Exception as e:  # noqa: BLE001 — classify, retry transient, else re-raise
            last = e
            if attempt == 3 or not any(t in str(e).lower() for t in _TRANSIENT):
                raise
            time.sleep(1.5 * (2 ** attempt))
    raise last  # pragma: no cover


async def _claude_chat_async(system: str, user: str) -> str:
    from claude_agent_sdk import query, ClaudeAgentOptions
    from claude_agent_sdk.types import AssistantMessage, TextBlock, ResultMessage

    options = ClaudeAgentOptions(model=CLAUDE_MODEL, system_prompt=system, tools=[])
    parts: list[str] = []
    result = None
    async for message in query(prompt=user, options=options):
        if isinstance(message, AssistantMessage):
            if getattr(message, "error", None) is not None:
                raise RuntimeError(f"Claude returned an error: {message.error}")
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
        elif isinstance(message, ResultMessage):
            result = message
    if result is not None and result.subtype != "success":
        raise RuntimeError(
            f"Run ended with '{result.subtype}'. If this is a rate-limit you've hit "
            "your subscription's rolling cap — wait and retry, or set CLAUDE_MODEL "
            "to a cheaper alias."
        )
    final = (result.result if result and result.result else "".join(parts))
    return (final or "").strip()


# ----------------------------------------------------------------------
# Provider 2 — Google Gemini, free tier
# ----------------------------------------------------------------------
_genai_model = None


def _chat_gemini(system: str, user: str) -> str:
    global _genai_model
    import google.generativeai as genai  # imported lazily so other providers don't need it
    if _genai_model is None:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is missing. Add it to your .env (free key at "
                "https://aistudio.google.com -> Get API key), or unset AUDIO_LLM to "
                "use the default Claude subscription brain."
            )
        genai.configure(api_key=key)
    # system_instruction is bound at construction; rebuild each call since the
    # distiller uses a distinct system prompt.
    _genai_model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=system)
    resp = _genai_model.generate_content(user)
    text = getattr(resp, "text", None)
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    return text.strip()


# ----------------------------------------------------------------------
# Provider 3 — DeepSeek (OpenAI-compatible chat completions, raw requests)
# ----------------------------------------------------------------------
def _chat_deepseek(system: str, user: str) -> str:
    import requests
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is missing. Add it to your .env (https://platform.deepseek.com "
            "-> API keys), or unset AUDIO_LLM to use the default Claude subscription brain."
        )
    r = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        },
        timeout=180,
    )
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    if not text:
        raise RuntimeError("DeepSeek returned an empty response.")
    return text.strip()


_PROVIDERS = {
    "claude": _chat_claude,
    "gemini": _chat_gemini,
    "deepseek": _chat_deepseek,
}


def chat(system: str, user: str) -> str:
    """Send a system prompt + a user prompt to the LLM and return its text reply.

    The single seam the distiller + curation talk through. Provider is chosen by
    PROVIDER (one place), defaulting to Claude. Keep the signature (system, user) ->
    str identical across providers so it stays a true drop-in swap.
    """
    fn = _PROVIDERS.get(PROVIDER, _chat_claude)  # unknown/unset -> Claude (default)
    return fn(system, user)


# ======================================================================
# MULTI-TURN SEAM — converse() for "Talk to Cadence" (chat.py)
# ======================================================================
# Always runs on the Claude Agent SDK: the persona chat needs the SDK's in-process
# tool + can_use_tool approval gate (mid-chat jobs with a [y/N] prompt). It is
# still provider-agnostic about STATE — the caller owns chat_state.json and hands
# us (system, summary, recent_turns, user_msg) every turn, so the durable memory
# survives a future brain swap. The optional `cadence` wiring is the tool+approval
# bundle; pass cadence=None to disable the native-tool job (marker fallback handles it).


def _render_turns(recent_turns) -> str:
    label = {"user": "User", "cadence": "Cadence"}
    return "\n".join(
        f"{label.get(t['role'], t['role'])}: {t['content']}" for t in recent_turns
    )


def _build_chat_prompt(summary: str, recent_turns, user_msg: str) -> str:
    parts = []
    if summary.strip():
        parts.append(f"[What you remember about the user / context]\n{summary.strip()}")
    if recent_turns:
        parts.append(f"[Recent conversation so far]\n{_render_turns(recent_turns)}")
    parts.append(f"[The user just said]\n{user_msg}")
    return "\n\n".join(parts)


async def _converse_async(system, summary, recent_turns, user_msg, cadence, model) -> str:
    from claude_agent_sdk import query, ClaudeAgentOptions
    from claude_agent_sdk.types import AssistantMessage, TextBlock, ResultMessage

    prompt_text = _build_chat_prompt(summary, recent_turns, user_msg)
    opts_kwargs = dict(model=model or CHAT_MODEL, system_prompt=system, tools=[])
    if cadence is not None:
        # Register the in-process tools + approval gate. We do NOT pre-allow them,
        # so can_use_tool fires and the user gets the [y/N] prompt.
        opts_kwargs["mcp_servers"] = {"cadence": cadence["server"]}
        opts_kwargs["can_use_tool"] = cadence["can_use_tool"]
        opts_kwargs["permission_mode"] = "default"
    options = ClaudeAgentOptions(**opts_kwargs)

    async def _input():
        yield {"type": "user", "message": {"role": "user", "content": prompt_text}}

    parts: list[str] = []
    result = None
    async for message in query(prompt=_input(), options=options):
        if isinstance(message, AssistantMessage):
            if getattr(message, "error", None) is not None:
                raise RuntimeError(f"Claude returned an error: {message.error}")
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
        elif isinstance(message, ResultMessage):
            result = message
    if result is not None and result.subtype != "success":
        raise RuntimeError(
            f"Chat turn ended with '{result.subtype}'. If this is a rate-limit "
            "you've hit your subscription's rolling cap — wait and retry."
        )
    return "".join(parts).strip()


def converse(system: str, summary: str, recent_turns, user_msg: str,
             *, cadence=None, model: str | None = None) -> str:
    """Multi-turn chat seam (Claude SDK). Returns Cadence's reply text.

    system        : the chat persona system prompt (built from SOUL only).
    summary       : durable distilled context (+ memory snapshot) about the user.
    recent_turns  : list of {"role": "user"|"cadence", "content": str} — the window.
    user_msg      : the user's new message.
    cadence       : optional tool wiring {"server", "can_use_tool"}; None disables
                    mid-chat jobs via the native tools.
    """
    _warn_if_metered()
    try:
        return asyncio.run(asyncio.wait_for(
            _converse_async(system, summary, recent_turns, user_msg, cadence, model),
            CHAT_TIMEOUT_SEC))
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"no reply within {CHAT_TIMEOUT_SEC}s — most likely your Claude "
            "subscription's rolling rate-limit, or a network stall. Wait a minute "
            "and try again (or /new). To ease rate pressure, set CHAT_MODEL/"
            "CLAUDE_MODEL to a lighter alias.")


if __name__ == "__main__":
    # Quick connectivity check: `python llm.py` confirms the selected brain works.
    print(f"Testing provider={PROVIDER} ...")
    print(chat("You are a test harness.", "Reply with exactly: ok"))
