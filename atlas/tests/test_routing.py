"""Direct-address routing: `/ask <agent>` targets the correct agent (mock adapters,
no LLM, no network)."""
import chat
import registry


class _FakeAdapter:
    def __init__(self, entry):
        self.entry = entry
        self.asked = []

    def ask(self, question, context=""):
        self.asked.append(question)
        return f"{self.entry.name} answers"


class _FakeOrch:
    def __init__(self):
        self.adapters = {e.name: _FakeAdapter(e) for e in registry.REGISTRY}


def test_ask_routes_to_the_named_agent_only():
    orch = _FakeOrch()
    state = {"summary": "s", "transcript": []}
    chat.ask_agent(orch, state, "scout", "is faceless dead?")
    assert orch.adapters["scout"].asked == ["is faceless dead?"]
    assert orch.adapters["sage"].asked == []          # the other agent untouched
    assert state["transcript"]                         # the exchange was recorded


def test_ask_resolves_display_name():
    orch = _FakeOrch()
    state = {"summary": "", "transcript": []}
    chat.ask_agent(orch, state, "Sage", "is ozempic safe?")
    assert orch.adapters["sage"].asked == ["is ozempic safe?"]


def test_ask_unknown_agent_is_graceful(capsys):
    orch = _FakeOrch()
    state = {"summary": "", "transcript": []}
    chat.ask_agent(orch, state, "nobody", "hi")        # must not raise
    out = capsys.readouterr().out
    assert "don't have an agent" in out
    assert state["transcript"] == []                   # nothing recorded
