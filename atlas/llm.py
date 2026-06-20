"""Atlas's brain seam — one swappable chat() function.

`chat(system, user) -> str` is the ONLY text-completion call Atlas makes OUTSIDE
the orchestrator. It powers two things: the persona `ask` capability (speaking AS
Scout/Sage for a single turn) and memory distillation (Phase 2). The ORCHESTRATOR
itself does NOT go through here — it drives the SDK's `query()` directly because it
needs in-process tools + streaming (see orchestrator.py).

DEFAULT provider: **Claude on your Claude Code SUBSCRIPTION** via claude_agent_sdk
— no env var, NO API key. It draws from your Pro/Max plan, NOT the metered
Anthropic API, and does NOT use ANTHROPIC_API_KEY (if that key is set the SDK
silently switches to the metered API — so we warn).

Swappable alternatives, all behind the same one switch (ATLAS_LLM):
- "gemini"   — Google Gemini (free tier). ATLAS_LLM=gemini + GEMINI_API_KEY in .env.
- "deepseek" — DeepSeek (OpenAI-compatible). ATLAS_LLM=deepseek + DEEPSEEK_API_KEY.
No Ollama (same policy as the rest of the fleet). Keys come from .env via dotenv.

PROVIDER PRECEDENCE (the fleet runs on ONE shared root .env — document this):
- `ATLAS_LLM` chooses the brain for ATLAS's own work: orchestration reasoning,
  persona `ask`, and distillation.
- A delegated JOB runs INSIDE the sibling's engine, which reads its OWN switch
  (`SAGE_LLM`, frozen at import). So "ask Scout" (Atlas's seam, ATLAS_LLM) and
  "Scout does a job" (sibling engine, SAGE_LLM) can run on different providers.
  `/agents` surfaces each agent's effective provider so this is never invisible.
"""
import asyncio
import os
import warnings

from dotenv import load_dotenv

# Atlas lives in its own dir but the keys live in the shared root .env one level up.
# Load the local .env first (if any), then the repo-root .env, without overriding
# anything already set in the environment.
_HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_HERE, ".env"))
load_dotenv(os.path.join(os.path.dirname(_HERE), ".env"))

# ======================================================================
# THE ONE SWITCH — which brain answers Atlas's chat()
# ======================================================================
PROVIDER = os.environ.get("ATLAS_LLM", "claude").strip().lower()

# Per-use Claude models (subscription). Orchestration/persona reasoning wants a
# capable-but-fast brain; change here only.
CLAUDE_MODEL = "claude-sonnet-4-6"     # Atlas's reasoning/persona brain
ORCH_MODEL = "claude-sonnet-4-6"       # the orchestrator's tool-driving brain (orchestrator.py reads this)
CHAT_TIMEOUT_SEC = 180                 # a chat turn beyond this is stalled, not thinking

GEMINI_MODEL = "gemini-2.5-flash"
DEEPSEEK_MODEL = "deepseek-v4-flash"


def _warn_if_metered() -> None:
    """The subscription seam needs ANTHROPIC_API_KEY UNSET — warn if it's set."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        warnings.warn(
            "ANTHROPIC_API_KEY is set, so the Agent SDK will bill the metered API "
            "rather than your subscription. Unset it to use your subscription.",
            stacklevel=2,
        )


def effective_provider() -> str:
    """Human-readable name of the brain Atlas's own seam will use (for /agents)."""
    return {"claude": "Claude (subscription)", "gemini": "Gemini",
            "deepseek": "DeepSeek"}.get(PROVIDER, "Claude (subscription)")


# ----------------------------------------------------------------------
# Provider 1 (DEFAULT) — Claude via the Agent SDK (subscription auth, no API key)
# ----------------------------------------------------------------------
def _chat_claude(system: str, user: str) -> str:
    _warn_if_metered()
    return asyncio.run(_claude_chat_async(system, user))


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
            f"Run ended with '{result.subtype}'. If this is a rate-limit, you've hit "
            "your subscription's rolling cap — wait and retry, or set a cheaper model.")
    final = (result.result if result and result.result else "".join(parts))
    return (final or "").strip()


# ----------------------------------------------------------------------
# Provider 2 — Google Gemini, free tier
# ----------------------------------------------------------------------
def _chat_gemini(system: str, user: str) -> str:
    import google.generativeai as genai  # lazy: other providers don't need it
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Add it to your .env (free key at "
            "https://aistudio.google.com -> Get API key), or unset ATLAS_LLM to use "
            "the default Claude subscription brain.")
    genai.configure(api_key=key)
    model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=system)
    resp = model.generate_content(user)
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
            "DEEPSEEK_API_KEY is missing. Add it to your .env, or unset ATLAS_LLM to "
            "use the default Claude subscription brain.")
    r = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": DEEPSEEK_MODEL,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}],
              "stream": False},
        timeout=180,
    )
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    if not text:
        raise RuntimeError("DeepSeek returned an empty response.")
    return text.strip()


_PROVIDERS = {"claude": _chat_claude, "gemini": _chat_gemini, "deepseek": _chat_deepseek}


def chat(system: str, user: str) -> str:
    """Send a system + user prompt to the LLM and return its text reply.

    The single seam for Atlas's persona `ask` and distillation. Provider is chosen
    by PROVIDER (one place), defaulting to Claude. Signature is identical across
    providers so it stays a true drop-in swap.
    """
    fn = _PROVIDERS.get(PROVIDER, _chat_claude)
    return fn(system, user)


if __name__ == "__main__":
    print(f"Testing Atlas provider={PROVIDER} ...")
    print(chat("You are a test harness.", "Reply with exactly: ok"))
