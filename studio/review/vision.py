"""studio.review.vision — the ONE vision seam the review's critics call.

``vision_chat(system, user, image_paths) -> str`` is the review's equivalent of
``atlas/llm.chat`` (text-only) but with EYES: it sends the system+user prompt PLUS a set
of sampled frames to Claude and returns the text reply. This is what lets the review
"look at the result" (GOLDEN_REFERENCE.md anti-pattern #3) rather than score a digest.

DEFAULT provider, exactly as the rest of the fleet: **Claude on your Claude Code
SUBSCRIPTION** via ``claude_agent_sdk`` — no API key, no metered billing (a set
``ANTHROPIC_API_KEY`` would silently switch the SDK to the metered API, so we warn,
matching atlas/llm). Images ride in as base64 content blocks on a streamed user message
(the SDK's ``AsyncIterable[dict]`` prompt form), which is the only way to pass images
through the subscription transport.

The seam is INJECTABLE everywhere it's used (``critics.run_critics(vision_fn=...)``,
``evidence.polish_vs_reference(vision_fn=...)``) so the whole review is offline-testable
with a fake that never touches the network. Heavy imports are lazy so ``import studio``
stays cheap.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import time
import warnings
from pathlib import Path

# Vision-capable subscription model (same family atlas/llm uses for reasoning).
VISION_MODEL = "claude-sonnet-4-6"
VISION_TIMEOUT_SEC = 240
_TRANSIENT = ("server_error", "overloaded", "connection", "timeout",
              "500", "502", "503", "529")
_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif"}


def _warn_if_metered() -> None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        warnings.warn(
            "ANTHROPIC_API_KEY is set, so the Agent SDK will bill the metered API "
            "rather than your subscription. Unset it to use your subscription.",
            stacklevel=2,
        )


def _image_block(path: str) -> dict | None:
    p = Path(path)
    if not p.is_file():
        return None
    media = _MEDIA_TYPES.get(p.suffix.lower())
    if not media:
        return None
    try:
        data = base64.b64encode(p.read_bytes()).decode("ascii")
    except Exception:
        return None
    return {"type": "image",
            "source": {"type": "base64", "media_type": media, "data": data}}


def vision_chat(system: str, user: str, image_paths: list[str] | None = None, *,
                model: str | None = None) -> str:
    """Send system+user+frames to Claude (subscription) and return the text reply.

    ``image_paths`` are read, base64-encoded, and attached as image content blocks on a
    streamed user message. Retries transient API hiccups with backoff (mirrors
    atlas/llm). Raises on non-transient errors — callers (critics/polish) catch and
    degrade so one failing lens never crashes the review."""
    _warn_if_metered()
    last = None
    for attempt in range(4):
        try:
            return asyncio.run(_vision_async(system, user, image_paths or [], model=model))
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt == 3 or not any(t in str(e).lower() for t in _TRANSIENT):
                raise
            time.sleep(1.5 * (2 ** attempt))
    raise last  # pragma: no cover


async def _vision_async(system: str, user: str, image_paths: list[str],
                        model: str | None = None) -> str:
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    from claude_agent_sdk.types import AssistantMessage, TextBlock, ResultMessage

    content: list[dict] = [{"type": "text", "text": user}]
    for path in image_paths:
        block = _image_block(path)
        if block:
            content.append(block)

    async def _stream():
        yield {"type": "user",
               "message": {"role": "user", "content": content},
               "parent_tool_use_id": None}

    options = ClaudeAgentOptions(model=model or VISION_MODEL,
                                 system_prompt=system, tools=[])
    parts: list[str] = []
    result = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query(_stream())
        async for message in client.receive_response():
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
            f"Vision run ended with '{result.subtype}'. If this is a rate-limit, "
            "you've hit your subscription's rolling cap — wait and retry.")
    final = (result.result if result and result.result else "".join(parts))
    return (final or "").strip()


# ----------------------------------------------------------------------
# JSON extraction — critics reply with a JSON object; tolerate fences/preamble
# ----------------------------------------------------------------------
def extract_json(text: str):
    """Best-effort: pull the first JSON object/array out of an LLM reply (tolerating
    ```json fences and prose). Returns the parsed value or None."""
    import json
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    candidate = fence.group(1).strip() if fence else text.strip()
    try:
        return json.loads(candidate)
    except Exception:
        pass
    # fall back to the first balanced {...} or [...] span
    for opener, closer in (("{", "}"), ("[", "]")):
        start = candidate.find(opener)
        end = candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start:end + 1])
            except Exception:
                continue
    return None
