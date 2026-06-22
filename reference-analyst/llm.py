"""Vera's brain — one swappable chat() function, a converse() chat seam, and the
VISION seam that turns saved reference frames into a style profile.

`chat(system, user) -> str` is the ONLY text-completion call the engine-side helpers
and the distiller make. To swap LLM providers you change ONE place: the `PROVIDER`
constant below (or set the VERA_LLM env var). Everything else is provider-agnostic.

DEFAULT provider: **Claude on your Claude Code SUBSCRIPTION** via claude_agent_sdk
— no env var required, NO API key. It draws from your Pro/Max plan, NOT the
pay-per-token Anthropic API, and does NOT use ANTHROPIC_API_KEY (if that key is
set, the SDK silently switches to the metered API — so we warn if we see it).

Swappable alternatives, all behind the same one switch:
- "gemini"   — Google Gemini (free tier). Activate with VERA_LLM=gemini and a
  GEMINI_API_KEY in .env (free key at https://aistudio.google.com -> Get API key).
- "deepseek" — DeepSeek (OpenAI-compatible, raw requests). Activate with
  VERA_LLM=deepseek and a DEEPSEEK_API_KEY in .env. (Text only — DeepSeek has no
  vision here, so the judged style-profile degrades to a clear note.)

Keys are NEVER hardcoded — they come from a .env file via python-dotenv.

THE VISION SEAM (`make_style_profiler`): the reference engine's judged layer is a
pure injected seam — `build_rubric(..., vision_fn=...)`. We bind it to the
vision-capable brain here, so the engine never imports an LLM and the objective half
still runs with zero network. Vision is best-effort: any failure degrades to the
engine's judged.status 'draft' + an error note, never a crash.
"""
import asyncio
import base64
import json
import os
import re
import time
import warnings

from dotenv import load_dotenv

load_dotenv()  # pulls GEMINI_API_KEY / DEEPSEEK_API_KEY out of .env

# ======================================================================
# THE ONE SWITCH — which brain answers chat()
# ======================================================================
# Default (unset) = "claude" (subscription). Export VERA_LLM=gemini / deepseek to swap.
PROVIDER = os.environ.get("VERA_LLM", "claude").strip().lower()

# Per-use Claude models (subscription). The distiller/text work is light; chat is
# fine on a fast Sonnet-class brain and keeps the heavier rate limit free.
CLAUDE_MODEL = "claude-sonnet-4-6"     # text/distiller brain ("haiku" spends even less)
CHAT_MODEL = "claude-sonnet-4-6"       # fast chat brain (converse) — change here only.
VISION_MODEL = "claude-sonnet-4-6"     # the frame-reading brain for the style profile.
CHAT_TIMEOUT_SEC = 180                  # a chat turn beyond this is stalled, not thinking

# Alternative-provider models (only used when VERA_LLM selects them).
GEMINI_MODEL = "gemini-2.5-flash"      # free, fast; "gemini-2.5-pro" for harder calls
DEEPSEEK_MODEL = "deepseek-v4-flash"   # current general-chat model

# Cap how many frames the vision call reads — bounded tokens + latency.
MAX_VISION_FRAMES = 6

# A flaky moment shouldn't lose the whole style profile: retry transient vision
# failures (server_error / overload) with linear backoff before degrading.
VISION_RETRIES = 2
VISION_RETRY_DELAY_SEC = 2.0


