"""The governance seam the single chat UI renders — read the CEO surface, and let
the human fulfill asks + decide checkpoints INLINE. Pure and file-backed: it adds
NO orchestration. Everything routes through the existing seams:

  * the digest      = ceo.state (goal/state/milestones/budget) + the journal;
  * the request queue = boundary's ceo/requests.jsonl, with resolutions tracked in a
                        sibling ceo/request_resolutions.jsonl (append-only, indexed);
  * fulfillment     = a pasted key lands in the env file (value NEVER logged), a
                      dropped file is placed under ceo/provided_assets/, info is noted;
  * checkpoints     = approve/decline a pending ask (publish / spend / create-agent /
                      core-edit), journaled through boundary.ceo_log;
  * the kill switch = the ceo/STOP file; the budget meter = state['budget'].
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import boundary
from ceo import state as ceo_state

RESOLUTIONS_FILE = "request_resolutions.jsonl"
ASSET_DROP_DIR = "provided_assets"


def _ceo_dir() -> Path:
    return boundary.CEO_DIR


def env_file() -> Path:
    """The env file a pasted credential lands in. Overridable so a smoke test never
    writes the real repo .env."""
    return Path(os.environ.get("ATLAS_ENV_FILE", str(boundary.REPO_DIR / ".env")))


# ----------------------------------------------------------------------
# Request queue + resolutions
# ----------------------------------------------------------------------
def load_requests() -> list[dict]:
    """Every ask ever filed, each tagged with its stable line `index`."""
    p = _ceo_dir() / "requests.jsonl"
    if not p.exists():
        return []
    out = []
    for i, line in enumerate(p.read_text().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rec["index"] = i
        out.append(rec)
    return out


def load_resolutions() -> dict:
    """index -> resolution record for asks the CEO has acted on."""
    p = _ceo_dir() / RESOLUTIONS_FILE
    res = {}
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            res[r["index"]] = r
    return res


def pending_requests() -> list[dict]:
    """Asks the CEO hasn't resolved yet — the inbox the UI surfaces."""
    res = load_resolutions()
    return [r for r in load_requests() if r["index"] not in res]


def _record_resolution(index: int, decision: str, note: str = "") -> dict:
    rec = {"index": index, "decision": decision, "note": note, "ts": time.time()}
    p = _ceo_dir() / RESOLUTIONS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


# The four checkpoint kinds the CEO charter names, inferred from the ask.
def checkpoint_type(req: dict) -> str:
    kind = req.get("kind", "")
    what = (req.get("what", "") or "").lower()
    if kind == "budget" or "spend" in what or "budget" in what:
        return "spend"
    if "public" in what or "publish" in what:
        return "publish"
    if "agent" in what or "promote" in what:
        return "create-agent"
    if "registry" in what or "core" in what or "rubric" in what:
        return "core-edit"
    return kind or "approval"


def is_checkpoint(req: dict) -> bool:
    """An approve/decline checkpoint vs. a fulfillable ask (key/asset/info)."""
    return checkpoint_type(req) in ("publish", "spend", "create-agent", "core-edit",
                                    "approval")


# ----------------------------------------------------------------------
# Decisions + inline fulfillment
# ----------------------------------------------------------------------
def approve_request(index: int, note: str = "") -> dict:
    boundary.ceo_log(f"APPROVED request #{index}" + (f": {note}" if note else ""))
    return _record_resolution(index, "approved", note)


def decline_request(index: int, note: str = "") -> dict:
    boundary.ceo_log(f"DECLINED request #{index}" + (f": {note}" if note else ""))
    return _record_resolution(index, "declined", note)


def suggested_env_var(req: dict) -> str | None:
    """Best-guess the env var name from the ask's how_to_provide (e.g. 'YT_API_KEY')."""
    text = " ".join(str(req.get(k, "")) for k in ("how_to_provide", "what"))
    m = re.search(r"\b([A-Z][A-Z0-9_]{3,})\b", text)
    return m.group(1) if m else None


