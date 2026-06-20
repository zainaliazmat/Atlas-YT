"""The isolation loader: both sibling engines in ONE process, isolated + cached +
leaving sys.path / sys.modules exactly as it found them."""
import pathlib
import sys

import adapters.loader as loader
from adapters.loader import load_engine

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCOUT = ROOT / "youtube-topic-agent"
SAGE = ROOT / "topic-researcher"


def test_both_engines_get_distinct_llm_modules():
    a = load_engine(SCOUT, "agent")
    r = load_engine(SAGE, "researcher")
    assert callable(a.run) and callable(r.run)
    assert a.llm is not r.llm                       # no cross-wiring
    assert a.llm.__file__ != r.llm.__file__


def test_load_once_returns_the_cached_module():
    a1 = load_engine(SCOUT, "agent")
    a2 = load_engine(SCOUT, "agent")
    assert a1 is a2                                 # never re-runs import side effects


def test_syspath_and_sysmodules_restored_after_a_real_load():
    # Force a genuine (uncached) load to exercise the restore path.
    loader._CACHE.pop((str(SAGE.resolve()), "researcher"), None)
    before_path = list(sys.path)
    before_llm = sys.modules.get("llm")             # Atlas's own llm (or None)
    load_engine(SAGE, "researcher")
    assert sys.path == before_path                  # path restored exactly
    assert sys.modules.get("llm") is before_llm     # global llm not clobbered
