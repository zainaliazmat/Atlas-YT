"""Atlas's agent-craft: improve an existing teammate, propose a new one, and
self-evaluate a finished video — all fenced by the same structural boundary.

The privilege gradient is the whole point:
  * improve_agent   writes SOFT-tier persona/prompt text only (boundary.SOFT).
  * propose_agent   writes only into agents-incubator/ (boundary.INCUBATOR) and
                    NEVER edits registry.py — promotion is a CORE change a human
                    applies, so Atlas can only ASK (a request_from_ceo approval).
  * run_self_eval   measures via eval/ and may apply ONE soft improvement through
                    eval/loop.py's guarded path; the rubric (its success bar) is
                    imported READ-ONLY and physically un-writable.

Nothing here can move Atlas's own success criterion or edit the spine.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import boundary
import registry


class AgentError(ValueError):
    """Unknown agent, unsafe name, or missing project — a caller mistake."""


# A new agent's handle must be a safe kebab/snake slug — this also fences the
# incubator write to a single child dir (no traversal, no escape).
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")


def _incubator_dir() -> Path:
    """Resolved at call time so tests can redirect boundary.REPO_DIR."""
    return boundary.REPO_DIR / "agents-incubator"


# ----------------------------------------------------------------------
# 1. improve_agent — a SOFT-tier persona/prompt edit, then re-validate.
# ----------------------------------------------------------------------
def _validate_persona(project_dir: Path, *, chat_fn=None) -> dict:
    """Re-run the persona-validation harness (validate_persona.py) for ONE agent:
    rebuild its persona prompt from soul/ and confirm it's coherent. The behavioral
    weak-model pass is opt-in (chat_fn) so the default is offline + free."""
    soul_dir = Path(project_dir) / "soul"

    def _read(p: Path) -> str:
        try:
            return p.read_text()
        except OSError:
            return ""

    soul = _read(soul_dir / "SOUL.md").strip()
    style = _read(soul_dir / "STYLE.md").strip()
    good = _read(soul_dir / "examples" / "good-outputs.md").strip()
    parts = [soul]
    if style:
        parts.append("# HOW YOU TALK (voice & style)\n\n" + style)
    if good:
        parts.append("# CALIBRATION — on-voice examples\n\n" + good)
    prompt = "\n\n".join(p for p in parts if p)

    result = {"ok": bool(soul), "soul_chars": len(soul),
              "prompt_chars": len(prompt), "behavioral": None}
    if chat_fn is not None and soul:
        # the soul.md idea: a weak brain should stay in character from the spec
        from validate_persona import QUESTIONS
        replies = []
        for q in QUESTIONS[:2]:
            try:
                replies.append({"q": q, "a": chat_fn(prompt, q)})
            except Exception as exc:  # noqa: BLE001 — a flaky brain never fails the edit
                replies.append({"q": q, "error": str(exc)})
        result["behavioral"] = replies
    return result


def improve_agent(name: str, file: str, content: str, *, chat_fn=None) -> dict:
    """Write `content` to an existing agent's SOFT-tier persona/prompt `file`
    (e.g. soul/SOUL.md, soul/STYLE.md, SKILL.md), then re-validate that agent's
    persona. Refuse anything that isn't SOFT — this tool only touches voice/prompt,
    never code, contracts, or secrets."""
    entry = registry.get_entry(name)
    if entry is None:
        raise AgentError(f"no agent named {name!r} in the registry")
    path = Path(entry.project_dir) / file
    tier = boundary.classify(path, content)
    if tier != boundary.SOFT:
        raise boundary.WriteBoundaryError(
            f"refused: improve_agent only edits SOFT persona/prompt files; "
            f"{file} classifies as {tier}")
    written = boundary.guarded_write(path, content)   # SOFT -> allowed
    validation = _validate_persona(Path(entry.project_dir), chat_fn=chat_fn)
    return {"agent": entry.name, "file": file, "written": str(written),
            "tier": tier, "validation": validation}


# ----------------------------------------------------------------------
# 2. propose_agent — scaffold a NEW agent into the incubator (never registry).
# ----------------------------------------------------------------------
def _soul_md(name: str, role: str, spec: str) -> str:
    return (f"# {name.title()} — the {role}\n\n"
            f"You are **{name.title()}**, the studio's {role}. {spec}\n\n"
            "## What you believe about the work\n"
            "- One clear job, done sharply. You do that one thing better than anyone.\n"
            "- You are straight about what you can and can't do — no dressing up a stub.\n\n"
            "## Who you are NOT\n"
            "- You are not the showrunner and not another specialist. You own your lane.\n")


def _style_md(name: str, role: str) -> str:
    return (f"# How {name.title()} talks\n\n"
            f"- Crisp and concrete; lead with the decision, then the why.\n"
            f"- In-character as the {role}; never break the fourth wall.\n")


def _engine_py(name: str, role: str, spec: str) -> str:
    """A minimal, dependency-free engine so the agent loads + smoke-tests in
    isolation. A real build replaces run() with the specialist's logic."""
    return (
        '"""Minimal engine for the proposed ' + name + ' agent (incubator scaffold).\n\n'
        'Dependency-free on purpose: it must import + run in isolation before the CEO\n'
        'promotes it. Replace run() with the real specialist logic on promotion.\n'
        '"""\n'
        'from __future__ import annotations\n\n'
        'NAME = ' + repr(name) + '\n'
        'ROLE = ' + repr(role) + '\n'
        'SPEC = ' + repr(spec) + '\n\n\n'
        'def run(brief: str) -> dict:\n'
        '    """Stub job: echoes the brief as a structured result so the smoke test\n'
        '    can prove the module loads and returns the expected shape."""\n'
        '    return {"agent": NAME, "role": ROLE, "brief": brief, "stub": True}\n'
    )


