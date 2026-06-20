"""Talk to Cadence — a conversational REPL over the same soul/identity.

Launch:  python run.py chat

Cadence talks like a person (persona from SOUL.md + STYLE.md + examples — NOT the SKILL
method/output contract), remembers you across sessions via a single distilled SUMMARY,
knows her past mix runs (memory.json), and can record narration or mix the audio mid-
conversation via in-process tools you approve before they run.

TWO JOBS. Cadence owns the audio trio across two jobs:
    /narrate <path>   per-scene tts -> narration.wav + transcript   ([y/N] gate)
    /mix <path>       source bed + place accent -> master.wav + manifest  ([y/N] gate)
Each job: compute (the tts / sourcing / mix spend) -> show a tight preview -> [y/N] ->
only then write the JSON artifact. The model can also trigger a job mid-chat via a
native tool; the SAME gate + preview fire. The [y/N] gate lives HERE, in the REPL —
Atlas runs the jobs gate-free through the adapter.

Memory model (summary-only — no transcript replay across sessions): identical to the
siblings. Across sessions Cadence's only long-term memory is a distilled summary in
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

import audio_engine as engine
import chat_state
import compaction
import llm

HERE = pathlib.Path(__file__).parent
STATE_PATH = HERE / "chat_state.json"

# ----------------------------------------------------------------------
# Persona bundle (soul.md framework): SOUL = identity, STYLE = voice,
# examples/ = calibration. The audio ENGINE reads only SOUL.md for identity; STYLE +
# examples are loaded HERE, into chat, so the voice never leaks into the engine.
# SKILL.md (the engine method) is never loaded into chat.
# ----------------------------------------------------------------------
SOUL_DIR = HERE / "soul"
SOUL = (SOUL_DIR / "SOUL.md").read_text()
STYLE = (SOUL_DIR / "STYLE.md").read_text()


def _load_examples() -> str:
    ex_dir = SOUL_DIR / "examples"
    parts = []
    for name in ("good-outputs.md", "bad-outputs.md"):
        p = ex_dir / name
        if p.exists():
            parts.append(p.read_text().strip())
    return "\n\n".join(parts)


EXAMPLES = _load_examples()

CONVERSATION_BUDGET_TOKENS = 8000

MAX_SNAPSHOT_RUNS = 5
DISTILL_TIMEOUT_SEC = 25

# Native tool names (how the model references them) + provider-agnostic markers.
NARRATE_TOOL_NAME = "mcp__cadence__record_narration"
MIX_TOOL_NAME = "mcp__cadence__mix_audio"
NARRATE_MARKER = "CADENCE_NARRATE:"
MIX_MARKER = "CADENCE_MIX:"


# ----------------------------------------------------------------------
# Persona system prompt — built from SOUL+STYLE+examples (NO SKILL method)
# ----------------------------------------------------------------------
CHAT_ADDENDUM = """
## Right now: a live conversation
You're talking with the user directly, in real time — not producing a mix. Talk like a
real person with your expertise: dry, exact, levels-in-numbers. Talk in dBFS, LUFS, duck
depth, the cut, the accent. Do NOT emit the structured JSON manifest in chat; that's for
the job. You voice and mix audio — you don't write the script, design the look, or build
the scene HTML.

## What you remember (be accurate about this)
You keep a distilled summary of what matters about this collaborator across sessions —
the kinds of videos they make, the Kokoro voice and pace they like, the music moods and
beds that have worked, their loudness and ducking posture, rulings you've made together
about what audio clears — but NOT the word-for-word history of past chats. So you are NOT
meeting them for the first time and you do NOT start fresh every session: use the
remembered context. If asked what you remember, describe it honestly: a running summary
of the important stuff, not a transcript.

## Running a job mid-chat
When the user has a script (and ideally a style guide + storyboard) ready, you can:
- `record_narration` — per-scene tts -> narration.wav + the transcript (the timing clock);
- `mix_audio` — source a cleared bed, place the one signature accent, pre-mix master.wav,
  and write the audio manifest.
