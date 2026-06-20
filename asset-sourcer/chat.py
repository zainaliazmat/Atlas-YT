"""Talk to Magpie — a conversational REPL over the same soul/identity.

Launch:  python run.py chat

Magpie talks like a person (persona from SOUL.md + STYLE.md + examples — NOT the SKILL
method/output contract), remembers you across sessions via a single distilled SUMMARY,
knows her past sourcing runs (memory.json), and can source a REAL asset_manifest
mid-conversation via an in-process tool you approve before it runs.

ONE JOB. Magpie owns one artifact, the asset_manifest:
    /source <path>   source + license the assets a storyboard needs   ([y/N] gate)
The job: search the allowlist + download-local in memory -> show a tight clearance
preview -> [y/N] -> only then write asset_manifest.json. The model can also trigger the
job mid-chat via a native tool; the SAME gate + preview fire. The [y/N] gate lives
HERE, in the REPL — Atlas runs the job gate-free through the adapter.

Memory model (summary-only — no transcript replay across sessions): identical to the
siblings. Across sessions Magpie's only long-term memory is a distilled summary in
chat_state.json; the raw transcript lives only in RAM and is distilled on every session
boundary (/exit, Ctrl+C, /new, /summary). A failed distill parks the raw turns under
"pending" so nothing is lost; the next launch folds them in.
"""
from __future__ import annotations

import asyncio
import itertools
import os
import pathlib
import signal
import sys
import threading
import time

from claude_agent_sdk import (
    tool,
    create_sdk_mcp_server,
    PermissionResultAllow,
    PermissionResultDeny,
)

import source_engine as engine
import chat_state
import compaction
import llm
import sources

HERE = pathlib.Path(__file__).parent
STATE_PATH = HERE / "chat_state.json"

# ----------------------------------------------------------------------
# Persona bundle (soul.md framework): SOUL = identity, STYLE = voice,
# examples/ = calibration. The source ENGINE reads ONLY SOUL.md (in fact it makes no
# LLM call at all); STYLE + examples are loaded HERE, into chat, so the voice never
# leaks into the engine. SKILL.md (the engine method) is never loaded into chat.
# ----------------------------------------------------------------------
SOUL_DIR = HERE / "soul"
SOUL = (SOUL_DIR / "SOUL.md").read_text()
STYLE = (SOUL_DIR / "STYLE.md").read_text()


def _load_examples() -> str:
    """Concatenate the calibration examples (good first, then bad) if present."""
    ex_dir = SOUL_DIR / "examples"
    parts = []
    for name in ("good-outputs.md", "bad-outputs.md"):
        p = ex_dir / name
        if p.exists():
            parts.append(p.read_text().strip())
    return "\n\n".join(parts)


EXAMPLES = _load_examples()

CONVERSATION_BUDGET_TOKENS = 8000  # headroom for summary + recent turns + new msg

# Native tool name (how the model references it) + a provider-agnostic marker.
SOURCE_TOOL_NAME = "mcp__magpie__source_assets"
SOURCE_MARKER = "MAGPIE_SOURCE:"

MAX_SNAPSHOT_RUNS = 5
DISTILL_TIMEOUT_SEC = 25


# ----------------------------------------------------------------------
# Persona system prompt — built from SOUL+STYLE+examples (NO SKILL method)
# ----------------------------------------------------------------------
CHAT_ADDENDUM = """
## Right now: a live conversation
You're talking with the user directly, in real time — not producing a manifest. Talk
like a real person with your expertise: dry, exact, provenance-first about RIGHTS. Talk
in license codes and rights statements (CC0, PDM, CC BY-SA 4.0, NoC-US, "no known
copyright restrictions"). Do NOT emit the structured JSON manifest in chat; that's for
the job. You source and clear assets — you don't design the look, write the script, or
build the scene HTML.

## What you remember (be accurate about this)
You keep a distilled summary of what matters about this collaborator across sessions —
the kinds of videos and topics they make, their channel and audience, the eras and
archives they keep reaching for, their licensing posture (monetized? attribution
tolerance? which jurisdictions matter), and rulings you've made together about what
clears — but NOT the word-for-word history of past chats. So you are NOT meeting them
for the first time and you do NOT start fresh every session: use the remembered context.
If asked what you remember, describe it honestly: a running summary of the important
stuff, not a transcript.

## Sourcing a manifest mid-chat
When the user has a storyboard ready, you can source the assets:
- the `source_assets` tool reads a storyboard (a project dir holding storyboard.json
  + style_guide.json, or a storyboard.json file), searches the allowlist, downloads
  cleared assets local, and writes asset_manifest.json.
The user approves before it runs, and you show a tight clearance preview first. When a
manifest comes back, walk it in your own voice — the count (cleared / sourced /
placeholder), then the ones that need a ruling: what cleared and under which license,
what you could only get to `sourced` and the carve-out you couldn't verify, and what's a
flagged placeholder with the query you'd retry. Don't dump the raw JSON.

## The one rule you never bend
Nothing is recorded as `cleared` without a verified accept-list license AND complete
attribution AND a local file. "No known copyright restrictions" is a reject. "Probably
PD" is a reject. A shrug doesn't survive a copyright strike — if you can't trace a
definite license, it ships as a flagged placeholder and you say so.
"""