def _warn_if_metered() -> None:
    """The subscription seam needs ANTHROPIC_API_KEY UNSET — warn if it's set."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        warnings.warn(
            "ANTHROPIC_API_KEY is set, so the Agent SDK will bill the metered API "
            "rather than your subscription. Unset it to use your subscription.",
            stacklevel=2,
        )


def _agent_options(model: str, system: str, **extra):
    """Build ClaudeAgentOptions ISOLATED from the user's interactive Claude Code config.

    ROOT CAUSE (debugged 2026-06): every SDK call spawns a `claude` subprocess that, by
    default, inherits the user's GLOBAL config — including claude.ai connector MCP servers
    (Google Drive / Calendar / Gmail) and plugin MCP servers (context7). Those servers
    CANNOT authenticate in this headless, programmatic context, so the CLI blocks trying
    to connect to them until they time out, intermittently stalling EVERY call 40–60s+
    (it's intermittent because the "needs-auth" result is cached — cold cache = re-probe =
    hang; warm = ~8s). This was misread as a rate-limit; it is not (proven: a bare CLI
    `claude -p` hung identically, while `--strict-mcp-config` was consistently fast).

    The fix, both belt and suspenders:
      - setting_sources=[]   -> don't load user/project/local settings.json at all
                               (so no inherited MCP servers, plugins, hooks, or effort).
      - strict_mcp_config=True -> ignore ALL MCP config except servers passed explicitly.
    Auth (.credentials.json) is independent of settings, so the subscription still works.
    Result: fast, deterministic text + vision calls that don't depend on the user's
    interactive environment. (A programmatic embedded call SHOULD be isolated like this.)
    """
    from claude_agent_sdk import ClaudeAgentOptions
    return ClaudeAgentOptions(model=model, system_prompt=system, tools=[],
                              setting_sources=[], strict_mcp_config=True, **extra)


# ----------------------------------------------------------------------
# Provider 1 (DEFAULT) — Claude via the Agent SDK (subscription auth, no API key)
# ----------------------------------------------------------------------
def _chat_claude(system: str, user: str) -> str:
    _warn_if_metered()
    return asyncio.run(_claude_chat_async(system, user))


async def _drain(query_iter, *, what: str = "Claude", subtype_hint: str = "") -> str:
    """Consume an SDK `query()` stream to completion and return its text.

    CRITICAL: we never `break`/`raise` *inside* the `async for`. Raising mid-stream
    leaves the SDK's async generator suspended while Python tries to `aclose()` it,
    which surfaces the noisy `RuntimeError: aclose(): asynchronous generator is already
    running` (and a scary traceback) on top of the real error. Instead we capture the
    first error + the result, let the generator finish naturally, and raise AFTER —
    so a transient server_error degrades cleanly.
    """
    from claude_agent_sdk.types import AssistantMessage, TextBlock, ResultMessage
    parts: list[str] = []
    err = None
    result = None
    async for message in query_iter:
        if isinstance(message, AssistantMessage):
            if getattr(message, "error", None) is not None and err is None:
                err = message.error          # capture, but keep draining the stream
            else:
                for block in message.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
        elif isinstance(message, ResultMessage):
            result = message
    if err is not None:
        raise RuntimeError(f"{what} returned an error: {err}")
    if result is not None and result.subtype != "success":
        raise RuntimeError(f"{what} run ended with '{result.subtype}'.{subtype_hint}")
    return (result.result if result and result.result else "".join(parts)).strip()


_RATE_HINT = (" If this is a rate-limit you've hit your subscription's rolling cap — "
              "wait and retry, or set CLAUDE_MODEL to a cheaper alias.")

# Substrings that mark a TRANSIENT, retryable Claude failure (server hiccup / overload).
_TRANSIENT = ("server_error", "overloaded", "503", "502", "timeout")


def _is_transient(exc: Exception) -> bool:
    return any(s in str(exc).lower() for s in _TRANSIENT)


async def _claude_chat_async(system: str, user: str) -> str:
    from claude_agent_sdk import query

    options = _agent_options(CLAUDE_MODEL, system)
    return await _drain(query(prompt=user, options=options), subtype_hint=_RATE_HINT)


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
                "https://aistudio.google.com -> Get API key), or unset VERA_LLM to "
                "use the default Claude subscription brain."
            )
        genai.configure(api_key=key)
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
            "-> API keys), or unset VERA_LLM to use the default Claude subscription brain."
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

    This is the single seam the engine-side helpers and distiller talk through.
    Provider is chosen by PROVIDER (one place), defaulting to Claude. Keep the
    signature (system, user) -> str identical across providers so it stays a true
    drop-in swap.
    """
    fn = _PROVIDERS.get(PROVIDER, _chat_claude)  # unknown/unset -> Claude (default)
    return fn(system, user)


# ======================================================================
# THE VISION SEAM — frames -> a style profile (the judged layer)
# ======================================================================
STYLE_PROFILE_SYSTEM = (
    "You are a discerning Reference Analyst studying still frames pulled from a "
    "reference video. You translate taste into a concise STYLE PROFILE the rest of a "
    "video pipeline can aim at. You describe ONLY what you can see — you never invent "
    "detail you can't observe, and you say plainly when the frames don't show "
    "something. There is no script here, so do NOT judge narration or alignment."
)

STYLE_PROFILE_INSTRUCTION = (
    "Study these reference frames and return a STYLE PROFILE as a single JSON object "
    "with EXACTLY these keys (no prose outside the JSON):\n"
    '  "visual_style":          a short phrase for the overall look,\n'
    '  "typography_character":  what the on-screen text feels like (or "none visible"),\n'
    '  "motion_feel":           how kinetic/static it reads across the frames,\n'
    '  "mood":                  the emotional register,\n'
    '  "layout_types":          a list of the layout patterns you observe '
    '(e.g. "full-bleed", "split-screen", "talking-head", "lower-third"),\n'
    '  "summary":               one honest sentence tying it together.\n'
    "Describe only what the frames support; if a property isn't observable, say so."
)


def _extract_json(text: str) -> dict:
    """Best-effort: pull the first JSON object out of a model reply (tolerates fences/prose)."""
    if not text:
        raise ValueError("empty vision reply")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    blob = fenced.group(1) if fenced else None
    if blob is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON object in vision reply")
        blob = text[start:end + 1]
    return json.loads(blob)