The user approves before either runs, and you show a tight preview first. When a result
comes back, walk it in your own voice — for narration: the scene count, the total, the
per-scene timing. For the mix: the tracks and their levels (VO at reference, bed ducked
under at −N dB, the accent on the cut at T seconds), what cleared and under which
license, what's a flagged placeholder excluded from the master, and that the three
total_duration values agree. Don't dump the raw JSON.

## The one rule you never bend
Nothing uncleared gets baked into the master. The VO is authoritative and the bed ducks
hard under it. One accent on the signature beat — a second is gilding, and you cut it. If
a bed can't clear, it ships as a flagged placeholder and the master runs VO-plus-accent —
and you say so.
"""


def build_system_prompt(soul_text: str = SOUL, style_text: str = STYLE,
                        examples_text: str = EXAMPLES) -> str:
    """Cadence's chat identity: SOUL + STYLE + examples/ + live-conversation guidance.

    Deliberately excludes SKILL.md (the audio method / output contract) — that would
    make Cadence terse and robotic in chat. STYLE + examples are what make her a person.
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
    "You maintain the long-term memory of Cadence, an audio / sound designer who voices "
    "narration and mixes documentary-style explainers, about ONE collaborator she works "
    "with. That memory is a single distilled summary she reloads at the start of every "
    "session — so it must hold only what makes her audio calls land closer to this "
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
        "- their audience, the Kokoro voice + pace they prefer\n"
        "- the music moods / beds that have worked, their loudness + ducking posture\n"
        "- rulings made about what audio clears, accents that landed\n"
        "- anything about how they like to work with an audio designer\n\n"
        "DROP the junk: greetings and small talk ('thanks', 'lol'), off-topic questions "
        "and her deflections, jailbreak / identity-test exchanges, and anything "
        "transient.\n\n"
        "MERGE with the memory you already hold — do not replace it; knowledge "
        "accumulates across sessions. Resolve contradictions in favor of the MOST RECENT "
        "information.\n\n"
        "Keep it BOUNDED and consolidated: a few tight bullet groups, well under 600 "
        "words. Output ONLY the updated summary — no preamble, no commentary. If the "
        "session contained nothing worth keeping, return the existing memory unchanged."
    )


def make_distiller(chat_fn=llm.chat):
    def distill(existing_summary: str, transcript: list[dict[str, str]]) -> str:
        existing = (existing_summary or "").strip()
        if not transcript:
            return existing
        new = chat_fn(DISTILL_SYSTEM, _distill_prompt(existing, transcript)).strip()
        return new or existing
    return distill


def _distill_with_timeout(distiller, summary, transcript, timeout):
    box: dict = {}

    def work():
        try:
            box["value"] = distiller(summary, transcript)
        except BaseException as exc:  # noqa: BLE001
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
    backlog = (state.get("pending") or []) + state["transcript"]
    if not backlog:
        state["pending"] = None
        chat_state.save_summary(STATE_PATH, state["summary"])
        return True
    if status:
        print(status)
    try:
        new_summary = _distill_with_timeout(distiller, state["summary"], backlog, timeout)
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
    pending = state.get("pending")
    if not pending:
        return
    print("💾 Recovering an unsaved session from last time…")
    try:
        state["summary"] = _distill_with_timeout(distiller, state["summary"],
                                                 pending, DISTILL_TIMEOUT_SEC)
    except BaseException:  # noqa: BLE001
        print("   (couldn't fold it in just now — I'll retry next launch.)")
        return
    state["pending"] = None
    chat_state.save_summary(STATE_PATH, state["summary"])


# ----------------------------------------------------------------------
# Memory awareness — a small, capped snapshot of past mix runs
# ----------------------------------------------------------------------
def memory_snapshot(mem: dict) -> str:
    runs = list(mem.get("runs", []))[-MAX_SNAPSHOT_RUNS:]
    if not runs:
        return ""
    items = []
    for r in runs:
        items.append(f"{r.get('scenes','?')} scenes → {r.get('tracks',0)} tracks, "
                     f"master {'yes' if r.get('master') else 'no'}")
    return "[Your mix memory]\nRecent runs: " + "; ".join(items)