def build_system_prompt(soul_text: str = SOUL, style_text: str = STYLE,
                        examples_text: str = EXAMPLES) -> str:
    """Magpie's chat identity: SOUL + STYLE + examples/ + live-conversation guidance.

    Deliberately excludes SKILL.md (the sourcing method / output contract) — that would
    make Magpie terse and robotic in chat. STYLE + examples are what make her sound like
    a person here.
    """
    parts = [soul_text.strip()]
    if style_text.strip():
        parts.append("# HOW YOU TALK (voice & style)\n\n" + style_text.strip())
    if examples_text.strip():
        parts.append(
            "# VOICE CALIBRATION (examples)\n\n"
            "These show how you sound right vs. off-character. Match the vibe of the "
            "good outputs; avoid the patterns in the bad ones. They are calibration, "
            "not scripts — never quote them verbatim.\n\n"
            + examples_text.strip())
    parts.append(CHAT_ADDENDUM.strip())
    return "\n\n".join(parts)


BUDGET_TOKENS = (compaction.estimate_tokens(build_system_prompt())
                 + CONVERSATION_BUDGET_TOKENS)


# ----------------------------------------------------------------------
# Distillation — the ONE memory helper, used on /exit, SIGINT, /new, /summary
# ----------------------------------------------------------------------
DISTILL_SYSTEM = (
    "You maintain the long-term memory of Magpie, a strict asset-sourcing and "
    "licensing researcher, about ONE collaborator she works with. That memory is a "
    "single distilled summary she reloads at the start of every session — so it must "
    "hold only what makes her sourcing and clearance calls land closer to this "
    "person's needs, in as few words as possible."
)


def _distill_prompt(existing_summary: str, transcript: list[dict[str, str]]) -> str:
    convo = compaction.transcript_text(transcript)
    return (
        "Here is the memory you already hold about the collaborator:\n"
        f"{existing_summary.strip() or '(nothing yet)'}\n\n"
        "Here is the full transcript of the session that just happened:\n"
        f"{convo}\n\n"
        "Rewrite the memory as a single clean, consolidated summary.\n\n"
        "KEEP only durable, craft-improving signal:\n"
        "- the kinds of videos / topics / channel they make\n"
        "- their audience, and the eras / archives / visual subjects they keep needing\n"
        "- their licensing posture: monetized or not, attribution tolerance, which "
        "jurisdictions matter, how strict they want clearance\n"
        "- rulings made about what clears (e.g. NC/ND, stock sites), looks that worked\n"
        "- anything about how they like to work with an asset sourcer\n\n"
        "DROP the junk: greetings and small talk ('thanks', 'lol'), off-topic "
        "questions and her deflections, jailbreak / identity-test exchanges, and "
        "anything transient.\n\n"
        "MERGE with the memory you already hold — do not replace it; knowledge "
        "accumulates across sessions. Resolve contradictions in favor of the MOST "
        "RECENT information.\n\n"
        "Keep it BOUNDED and consolidated: a few tight bullet groups, well under "
        "600 words. Output ONLY the updated summary — no preamble, no commentary. "
        "If the session contained nothing worth keeping, return the existing memory "
        "unchanged."
    )