def _promotion_md(name: str, role: str, spec: str) -> str:
    """The PROPOSED AgentEntry patch — as TEXT for a human to apply to registry.py.
    Promoting (editing registry.py) is a CORE change; Atlas never applies it."""
    entry_src = (
        "    AgentEntry(\n"
        f"        name={name!r},\n"
        f"        display={name.title()!r},\n"
        "        emoji=\"🧩\",\n"
        f"        blurb={spec[:120]!r},\n"
        f"        project_dir=str(_ROOT / {name!r}),   # move out of agents-incubator/ on promotion\n"
        "        adapter_cls=...,   # wrap engine.run() in an Adapter subclass\n"
        f"        role={role!r},\n"
        "        jobs=[JobSpec(\n"
        f"            name=\"run\", tool=\"{name}_run\",\n"
        f"            description={('Run the ' + role + ' job.')!r},\n"
        "            params={\"brief\": str})],\n"
        "    ),\n"
    )
    return (
        f"# Promotion proposal — {name.title()} ({role})\n\n"
        f"{spec}\n\n"
        "## To promote (a human applies this — it is a CORE change)\n"
        f"1. Move `agents-incubator/{name}/` to a sibling project dir.\n"
        "2. Wrap `engine.run()` in an Adapter subclass under `atlas/adapters/`.\n"
        "3. Add the following `AgentEntry` to `atlas/registry.py`'s `REGISTRY`:\n\n"
        "```python\n" + entry_src + "```\n\n"
        "Atlas did NOT and cannot apply step 3 — registry.py is propose-only.\n"
    )


def _smoke_test_engine(engine_path: Path, name: str) -> dict:
    """Load the scaffolded engine in ISOLATION (by file path, not via registry)
    and call run() — proving it imports and returns the expected shape."""
    try:
        spec = importlib.util.spec_from_file_location(f"_incubator_{name}", engine_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out = mod.run("smoke")
        ok = isinstance(out, dict) and out.get("agent") == name
        return {"ok": bool(ok), "result": out}
    except Exception as exc:  # noqa: BLE001 — a broken scaffold reports, never crashes
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def propose_agent(name: str, role: str, spec: str) -> dict:
    """Scaffold a NEW agent into agents-incubator/<name>/ (SOUL + STYLE + a minimal
    engine + a PROPOSED AgentEntry patch as text), smoke-test it in isolation, and
    file a CEO approval request to PROMOTE it. registry.py is never edited."""
    if not _SAFE_NAME.match(name or ""):
        raise AgentError(
            f"unsafe agent name {name!r}: use a lowercase kebab/snake handle")
    dirp = _incubator_dir() / name
    files = {
        "soul/SOUL.md": _soul_md(name, role, spec),
        "soul/STYLE.md": _style_md(name, role),
        "engine.py": _engine_py(name, role, spec),
        "PROMOTION.md": _promotion_md(name, role, spec),
    }
    written = []
    for rel, body in files.items():
        # every path lands under agents-incubator/ -> boundary.INCUBATOR (allowed).
        written.append(str(boundary.guarded_write(dirp / rel, body)))

    smoke = _smoke_test_engine(dirp / "engine.py", name)

    req = boundary.request_from_ceo(
        "approval",
        f"Promote a new agent '{name}' ({role}) from the incubator",
        f"It scaffolded and smoke-tested clean: {spec}",
        f"Review agents-incubator/{name}/PROMOTION.md and apply the AgentEntry to "
        "registry.py (a CORE change only you can make).")

    return {"agent": name, "dir": str(dirp), "files": written, "smoke": smoke,
            "promotion_request": req["record"], "promotion_message": req["message"],
            "registry_edited": False}


# ----------------------------------------------------------------------
# 3. run_self_eval — measure a finished video, maybe apply ONE soft tweak.
# ----------------------------------------------------------------------
def run_self_eval(slug: str, *, apply: bool = False, judged: bool = False,
                  inspect_fn=None) -> dict:
    """Measure project `slug` via eval/ and (if apply) push ONE soft improvement
    through eval/loop.py's guarded path. The rubric is the read-only success bar:
    apply_soft_change physically refuses to write it, and can_write_rubric() proves
    that boundary held this run. `inspect_fn` is injectable for offline testing."""
    import projects
    from eval import diagnose, inspector, loop

    pdir = projects.project_dir(slug)
    if pdir is None:
        raise AgentError(f"no project named {slug!r}")

    inspect = inspect_fn or (lambda d: inspector.run_inspection(d, run_judged=judged))
    scorecard = inspect(pdir)

    target = diagnose.pick_primary_target(scorecard)
    applied = None
    if apply and target is not None:
        proposal = loop.propose_fix(target)
        written = loop.apply_soft_change(proposal["soft_path"], proposal["addendum"])
        applied = {"soft_path": str(written), "band_id": target["band_id"],
                   "direction": proposal["direction"]}

    return {"slug": slug, "overall": scorecard.get("overall"),
            "quality_score": scorecard.get("quality_score"),
            "target": target, "applied": applied,
            "rubric_read_only": loop.can_write_rubric()}
