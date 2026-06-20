"""Import a sibling agent's engine IN-PROCESS, with its module graph isolated.

Atlas depends on two sibling projects (youtube-topic-agent = Scout,
topic-researcher = Sage). Both ship modules with the SAME bare names — `llm`,
`chat_state`, `search`, `youtube`, `compaction`, `chat`, `run`. A naive
`sys.path.insert(...)` + `import` would let whichever sibling loaded FIRST win
`sys.modules['llm']`, silently cross-wiring the second sibling to the first one's
brain. This loader prevents that.

How it stays safe (review-hardened):
- **Isolation:** snapshot `sys.path` + the colliding `sys.modules` names, drop the
  local names so the engine re-imports ITS OWN siblings, load the engine under a
  unique key, then restore. The loaded engine keeps its sibling references in its
  module globals, so it remains self-contained after restore. (Proven by probe:
  `scout.llm.__file__ != sage.llm.__file__` in one process.)
- **Load-once cache:** each (dir, module) is loaded exactly once and cached, so we
  never re-run a sibling's import side effects (`load_dotenv`, reading SOUL/SKILL)
  or risk a re-collision on a second job.
- **Thread-safety:** `sys.path`/`sys.modules` are process-global, so the
  snapshot→mutate→restore window is a critical section guarded by a single lock.
  Two adapters loading different engines concurrently can never cross-wire.

The loader only controls imports AT LOAD TIME. It does NOT sandbox a sibling that
lazily imports a colliding LOCAL name at call time — but neither sibling does today
(both import their siblings eagerly at module top; the only lazy import is
`claude_agent_sdk`, a site-package that resolves identically regardless).
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import threading

# Module names BOTH siblings define locally and would therefore collide on. Kept as
# a superset (a name a sibling doesn't have is simply absent from the snapshot).
_COLLIDING = (
    "llm", "chat_state", "search", "youtube", "compaction", "chat", "run",
    "trends", "researcher", "factcheck", "agent",
)

_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], object] = {}


def load_engine(agent_dir: str | pathlib.Path, module_name: str):
    """Load `<module_name>.py` from `agent_dir` with sibling imports isolated.

    Returns the loaded module object (cached after the first call). Thread-safe.
    """
    agent_dir = str(pathlib.Path(agent_dir).resolve())
    key = (agent_dir, module_name)

    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached

        names = set(_COLLIDING) | {module_name}
        saved_path = list(sys.path)
        saved_mods = {n: sys.modules.get(n) for n in names}
        try:
            for n in names:
                sys.modules.pop(n, None)
            sys.path.insert(0, agent_dir)

            spec = importlib.util.spec_from_file_location(
                module_name, f"{agent_dir}/{module_name}.py")
            if spec is None or spec.loader is None:
                raise ImportError(
                    f"Could not locate {module_name}.py in {agent_dir}")
            mod = importlib.util.module_from_spec(spec)
            # Register under its own name so the engine's relative imports resolve
            # while it executes; the finally block restores any prior occupant.
            sys.modules[module_name] = mod
            spec.loader.exec_module(mod)
        finally:
            sys.path[:] = saved_path
            for n, m in saved_mods.items():
                if m is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = m

        _CACHE[key] = mod
        return mod