def make_distiller(chat_fn=llm.chat):
    """Build distill(existing_summary, transcript) -> new_summary from a chat seam."""
    def distill(existing_summary: str, transcript: list[dict[str, str]]) -> str:
        existing = (existing_summary or "").strip()
        if not transcript:
            return existing
        new = chat_fn(DISTILL_SYSTEM, _distill_prompt(existing, transcript)).strip()
        return new or existing
    return distill


def _distill_with_timeout(distiller, summary, transcript, timeout):
    """Run `distiller(summary, transcript)` with a hard timeout (daemon thread)."""
    box: dict = {}

    def work():
        try:
            box["value"] = distiller(summary, transcript)
        except BaseException as exc:  # noqa: BLE001 — surfaced to the caller below
            box["error"] = exc

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError("distill timed out")
    if "error" in box:
        raise box["error"]
    return box["value"]


def distill_and_save(state, distiller, *, status: str | None = None,
                     timeout: float = DISTILL_TIMEOUT_SEC) -> bool:
    """Distill the session into the summary and persist ONLY the summary.

    On failure/timeout (NO DATA LOSS): the whole backlog is parked under "pending" in
    chat_state.json and the existing summary is kept; returns False.
    """
    backlog = (state.get("pending") or []) + state["transcript"]
    if not backlog:
        state["pending"] = None
        chat_state.save_summary(STATE_PATH, state["summary"])
        return True

    if status:
        print(status)
    try:
        new_summary = _distill_with_timeout(distiller, state["summary"], backlog,
                                            timeout)
    except BaseException:  # noqa: BLE001 — any failure must not lose the chat
        state["pending"] = backlog
        chat_state.save_summary(STATE_PATH, state["summary"], pending=backlog)
        return False

    state["summary"] = new_summary
    state["pending"] = None
    state["transcript"] = []
    chat_state.save_summary(STATE_PATH, new_summary)
    return True


def _recover_pending(state, distiller) -> None:
    """On launch, fold any "pending" raw transcript (failed prior distill) in."""
    pending = state.get("pending")
    if not pending:
        return
    print("💾 Recovering an unsaved session from last time…")
    try:
        state["summary"] = _distill_with_timeout(distiller, state["summary"],
                                                 pending, DISTILL_TIMEOUT_SEC)
    except BaseException:  # noqa: BLE001 — keep pending for next time, don't crash
        print("   (couldn't fold it in just now — I'll retry next launch.)")
        return
    state["pending"] = None
    chat_state.save_summary(STATE_PATH, state["summary"])


# ----------------------------------------------------------------------
# Memory awareness — a small, capped snapshot of past sourcing runs
# ----------------------------------------------------------------------
def memory_snapshot(mem: dict) -> str:
    """A compact, clearly-labeled view of past sourcing runs for Magpie's context."""
    runs = list(mem.get("runs", []))[-MAX_SNAPSHOT_RUNS:]
    if not runs:
        return ""
    items = []
    for r in runs:
        items.append(f"{r.get('scenes','?')} scenes → {r.get('cleared',0)} cleared / "
                     f"{r.get('sourced',0)} sourced / {r.get('placeholder',0)} placeholder")
    return "[Your sourcing memory]\nRecent runs: " + "; ".join(items)


# ----------------------------------------------------------------------
# Tight preview (shown BEFORE writing) + the post-run digest
# ----------------------------------------------------------------------
def format_manifest_preview(manifest: dict) -> str:
    st = engine.manifest_stats(manifest)
    lines = ["ASSET MANIFEST — preview",
             f"  {st['total']} assets — {st['cleared']} cleared, {st['sourced']} sourced, "
             f"{st['placeholder']} placeholder"]
    for a in manifest.get("assets", []):
        tag = {"cleared": "✓", "sourced": "~", "placeholder": "·"}.get(a.get("status"), "?")
        flag = f"  ⚑ {a.get('flag')}" if a.get("flag") else ""
        lines.append(f"   {tag} {a.get('asset_id')} (sc{a.get('scene_no')}) "
                     f"{a.get('type')} · {a.get('source')} · {a.get('license')}{flag}")
    return "\n".join(lines)


def format_digest(manifest: dict, json_path) -> str:
    return f"{format_manifest_preview(manifest)}\n  saved (for the next agent): {json_path}"