# ----------------------------------------------------------------------
# Previews (shown BEFORE writing) + digests
# ----------------------------------------------------------------------
def format_transcript_preview(out: dict) -> str:
    tr = out["transcript"]
    lines = ["NARRATION — preview",
             f"  {len(tr['segments'])} scenes, {tr['total_duration_sec']}s total "
             f"→ {out['narration_wav']}"]
    for s in tr["segments"][:12]:
        lines.append(f"   scene {s['scene_no']}: {s['start_sec']}–{s['end_sec']}s")
    return "\n".join(lines)


def format_manifest_preview(manifest: dict) -> str:
    st = engine.manifest_stats(manifest)
    lines = ["AUDIO MANIFEST — preview",
             f"  {st['tracks']} tracks ({st['music']} music, {st['sfx']} sfx) · "
             f"total {manifest.get('total_duration_sec')}s · "
             f"master {'rendered' if st['master'] else 'NOT rendered'}"]
    for t in manifest.get("tracks", []):
        tag = {"cleared": "✓", "sourced": "~", "placeholder": "·"}.get(t.get("status"), "?")
        duck = (f" duck:{t['ducking']}" if t.get("ducking") not in (False, None) else "")
        at = f" @{t['at_sec']}s" if t.get("at_sec") is not None else ""
        flag = f"  ⚑ {t.get('flag')}" if t.get("flag") else ""
        lines.append(f"   {tag} {t.get('role')}: {t.get('uri')} · "
                     f"{t.get('gain_db')}dB{duck}{at} · {t.get('license')}{flag}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Compute (the spend) + persist — separated so we PREVIEW before we WRITE
# ----------------------------------------------------------------------
def compute_narrate(path: str) -> tuple[dict, pathlib.Path]:
    """Synthesize narration in place (tts + concat); transcript NOT yet written."""
    pdir = engine._resolve_pdir(path)
    script = engine.load_script(pdir if pdir == pathlib.Path(path).expanduser()
                                else path)
    ok, reason = engine.validate_script(script)
    if not ok:
        raise ValueError(reason)
    out = engine.record_narration(script, pdir=pdir, voice=engine.VOICE_DEFAULT)
    return out, pdir


def persist_narrate(out: dict, pdir: pathlib.Path) -> pathlib.Path:
    p = pathlib.Path(pdir) / engine.AUDIO_SUBDIR / "narration.transcript.json"
    chat_state.atomic_write_json(p, out["transcript"])
    return p


def compute_mix(path: str) -> tuple[dict, pathlib.Path]:
    """Source the bed, place the accent, pre-mix master.wav; manifest NOT yet written."""
    pdir = engine._resolve_pdir(path)
    script = engine.load_script(pdir if pdir == pathlib.Path(path).expanduser()
                                else path)
    ok, reason = engine.validate_script(script)
    if not ok:
        raise ValueError(reason)
    style = engine._load_beside(pdir, "style_guide.json") or None
    storyboard = engine._load_beside(pdir, "storyboard.json") or None
    transcript = chat_state.load_json(
        pdir / engine.AUDIO_SUBDIR / "narration.transcript.json", {})
    if not transcript.get("segments"):
        out = engine.record_narration(script, pdir=pdir, voice=engine.VOICE_DEFAULT)
        persist_narrate(out, pdir)
        transcript = out["transcript"]
    res = engine.mix_audio(script, style, storyboard, transcript, pdir=pdir)
    return res["manifest"], pdir


def persist_mix(manifest: dict, pdir: pathlib.Path) -> pathlib.Path:
    p = pathlib.Path(pdir) / engine.AUDIO_SUBDIR / "audio_manifest.json"
    chat_state.atomic_write_json(p, manifest)
    engine._log_run(engine.load_script(pdir), engine.manifest_stats(manifest))
    return p


# job verb -> (compute, preview, persist, write-prompt, digest-instruction)
JOBS = {
    "narrate": (compute_narrate, format_transcript_preview, persist_narrate,
                "🎙️  Write this narration + transcript?",
                "Report it in your own voice — scene count, total, the per-scene timing."),
    "mix": (compute_mix, format_manifest_preview, persist_mix,
            "🎚️  Write this audio manifest?",
            "Report it in your own voice — the tracks and their levels (VO reference, "
            "bed ducked under at −N, accent on the cut at T), what cleared and under "
            "which license, any flagged placeholder excluded from the master, and that "
            "the three total_duration values agree."),
}


def run_job(verb: str, path: str, *, gate: bool) -> str | None:
    """Run a gated job: compute -> preview -> [y/N] -> write. None if declined."""
    compute, preview, persist, prompt, _ = JOBS[verb]
    result, pdir = compute(path)
    print("\n" + preview(result))
    if gate and not ask_yes_no(f"\n{prompt} [y/N] "):
        return None
    json_path = persist(result, pdir)
    return f"{preview(result)}\n  saved (for the next agent): {json_path}"


# ----------------------------------------------------------------------
# Strict marker parsing (provider-agnostic fallback trigger)
# ----------------------------------------------------------------------
def _parse_marker(text: str, marker: str) -> str | None:
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


def parse_cadence_request(text: str) -> tuple[str, str] | None:
    """Return (verb, path) for a clean trailing marker, else None."""
    for marker, verb in ((MIX_MARKER, "mix"), (NARRATE_MARKER, "narrate")):
        path = _parse_marker(text, marker)
        if path:
            return verb, path
    return None


def strip_cadence_request(text: str) -> str:
    kept = [ln for ln in text.splitlines()
            if not (ln.strip().startswith(NARRATE_MARKER)
                    or ln.strip().startswith(MIX_MARKER))]
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


def _start_thinking(label: str = "Cadence is listening") -> None:
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
# Native tools + approval callback (Claude path). The gate is the in-body preview +
# [y/N] (run_job), run off the SDK loop via to_thread; can_use_tool just admits
# Cadence's own tools so that body-gate fires.
# ----------------------------------------------------------------------
@tool("record_narration", "Synthesize per-scene narration + the transcript from a "
      "script. Pass a project directory holding script.json, or a script.json path.",
      {"path": str})
async def record_narration_tool(args):
    return await _run_tool("narrate", args)


@tool("mix_audio", "Source a cleared music bed, place the signature SFX accent, pre-mix "
      "master.wav, and write the audio manifest. Pass a project directory (script.json "
      "+ style_guide.json + storyboard.json) or a script.json path.", {"path": str})
async def mix_audio_tool(args):
    return await _run_tool("mix", args)


async def _run_tool(verb: str, args):
    path = (args.get("path") or "").strip()
    try:
        digest = await asyncio.to_thread(run_job, verb, path, gate=True)
    except Exception as exc:  # keep the conversation alive on any failure
        return {"content": [{"type": "text", "text": f"Couldn't {verb} the audio: {exc}"}],
                "is_error": True}
    if digest is None:
        return {"content": [{"type": "text",
                             "text": f"The user declined to write the {verb} artifact for "
                                     f"{path!r} right now."}]}
    return {"content": [{"type": "text", "text": f"Audio {verb} written:\n{digest}"}]}


async def can_use_tool(name, inp, ctx):
    """Admit Cadence's own tools (their body holds the preview + [y/N]); deny others."""
    _stop_thinking()
    if name in (NARRATE_TOOL_NAME, MIX_TOOL_NAME):
        path = (inp.get("path") or "").strip()
        if not path:
            return PermissionResultDeny(behavior="deny",
                                        message="No script path was given to work from.",
                                        interrupt=False)
        return PermissionResultAllow(behavior="allow", updated_input=inp)
    return PermissionResultDeny(behavior="deny",
                                message="That tool isn't allowed here.",
                                interrupt=False)


_CADENCE_SERVER = create_sdk_mcp_server("cadence",
                                        tools=[record_narration_tool, mix_audio_tool])
CADENCE_WIRING = {"server": _CADENCE_SERVER, "can_use_tool": can_use_tool}


# ----------------------------------------------------------------------
# Context assembly + a single conversational turn
# ----------------------------------------------------------------------
def _context_summary(state: dict, snapshot: str) -> str:
    parts = [p for p in (state["summary"].strip(), snapshot.strip()) if p]
    return "\n\n".join(parts)


def _send(state, system, summarizer, snapshot, user_msg, *, cadence):
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
        return llm.converse(system, summary, state["transcript"], user_msg, cadence=cadence)
    finally:
        _stop_thinking()


def handle_message(state, system, summarizer, user_msg):
    mem = engine.load_memory()
    snapshot = memory_snapshot(mem)
    try:
        reply = _send(state, system, summarizer, snapshot, user_msg, cadence=CADENCE_WIRING)
    except Exception as exc:
        print(f"\n(Cadence hit a problem: {exc}\n Try again, or /new if it persists.)")
        return
    if reply is None:
        return

    req = parse_cadence_request(reply)
    display = strip_cadence_request(reply) if req else reply
    print(f"\nCadence: {display}")

    chat_state.append_turn(state, "user", user_msg)
    chat_state.append_turn(state, "cadence", display or reply)

    if req:
        verb, path = req
        _job_then_discuss(state, system, summarizer, verb, path, gate=True)


def _job_then_discuss(state, system, summarizer, verb, path, *, gate):
    _, _, _, _, digest_instruction = JOBS[verb]
    try:
        digest = run_job(verb, path, gate=gate)
    except Exception as exc:
        print(f"   (couldn't {verb} the audio: {exc})")
        return
    if digest is None:
        feedback = (f"[note] The user declined to write the {verb} artifact from {path!r}. "
                    "Acknowledge and keep talking.")
    else:
        feedback = (f"[audio {verb} done from {path!r}]\n{digest}\n{digest_instruction}")

    mem = engine.load_memory()
    snapshot = memory_snapshot(mem)
    try:
        reply = _send(state, system, summarizer, snapshot, feedback, cadence=None)
    except Exception as exc:
        print(f"\n(Cadence couldn't report the result: {exc})")
        return
    if reply:
        print(f"\nCadence: {reply}")
        chat_state.append_turn(state, "user", feedback)
        chat_state.append_turn(state, "cadence", reply)


# ----------------------------------------------------------------------
# Slash commands
# ----------------------------------------------------------------------
HELP = """Commands:
  /narrate <path>  per-scene tts -> narration.wav + transcript (project dir or script.json)
  /mix <path>      source bed + place accent -> master.wav + audio manifest
  /summary         distill the session so far, then show what Cadence remembers
  /new             distill + start a fresh thread (keeps what Cadence knows about you)
  /help            show this
  /exit            save (distill) and quit
Anything else is just conversation."""


def handle_command(state, system, summarizer, distiller, raw) -> bool:
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
        print("\n[What Cadence remembers about you]\n" + body)
        if not ok:
            print("(I couldn't fully update just now — kept what I had; your chat is "
                  "safe and I'll fold it in next launch.)")
    elif cmd == "/new":
        distill_and_save(state, distiller,
                         status="💾 Saving what matters before clearing the thread…")
        state["transcript"] = []
        print("Fresh thread. I've folded this chat into what I remember about you "
              "— your voice, your moods, your loudness posture; the back-and-forth is "
              "cleared.")
    elif cmd in ("/narrate", "/mix"):
        verb = cmd[1:]
        if not arg:
            print(f"Usage: /{verb} <project_dir or script.json>")
        else:
            # Explicit command: typing it IS approval for the WRITE, but Cadence still
            # previews first. (The [y/N] gate lives on the model-initiated tool + marker.)
            _job_then_discuss(state, system, summarizer, verb, arg, gate=False)
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
    print("Talk to Cadence.  /help for commands, /exit to leave.")
    if state["summary"].strip():
        print("(Cadence remembers what matters about your work from before — pick up "
              "wherever you like.)")
    print("=" * 64)

    while True:
        try:
            user = input("\nYou: ").strip()
        except EOFError:
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
