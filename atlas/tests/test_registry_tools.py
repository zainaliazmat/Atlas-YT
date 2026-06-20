"""The registry resolves agents and GENERATES the right tools — and a brand-new
(mock) agent surfaces its tools with ZERO orchestrator changes."""
import registry
import tools
from progress import list_progress


def test_real_registry_generates_expected_tools():
    adapters = registry.build_adapters()
    prog, _ = list_progress()
    _server, allowed = tools.build_server(adapters, prog)
    assert "mcp__atlas__scout_find_topics" in allowed
    assert "mcp__atlas__sage_research" in allowed
    assert "mcp__atlas__ask_scout" in allowed
    assert "mcp__atlas__ask_sage" in allowed


def test_get_entry_resolves_by_handle_and_display():
    assert registry.get_entry("scout").name == "scout"
    assert registry.get_entry("Sage").name == "sage"
    assert registry.get_entry("Viral Scout").name == "scout"
    assert registry.get_entry("nobody") is None


def test_roster_lists_every_agent():
    r = registry.roster()
    for e in registry.REGISTRY:
        assert e.display in r


# --- THE EXTENSIBILITY GUARANTEE: a future agent needs only a registry entry -----
class _MockAdapter:
    """Stands in for a real adapter — no engine, no network."""
    def __init__(self, entry):
        self.entry = entry
        self.progress = None

    def run_job(self, job_name, progress, **params):
        return {"ok": True, "text": "mock job ran"}

    def ask(self, question, context=""):
        return "mock persona reply"


def test_adding_a_mock_agent_surfaces_its_tool_with_no_orchestrator_change():
    mock_entry = registry.AgentEntry(
        name="mockzilla", display="Mockzilla", emoji="🤖",
        blurb="A pretend agent that does pretend jobs.",
        project_dir="/does/not/matter", adapter_cls=_MockAdapter,
        jobs=[registry.JobSpec(name="do_thing", tool="mockzilla_do_thing",
                               description="Do the pretend thing.",
                               params={"input": str})],
        persona=True,
    )
    # Build the server from a dict that includes the new entry — exactly what the
    # orchestrator does, with NO orchestrator edits.
    adapters = {"mockzilla": _MockAdapter(mock_entry)}
    prog, _ = list_progress()
    _server, allowed = tools.build_server(adapters, prog)
    assert "mcp__atlas__mockzilla_do_thing" in allowed
    assert "mcp__atlas__ask_mockzilla" in allowed