# ----------------------------------------------------------------------
# Compute (search + download) + persist — separated so we PREVIEW before we WRITE
# ----------------------------------------------------------------------
def _resolve_pdir(path: str) -> pathlib.Path:
    """Where downloads + the manifest land: the project dir if `path` is one, else a
    timestamped folder under manifests/."""
    p = pathlib.Path(path).expanduser()
    if p.is_dir():
        return p
    engine.MANIFESTS_DIR.mkdir(exist_ok=True)
    pdir = engine.MANIFESTS_DIR / f"{engine._slug(p.stem)}-{time.strftime('%Y%m%d-%H%M%S')}"
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir


def compute_manifest(path: str) -> tuple[dict, pathlib.Path]:
    """Source assets in memory (downloads land local; manifest NOT yet written).

    Raises ValueError on an unusable storyboard.
    """
    storyboard = engine.load_storyboard(path)
    style_guide = engine.load_style_guide(path) or None
    ok, reason = engine.validate_storyboard(storyboard)
    if not ok:
        raise ValueError(reason)
    pdir = _resolve_pdir(path)
    manifest = engine.source_assets(storyboard, style_guide,
                                    client=sources.SourceClient(), pdir=pdir)
    stamped = {"schema_version": engine.SCHEMA_VERSION, **manifest}
    return stamped, pdir


def persist(manifest: dict, pdir: pathlib.Path) -> pathlib.Path:
    """Write the stamped manifest to the project dir + log the run."""
    out = pathlib.Path(pdir) / "asset_manifest.json"
    chat_state.atomic_write_json(out, manifest)
    storyboard = engine.load_storyboard(pdir)
    engine._log_run(storyboard, engine.manifest_stats(manifest))
    return out


# ----------------------------------------------------------------------
# The gated job — compute -> preview -> [y/N] -> write. (Synchronous; the native
# tool calls this off the SDK loop via asyncio.to_thread.)
# ----------------------------------------------------------------------
def run_source_job(path: str, *, gate: bool) -> str | None:
    """Source job with an optional [y/N] gate AFTER a tight preview. None if declined."""
    manifest, pdir = compute_manifest(path)
    print("\n" + format_manifest_preview(manifest))
    if gate and not ask_yes_no("\n🗂️  Write this asset manifest? [y/N] "):
        return None
    json_path = persist(manifest, pdir)
    return format_digest(manifest, json_path)


# ----------------------------------------------------------------------
# Strict marker parsing (provider-agnostic fallback trigger)
# ----------------------------------------------------------------------
def _parse_marker(text: str, marker: str) -> str | None:
    """Return the path iff `text` ends with a single, exact `marker` line."""
    lines = text.splitlines()
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty:
        return None
    marker_lines = [ln for ln in lines if ln.strip().startswith(marker)]
    if len(marker_lines) != 1:
        return None
    if marker_lines[0].strip() != nonempty[-1].strip():
        return None
    path = marker_lines[0].strip()[len(marker):].strip()
    return path or None


def parse_magpie_request(text: str) -> str | None:
    """Return the path for a clean trailing MAGPIE_SOURCE: marker, else None."""
    return _parse_marker(text, SOURCE_MARKER)


def strip_magpie_request(text: str) -> str:
    """Remove any marker line so it isn't shown to the user."""
    kept = [ln for ln in text.splitlines()
            if not ln.strip().startswith(SOURCE_MARKER)]
    return "\n".join(kept).strip()