def _image_blocks(frames: list[str]) -> list[dict]:
    """Read frame files into base64 image content blocks (jpeg/png by extension)."""
    blocks = []
    for p in frames[:MAX_VISION_FRAMES]:
        try:
            data = base64.standard_b64encode(open(p, "rb").read()).decode("ascii")
        except OSError as e:
            raise RuntimeError(f"couldn't read frame {p}: {e}") from e
        media = "image/png" if str(p).lower().endswith(".png") else "image/jpeg"
        blocks.append({"type": "image",
                       "source": {"type": "base64", "media_type": media, "data": data}})
    return blocks


async def _vision_claude_async(frames: list[str]) -> str:
    from claude_agent_sdk import query

    content = _image_blocks(frames) + [{"type": "text", "text": STYLE_PROFILE_INSTRUCTION}]
    options = _agent_options(VISION_MODEL, STYLE_PROFILE_SYSTEM)

    async def _input():
        yield {"type": "user", "message": {"role": "user", "content": content}}

    return await _drain(query(prompt=_input(), options=options), what="Vision")


def _vision_gemini(frames: list[str]) -> str:
    import google.generativeai as genai
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing — can't run the vision profile on gemini.")
    genai.configure(api_key=key)
    model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=STYLE_PROFILE_SYSTEM)
    payload = [STYLE_PROFILE_INSTRUCTION]
    for p in frames[:MAX_VISION_FRAMES]:
        media = "image/png" if str(p).lower().endswith(".png") else "image/jpeg"
        payload.append({"mime_type": media, "data": open(p, "rb").read()})
    resp = model.generate_content(payload)
    text = getattr(resp, "text", None)
    if not text:
        raise RuntimeError("Gemini returned an empty vision response.")
    return text.strip()


def vision_style_profile(frames: list[str]) -> dict:
    """frames -> a style-profile dict (the engine's judged seam).

    Raises on any degradation (no frames, no vision-capable brain, network/parse
    failure) — the engine catches it and records judged.status 'draft' + the note, so
    a missing profile NEVER crashes a rubric build.
    """
    frames = [f for f in (frames or []) if f]
    if not frames:
        raise RuntimeError("no frames were saved to read (cv2 unavailable or no video frames).")
    if PROVIDER == "deepseek":
        raise RuntimeError("VERA_LLM=deepseek has no vision seam — set VERA_LLM=claude "
                           "(or gemini) to score the style profile.")

    def _once() -> str:
        if PROVIDER == "gemini":
            return _vision_gemini(frames)
        _warn_if_metered()
        return asyncio.run(_vision_claude_async(frames))

    # A flaky moment (transient server_error / overload) shouldn't lose the whole style
    # profile: retry a couple of times with backoff before letting the engine degrade.
    for attempt in range(VISION_RETRIES + 1):
        try:
            raw = _once()
            break
        except RuntimeError as exc:
            if attempt < VISION_RETRIES and _is_transient(exc):
                time.sleep(VISION_RETRY_DELAY_SEC * (attempt + 1))
                continue
            raise
    profile = _extract_json(raw)
    profile.setdefault("frames_read", len(frames[:MAX_VISION_FRAMES]))
    return profile


def make_style_profiler():
    """Return the vision_fn to pass to `reference_engine.build_rubric(vision_fn=...)`."""
    return vision_style_profile


# ======================================================================
# MULTI-TURN SEAM — converse() for "Talk to Vera" (chat.py)
# ======================================================================
# Always runs on the Claude Agent SDK: the persona chat reuses the SDK loop. It is
# still provider-agnostic about STATE — the caller owns chat_state.json and hands us
# (system, summary, recent_turns, user_msg) every turn, so the durable memory
# survives a future brain swap.


def _render_turns(recent_turns) -> str:
    label = {"user": "User", "vera": "Vera"}
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


async def _converse_async(system, summary, recent_turns, user_msg, model) -> str:
    from claude_agent_sdk import query

    prompt_text = _build_chat_prompt(summary, recent_turns, user_msg)
    options = _agent_options(model or CHAT_MODEL, system)

    async def _input():
        yield {"type": "user", "message": {"role": "user", "content": prompt_text}}

    return await _drain(query(prompt=_input(), options=options),
                        what="Chat turn", subtype_hint=_RATE_HINT)


def converse(system: str, summary: str, recent_turns, user_msg: str,
             *, model: str | None = None) -> str:
    """Multi-turn chat seam (Claude SDK). Returns Vera's reply text."""
    _warn_if_metered()
    try:
        return asyncio.run(asyncio.wait_for(
            _converse_async(system, summary, recent_turns, user_msg, model),
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
