"""The structural write boundary for Atlas's new agency tools.

Atlas (the showrunner) now reaches outside the meeting: it reads its own and its
agents' code, writes soft-tier persona/playbook text and per-project artifacts,
escalates to the CEO, and keeps a journal. None of that may become a path to
self-modify the spine. This module is where that privilege asymmetry is made
PHYSICAL — the same lesson as eval/loop.py's WriteBoundaryError, generalized to a
tier classifier every write must pass through.

Tiers (classify):
  SOFT      persona/prompt/playbook .md, anything under a soul/ dir   -> ALLOW
  PROJECT   projects/<slug>/...   (a video's accumulating workspace)  -> ALLOW
  INCUBATOR agents-incubator/...  (where new agents are grown)        -> ALLOW
  CORE      orchestrator/registry/tools/llm/boundary + rubric/contracts,
            and ANY other in-repo path by default                     -> REFUSE
  SECRETS   .env, key files, or content carrying a key pattern        -> REFUSE
  OUTSIDE   anything outside the repo root                            -> REFUSE

The default for an unrecognized in-repo path is CORE (propose-only): the boundary
fails CLOSED. New writable surfaces are opted IN, never assumed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

# The same exception type the eval loop's boundary raises — one boundary error for
# the whole studio, not a parallel hierarchy.
from eval.loop import WriteBoundaryError

ATLAS_DIR = Path(__file__).resolve().parent      # .../atlas
REPO_DIR = ATLAS_DIR.parent                       # repo root

# Where Atlas talks UP to the CEO. Module-global so tests can redirect it; an env
# override (ATLAS_CEO_DIR) lets a separate process (e.g. the web app under a smoke
# test) point the whole CEO surface at a sandbox without code changes.
CEO_DIR = Path(os.environ.get("ATLAS_CEO_DIR", str(ATLAS_DIR / "ceo")))


class ReadBoundaryError(PermissionError):
    """Raised when read_repo is pointed outside the repo root."""


# ----------------------------------------------------------------------
# Tiers
# ----------------------------------------------------------------------
SOFT = "SOFT"
PROJECT = "PROJECT"
INCUBATOR = "INCUBATOR"
CORE = "CORE"
SECRETS = "SECRETS"
OUTSIDE = "OUTSIDE"

_ALLOW_TIERS = frozenset({SOFT, PROJECT, INCUBATOR})

# CORE files (relative to ATLAS_DIR) — the spine Atlas may propose to but not edit.
_CORE_FILES = {"orchestrator.py", "registry.py", "tools.py", "llm.py", "boundary.py"}
# CORE dirs (relative to ATLAS_DIR) — the frozen success criterion + contracts.
_CORE_DIRS = {"rubric", "contracts"}

# Soft-tier markers: persona/voice/playbook/prompt text an agent "runs on".
_SOFT_TOKENS = ("SOUL", "STYLE", "SKILL", "PERSONA", "PLAYBOOK", "PROMPT",
                "COACH", "ADDENDUM")

# Secret-bearing FILENAMES (suffixes + exact names).
_SECRET_SUFFIXES = {".pem", ".key", ".pfx", ".p12"}
_SECRET_NAMES = {"id_rsa", "id_dsa", "credentials", "secrets"}

# Secret-bearing CONTENT — common live-key shapes. A match anywhere in the content
# trips SECRETS regardless of how innocent the path looks.
_SECRET_CONTENT = re.compile(
    r"""(
        sk-[A-Za-z0-9\-]{16,}                     # OpenAI / Anthropic style keys
      | AKIA[0-9A-Z]{16}                          # AWS access key id
      | -----BEGIN[ A-Z]*PRIVATE KEY-----         # PEM private key block
      | (?i:(api[_-]?key|secret|token|password)\s*[:=]\s*['"]?[A-Za-z0-9_\-]{16,})
    )""",
    re.VERBOSE,
)


def _is_secret(rp: Path, content: str | None) -> bool:
    name = rp.name.lower()
    if name == ".env" or name.startswith(".env"):
        return True
    if rp.suffix.lower() in _SECRET_SUFFIXES or name in _SECRET_NAMES:
        return True
    if content is not None and _SECRET_CONTENT.search(content):
        return True
    return False


def _is_core(rp: Path) -> bool:
    # spine files directly under atlas/
    if rp.parent == ATLAS_DIR and rp.name in _CORE_FILES:
        return True
    # the frozen rubric / contracts trees
    for d in _CORE_DIRS:
        root = ATLAS_DIR / d
        if root == rp or root in rp.parents:
            return True
    return False


def _under(rp: Path, *parts: str) -> bool:
    """Is `rp` inside a `<part>/<slug>/...` directory anywhere in the repo?"""
    segs = rp.parts
    for i, seg in enumerate(segs[:-1]):           # exclude the file itself
        if seg in parts:
            return True
    return False


def _is_soft(rp: Path) -> bool:
    if rp.suffix.lower() != ".md":
        return False
    if any(tok in rp.stem.upper() for tok in _SOFT_TOKENS):
        return True
    return any(part.lower() == "soul" for part in rp.parts)


def classify(path: str | Path, content: str | None = None) -> str:
    """Return the single tier for `path` (+ optional `content`). Fails CLOSED:
    an in-repo path matching no allow rule is CORE (propose-only)."""
    rp = Path(path).resolve()

    # outside the repo entirely — no write reaches here
    if REPO_DIR != rp and REPO_DIR not in rp.parents:
        return OUTSIDE
    # secrets win over everything (a .env inside projects/ is still a secret)
    if _is_secret(rp, content):
        return SECRETS
    # the spine: refuse before any allow rule can match it
    if _is_core(rp):
        return CORE
    if _under(rp, "projects"):
        return PROJECT
    if _under(rp, "agents-incubator"):
        return INCUBATOR
    if _is_soft(rp):
        return SOFT
    return CORE      # default: propose-only


# ----------------------------------------------------------------------
# Guarded write — the one door every write_file goes through.
# ----------------------------------------------------------------------
def guarded_write(path: str | Path, content: str) -> Path:
    """Write `content` to `path` ONLY if its tier is allowed (soft/project/
    incubator). Otherwise raise WriteBoundaryError WITHOUT writing anything."""
    tier = classify(path, content)
    if tier not in _ALLOW_TIERS:
        raise WriteBoundaryError(
            f"refused: {path} is {tier} — Atlas may not write it (propose-only)")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p.resolve()


def guarded_delete(path: str | Path) -> Path:
    """Permanently delete `path` (a file or whole directory tree) ONLY if it is a
    PROJECT-tier workspace under projects/. Every other tier — soft persona text, the
    incubator, the core spine, secrets, the projects/ root itself, anything outside the
    repo — is REFUSED without touching the filesystem.

    Deletion is the one IRREVERSIBLE privilege, so the door is deliberately narrower
    than `guarded_write`: a video may delete its own workspace and nothing else. Like
    the write door, it fails CLOSED — an unrecognized path classifies CORE and is
    refused."""
    rp = Path(path).resolve()
    tier = classify(rp)
    if tier != PROJECT:
        raise WriteBoundaryError(
            f"refused: {path} is {tier} — delete is only allowed for a project "
            "workspace under projects/<slug>/.")
    if not rp.exists():
        raise FileNotFoundError(f"nothing to delete at {path}")
    if rp.is_dir():
        shutil.rmtree(rp)
    else:
        rp.unlink()
    return rp


def can_write_core() -> bool:
    """Self-test for tests/reports: Atlas can NEVER write a CORE file. The boundary
    refuses the spine, so this is permanently False."""
    try:
        guarded_write(ATLAS_DIR / "orchestrator.py", "# tamper")
    except WriteBoundaryError:
        return False
    return True   # only reached if the boundary failed open (it must not)


# ----------------------------------------------------------------------
# Read — jailed to the repo root, read-only.
# ----------------------------------------------------------------------
def read_repo(path: str | Path) -> str:
    """Return the text of a repo file. Reject any path that resolves outside the
    repo root (traversal-safe via resolve())."""
    rp = Path(path).resolve()
    if REPO_DIR != rp and REPO_DIR not in rp.parents:
        raise ReadBoundaryError(f"refused: {path} is outside the repo root")
    if not rp.is_file():
        raise FileNotFoundError(f"no file at {path}")
    return rp.read_text()


# Directories never worth listing — build/cache noise that drowns the signal.
_LIST_IGNORE = {"__pycache__", ".git", "node_modules", ".venv", "venv",
                ".mypy_cache", ".pytest_cache", ".ruff_cache", ".DS_Store"}


def list_dir(path: str | Path, *, recursive: bool = False,
             max_entries: int = 1500) -> tuple[list[str], bool]:
    """List a directory INSIDE the repo (read-only, jailed — the read counterpart to
    read_repo, which only reads files). Returns (entries, truncated): a sorted list of
    repo-relative paths (directories end with '/') and whether the cap was hit.
    `recursive` walks the whole subtree; build/cache dirs in _LIST_IGNORE are always
    skipped. Rejects paths outside the repo root (traversal-safe via resolve())."""
    rp = Path(path).resolve()
    if REPO_DIR != rp and REPO_DIR not in rp.parents:
        raise ReadBoundaryError(f"refused: {path} is outside the repo root")
    if not rp.exists():
        raise FileNotFoundError(f"no directory at {path}")
    if rp.is_file():
        raise NotADirectoryError(f"{path} is a file, not a directory — use read_repo")

    paths = rp.rglob("*") if recursive else rp.iterdir()
    rels: list[str] = []
    for p in paths:
        if any(part in _LIST_IGNORE for part in p.relative_to(rp).parts):
            continue
        rels.append(p.relative_to(REPO_DIR).as_posix() + ("/" if p.is_dir() else ""))
    rels.sort()
    truncated = len(rels) > max_entries
    return rels[:max_entries], truncated


# ----------------------------------------------------------------------
# CEO channel — escalate up, journal, kill-switch.
# ----------------------------------------------------------------------
REQUEST_KINDS = ("api_key", "asset", "approval", "info", "budget")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(filename: str, record: dict) -> Path:
    path = CEO_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    return path


def request_from_ceo(kind: str, what: str, why: str, how_to_provide: str) -> dict:
    """Append a structured ask to ceo/requests.jsonl and return a CEO-facing
    message. Atlas NEVER blocks hard on the answer: if the CEO can't provide it,
    Atlas finds a legal alternative rather than stall (see SOUL / the charter)."""
    if kind not in REQUEST_KINDS:
        raise ValueError(f"kind must be one of {REQUEST_KINDS}, got {kind!r}")
    record = {"ts": _now(), "kind": kind, "what": what, "why": why,
              "how_to_provide": how_to_provide}
    _append_jsonl("requests.jsonl", record)
    message = (
        f"📨 Request to CEO [{kind}]: {what}\n"
        f"   Why: {why}\n"
        f"   How to provide: {how_to_provide}\n"
        f"   (Logged to ceo/requests.jsonl.) If you can't provide this, say so — "
        f"I'll find a legal alternative rather than block the work.")
    return {"record": record, "message": message}


def ceo_log(entry: str) -> dict:
    """Append a single line to the append-only ceo/journal.jsonl."""
    record = {"ts": _now(), "entry": entry}
    _append_jsonl("journal.jsonl", record)
    return {"record": record}


def kill_switch_active() -> bool:
    """True iff a ceo/STOP file exists — the CEO's hard halt. Atlas checks this
    before acting and refuses (saying so) when it's set."""
    return (CEO_DIR / "STOP").exists()