# ----------------------------------------------------------------------
# Approval gate
# ----------------------------------------------------------------------
def ask_yes_no(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# ----------------------------------------------------------------------
# "Thinking…" indicator
# ----------------------------------------------------------------------
_spinner: tuple | None = None


def _start_thinking(label: str = "Magpie is digging") -> None:
    global _spinner
    if _spinner is not None:
        return
    stop = threading.Event()

    def run():
        for ch in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
            if stop.is_set():
                break
            print(f"\r{label}… {ch} ", end="", flush=True)
            time.sleep(0.08)
        print("\r" + " " * (len(label) + 6) + "\r", end="", flush=True)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    _spinner = (stop, t)


def _stop_thinking() -> None:
    global _spinner
    if _spinner is None:
        return
    stop, t = _spinner
    _spinner = None
    stop.set()
    t.join(timeout=1)


# ----------------------------------------------------------------------
# Native tool + approval callback (Claude path). The gate is the in-body preview +
# [y/N] (run_source_job), run off the SDK loop via to_thread; can_use_tool just admits
# Magpie's own tool so that body-gate fires.
# ----------------------------------------------------------------------
@tool("source_assets", "Source + license the assets a storyboard needs. Pass a project "
      "directory holding storyboard.json + style_guide.json, or a storyboard.json path.",
      {"path": str})
async def source_assets_tool(args):
    path = (args.get("path") or "").strip()
    try:
        digest = await asyncio.to_thread(run_source_job, path, gate=True)
    except Exception as exc:  # keep the conversation alive on any failure
        return {"content": [{"type": "text", "text": f"Couldn't source the assets: {exc}"}],
                "is_error": True}
    if digest is None:
        return {"content": [{"type": "text",
                             "text": f"The user declined to write the asset manifest for "
                                     f"{path!r} right now."}]}
    return {"content": [{"type": "text", "text": f"Asset manifest written:\n{digest}"}]}


async def can_use_tool(name, inp, ctx):
    """Admit Magpie's own tool (its body holds the preview + [y/N] gate); deny others."""
    _stop_thinking()
    if name == SOURCE_TOOL_NAME:
        path = (inp.get("path") or "").strip()
        if not path:
            return PermissionResultDeny(behavior="deny",
                                        message="No storyboard path was given to source from.",
                                        interrupt=False)
        return PermissionResultAllow(behavior="allow", updated_input=inp)
    return PermissionResultDeny(behavior="deny",
                                message="That tool isn't allowed here.",
                                interrupt=False)


_MAGPIE_SERVER = create_sdk_mcp_server("magpie", tools=[source_assets_tool])
MAGPIE_WIRING = {"server": _MAGPIE_SERVER, "can_use_tool": can_use_tool}


# ----------------------------------------------------------------------
# Context assembly + a single conversational turn
# ----------------------------------------------------------------------
def _context_summary(state: dict, snapshot: str) -> str:
    parts = [p for p in (state["summary"].strip(), snapshot.strip()) if p]
    return "\n\n".join(parts)


def _send(state, system, summarizer, snapshot, user_msg, *, magpie):
    """Compact if needed, call the model, return Magpie's reply text (or None)."""
    _start_thinking()
    try:
        info = compaction.compact(
            state, summarizer=summarizer, system=system, extra=snapshot,
            pending_user_msg=user_msg, budget=BUDGET_TOKENS)
        if not info["fits"]:
            _stop_thinking()
            print("⚠️  " + info["reason"])
            return None
        summary = _context_summary(state, snapshot)
        return llm.converse(system, summary, state["transcript"], user_msg, magpie=magpie)
    finally:
        _stop_thinking()


def handle_message(state, system, summarizer, user_msg):
    """One user message -> Magpie's reply, kept in the in-RAM transcript only."""
    mem = engine.load_memory()
    snapshot = memory_snapshot(mem)
    try:
        reply = _send(state, system, summarizer, snapshot, user_msg, magpie=MAGPIE_WIRING)
    except Exception as exc:
        print(f"\n(Magpie hit a problem: {exc}\n Try again, or /new if it persists.)")
        return
    if reply is None:
        return

    req = parse_magpie_request(reply)
    display = strip_magpie_request(reply) if req else reply
    print(f"\nMagpie: {display}")

    chat_state.append_turn(state, "user", user_msg)
    chat_state.append_turn(state, "magpie", display or reply)

    # Fallback path: the model emitted a marker instead of calling a tool.
    if req:
        _job_then_discuss(state, system, summarizer, req, gate=True)


def _job_then_discuss(state, system, summarizer, path, *, gate):
    """Run the gated sourcing job and let Magpie report it in voice."""
    try:
        digest = run_source_job(path, gate=gate)
    except Exception as exc:
        print(f"   (couldn't source the manifest: {exc})")
        return
    if digest is None:
        feedback = (f"[note] The user declined to write the asset manifest from {path!r}. "
                    "Acknowledge and keep talking.")
    else:
        feedback = (f"[asset manifest sourced from {path!r}]\n{digest}\n"
                    "Report this to the user in your own voice — the count (cleared / "
                    "sourced / placeholder), then the ones that need a ruling: what "
                    "cleared and under which license, what's only `sourced` and the "
                    "carve-out you couldn't verify, and any flagged placeholders with "
                    "the query you'd retry.")

    mem = engine.load_memory()
    snapshot = memory_snapshot(mem)
    try:
        reply = _send(state, system, summarizer, snapshot, feedback, magpie=None)
    except Exception as exc:
        print(f"\n(Magpie couldn't report the manifest: {exc})")
        return
    if reply:
        print(f"\nMagpie: {reply}")
        chat_state.append_turn(state, "user", feedback)
        chat_state.append_turn(state, "magpie", reply)


# ----------------------------------------------------------------------
# Slash commands
# ----------------------------------------------------------------------
HELP = """Commands:
  /source <path>  source + license a storyboard's assets (project dir or storyboard.json)
  /summary        distill the session so far, then show what Magpie remembers
  /new            distill + start a fresh thread (keeps what Magpie knows about you)
  /help           show this
  /exit           save (distill) and quit
Anything else is just conversation."""


def handle_command(state, system, summarizer, distiller, raw) -> bool:
    """Return True to keep looping, False to exit."""
    parts = raw.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit"):
        distill_and_save(state, distiller, status="💾 Saving session summary…")
        print("Saved. Talk soon.")
        return False
    if cmd == "/help":
        print(HELP)
    elif cmd == "/summary":
        ok = distill_and_save(state, distiller, status="💾 Updating what I remember…")
        body = state["summary"].strip() or "(nothing worth remembering yet)"
        print("\n[What Magpie remembers about you]\n" + body)
        if not ok:
            print("(I couldn't fully update just now — kept what I had; your chat "
                  "is safe and I'll fold it in next launch.)")
    elif cmd == "/new":
        distill_and_save(state, distiller,
                         status="💾 Saving what matters before clearing the thread…")
        state["transcript"] = []
        print("Fresh thread. I've folded this chat into what I remember about you "
              "— your topics, your archives, your licensing posture; the back-and-forth "
              "is cleared.")
    elif cmd == "/source":
        if not arg:
            print("Usage: /source <project_dir or storyboard.json>")
        else:
            # Explicit command: typing it IS the approval for the WRITE, but Magpie
            # still previews first. (The [y/N] gate lives on the model-initiated
            # tool + marker path.)
            _job_then_discuss(state, system, summarizer, arg, gate=False)
    else:
        print(f"Unknown command {cmd!r}. /help for the list.")
    return True


# ----------------------------------------------------------------------
# Graceful Ctrl+C (SIGINT) handling
# ----------------------------------------------------------------------
_SESSION: dict = {"state": None, "distiller": None, "interrupting": False}


def _flush_pending_and_die(state) -> None:
    try:
        backlog = (state.get("pending") or []) + state["transcript"]
        if backlog:
            chat_state.save_summary(STATE_PATH, state["summary"], pending=backlog)
    finally:
        os._exit(130)


def _sigint_handler(signum, frame):
    ctx = _SESSION
    if ctx.get("interrupting"):
        _flush_pending_and_die(ctx["state"])
    ctx["interrupting"] = True
    distill_and_save(ctx["state"], ctx["distiller"],
                     status="\n💾 Saving session summary…  (Ctrl+C again to skip)")
    print("Saved. Talk soon.")
    sys.exit(0)


# ----------------------------------------------------------------------
# REPL
# ----------------------------------------------------------------------
def start():
    system = build_system_prompt()
    state = chat_state.load_state(STATE_PATH)
    summarizer = compaction.make_summarizer(llm.chat)
    distiller = make_distiller()

    _SESSION.update(state=state, distiller=distiller, interrupting=False)
    signal.signal(signal.SIGINT, _sigint_handler)
    _recover_pending(state, distiller)

    print("=" * 64)
    print("Talk to Magpie.  /help for commands, /exit to leave.")
    if state["summary"].strip():
        print("(Magpie remembers what matters about your work from before — pick up "
              "wherever you like.)")
    print("=" * 64)

    while True:
        try:
            user = input("\nYou: ").strip()
        except EOFError:  # Ctrl+D — save and leave gracefully
            print()
            distill_and_save(state, distiller, status="💾 Saving session summary…")
            print("Saved. Talk soon.")
            break
        if not user:
            continue
        if user.startswith("/"):
            if not handle_command(state, system, summarizer, distiller, user):
                break
            continue
        handle_message(state, system, summarizer, user)


if __name__ == "__main__":
    start()
