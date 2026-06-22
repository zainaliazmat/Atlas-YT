"""Vera is in the registry and her tools auto-generate — with ZERO orchestrator edits.

The extensibility guarantee, exercised on the REAL new agent (not a mock): adding one
AgentEntry + one adapter is all it took for `reference_analyst_build_rubric` and
`ask_reference_analyst` to appear.
"""
import registry
import tools
from progress import list_progress


def test_vera_is_registered_and_resolvable():
    e = registry.get_entry("reference_analyst")
    assert e is not None
    assert e.display == "Vera"
    assert registry.get_entry("Vera").name == "reference_analyst"
    assert e.role == "Reference Analyst (standards)"
    assert e.persona is True
    assert e.stub is False  # she's a real, built specialist


def test_veras_tools_auto_generate():
    adapters = registry.build_adapters()
    prog, _ = list_progress()
    _server, allowed = tools.build_server(adapters, prog)
    assert "mcp__atlas__reference_analyst_build_rubric" in allowed
    assert "mcp__atlas__ask_reference_analyst" in allowed


def test_build_rubric_jobspec_shape():
    e = registry.get_entry("reference_analyst")
    job = next(j for j in e.jobs if j.name == "build_rubric")
    assert job.tool == "reference_analyst_build_rubric"
    assert set(job.params) == {"videos", "ceo_prefs"}
    assert job.timeout >= 600  # generous for FFmpeg analysis + optional vision pass


def test_roster_lists_vera_with_her_tools():
    r = registry.roster()
    assert "Vera" in r
    assert "reference_analyst_build_rubric" in r
    # she's additive: the pipeline-stage agents are all still present and unchanged
    for display in ("Sage", "Marlow", "Iris", "Magpie", "Cadence", "Mason", "Viral Scout"):
        assert display in r