def provide_api_key(index: int, env_var: str, value: str) -> dict:
    """A pasted credential lands in the env file (env ONLY — never code) and the live
    process env. The secret VALUE is never written to a log or journal."""
    env_var = (env_var or "").strip()
    if not env_var:
        raise ValueError("an env var name is required to place the key")
    ef = env_file()
    ef.parent.mkdir(parents=True, exist_ok=True)
    with ef.open("a") as fh:
        fh.write(f"\n{env_var}={value}\n")
    os.environ[env_var] = value                       # usable this process too
    boundary.ceo_log(f"FULFILLED request #{index}: set env var {env_var} "
                     "(value not logged)")
    return _record_resolution(index, "fulfilled", f"set {env_var}")


def provide_asset(index: int, filename: str, content: bytes) -> dict:
    """A dropped file is placed under ceo/provided_assets/ and the ask resolved."""
    safe = Path(filename or "asset").name              # no traversal
    dest = _ceo_dir() / ASSET_DROP_DIR / safe
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    boundary.ceo_log(f"FULFILLED request #{index}: placed asset {safe} at {dest}")
    return _record_resolution(index, "fulfilled", f"placed {safe}")


def provide_info(index: int, text: str) -> dict:
    boundary.ceo_log(f"FULFILLED request #{index}: info provided — {(text or '')[:80]}")
    return _record_resolution(index, "fulfilled", text or "")


# ----------------------------------------------------------------------
# Kill switch + budget meter
# ----------------------------------------------------------------------
def kill_switch_active() -> bool:
    return boundary.kill_switch_active()


def set_kill_switch(on: bool) -> bool:
    """Engage (write ceo/STOP) or clear the kill switch. Returns the new state."""
    stop = _ceo_dir() / "STOP"
    if on:
        stop.parent.mkdir(parents=True, exist_ok=True)
        stop.write_text("halt")
    else:
        try:
            stop.unlink()
        except FileNotFoundError:
            pass
    return kill_switch_active()


def budget_meter() -> dict:
    b = (ceo_state.load().get("budget") or {})
    ceiling = float(b.get("ceiling_usd", 0) or 0)
    spent = float(b.get("spent_usd", 0) or 0)
    return {"ceiling": ceiling, "spent": round(spent, 2),
            "remaining": round(max(0.0, ceiling - spent), 2)}


# ----------------------------------------------------------------------
# The digest panel
# ----------------------------------------------------------------------
def recent_journal(n: int = 6) -> list[str]:
    p = _ceo_dir() / "journal.jsonl"
    if not p.exists():
        return []
    entries = []
    for line in p.read_text().splitlines():
        try:
            entries.append(json.loads(line).get("entry", ""))
        except json.JSONDecodeError:
            continue
    return entries[-n:]


def digest_panel(*, n_journal: int = 6) -> str:
    """A single markdown read-out: state + milestones + latest journal + budget meter
    + kill-switch status + the count/heads of pending asks."""
    st = ceo_state.load()
    bm = budget_meter()
    pend = pending_requests()
    lines = ["# 🧭 CEO Digest", "", "## Business state", ceo_state.summary_text(st), ""]

    lines.append("## Milestones")
    for m in st.get("milestones", []):
        mark = "✅" if m.get("status") == "done" else "▫️"
        lines.append(f"- {mark} {m.get('name','?')} — _{m.get('status','pending')}_")
    lines.append("")

    lines.append("## Latest journal")
    jr = recent_journal(n_journal)
    lines += ([f"- {e}" for e in jr] or ["- _(nothing logged yet)_"])
    lines.append("")

    bar_len = 20
    filled = 0 if bm["ceiling"] <= 0 else min(bar_len, int(bar_len * bm["spent"] / bm["ceiling"]))
    bar = "█" * filled + "░" * (bar_len - filled)
    lines.append("## Budget meter")
    lines.append(f"`{bar}`  ${bm['spent']} / ${bm['ceiling']} spent · "
                 f"${bm['remaining']} remaining")
    lines.append("")

    ks = kill_switch_active()
    lines.append(f"## Kill switch: {'🛑 ENGAGED — Atlas is halted' if ks else '🟢 clear'}")
    lines.append("")

    lines.append(f"## Pending asks: {len(pend)}")
    for r in pend:
        lines.append(f"- **[{checkpoint_type(r)}]** {r.get('what','(no detail)')}")
    if not pend:
        lines.append("- _(none — you're all caught up)_")
    return "\n".join(lines)
