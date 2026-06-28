"""The uniform adapter interface every managed agent is wrapped behind.

An adapter gives Atlas TWO capabilities over a sibling agent, without touching that
sibling's code:

- a JOB: `run_job(job_name, progress, **params) -> dict` — calls the sibling's
  ENGINE in-process (via the isolation loader) and returns a compact, LLM-friendly
  digest. Emits deterministic progress lines as it runs.
- a PERSONA: `ask(question, context) -> str` — loads the sibling's SOUL + STYLE and
  produces a single-turn, in-character reply through Atlas's OWN llm seam. (We use
  Atlas's seam, never the sibling's `converse`, so there's no second SDK loop and no
  lazy-import surprise.)

The sibling engine is loaded LAZILY and cached once (loader handles the caching),
so importing two siblings with colliding module names never cross-wires and a
sibling's import side effects run exactly once.
"""
from __future__ import annotations

import pathlib
from abc import ABC, abstractmethod

import llm
from adapters.loader import load_engine


class Adapter(ABC):
    """Base adapter. Subclasses set `module_name` and implement `run_job`."""

    #: the engine module to import from the sibling's project dir (e.g. "agent").
    module_name: str = ""

    def __init__(self, entry):
        self.entry = entry            # the registry AgentEntry
        self._engine = None

    # ---- engine access (lazy, loaded-once via the loader cache) ----
    def engine(self):
        if self._engine is None:
            self._engine = load_engine(self.entry.project_dir, self.module_name)
        return self._engine

    # ---- project workspace (explicit slug; the manifest module owns the path) ----
    @staticmethod
    def resolve_pdir(slug: str):
        """The project workspace for `slug` (minted by start_project), or None if the
        slug is empty / no such project. Production jobs read their upstream artifacts
        from here and write their output here, so one slug accumulates one video."""
        import projects
        return projects.project_dir(slug)

    # ---- JOB capability (agent-specific) ----
    @abstractmethod
    def run_job(self, job_name: str, progress, **params) -> dict:
        """Run a delegated job. Return {"ok": bool, "text": <digest>, ...}.

        May raise — the tool layer wraps this in try/except + a timeout so a failure
        is reported as a structured tool result and never crashes the meeting.
        """

    # ---- PERSONA capability (shared) ----
    def ask(self, question: str, context: str = "") -> str:
        """Single-turn, in-character reply as this agent, via Atlas's llm seam."""
        system = self._persona_system()
        ctx = f"\n\n[Context from the meeting]\n{context.strip()}" if context.strip() else ""
        user = (
            f"The CEO's chief-of-staff is relaying a question to you in a team "
            f"meeting.{ctx}\n\n[The question]\n{question.strip()}\n\n"
            "Answer briefly and in character, in your own voice. This is "
            "conversation, NOT a job — do NOT produce your structured output format; "
            "just give your honest take."
        )
        return llm.chat(system, user)

    def _persona_system(self) -> str:
        """Build the persona system prompt from the sibling's SOUL (+ STYLE).

        Loads SOUL + STYLE only — NOT the examples/ (bounded tokens) and NOT SKILL.md
        (that's the engine's job contract, which would make the reply robotic).
        """
        soul_dir = pathlib.Path(self.entry.project_dir) / "soul"
        soul = self._read(soul_dir / "SOUL.md")
        style = self._read(soul_dir / "STYLE.md")
        parts = [soul.strip() or f"You are {self.entry.display}."]
        if style.strip():
            parts.append("# HOW YOU TALK (voice & style)\n\n" + style.strip())
        parts.append(
            "## Right now: a team meeting\n"
            "You're speaking with the CEO and the rest of the team, relayed by "
            "Atlas (the manager). Talk like a real person with your expertise — "
            "natural and brief. Do not emit your structured job output here.")
        return "\n\n".join(parts)

    @staticmethod
    def _read(path: pathlib.Path) -> str:
        try:
            return path.read_text()
        except OSError:
            return ""
